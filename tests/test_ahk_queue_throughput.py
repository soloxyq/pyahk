#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK 队列的**吞吐上限压力测试**(跑真实 AHK 代码,不是重写一份)。

和 test_ahk_hold_ledger.py 同样的做法:按函数名从 hold_server_extended.ahk
原文逐字抽取队列机制(EnqueueAction / ProcessQueue / ExecuteAction / 计数器),
拼上桩函数后交给 AutoHotkey.exe 执行 —— 被测的是实现本身,不会与实现漂移。

为什么需要这组测试:队列的吞吐上限由定时器周期和"每 tick 最多执行一个动作"共同决定,
**而定时器周期不等于你请求的数字** —— Windows 消息定时器粒度 ~15.6ms,`SetTimer` 会向上
凑整:请求 20ms 实际 31.6ms(31.7 动作/秒),请求 15ms 才是 15.8ms(63 动作/秒)。
所以上限必须**墙钟实测**,拿 1000/周期 去算会高估一倍。

而 EnqueueAction 无上界、生产侧(技能调度)也没有任何背压。一旦生产速率超过上限,
队列会无限增长,按键在"决策后好几秒"才打出去 ——
表现为"技能乱放/放的是几秒前该放的技能",而不是明显的报错。

这些测试把上限、放大系数、队头阻塞和过载后果**量化**下来,作为协议重构的基线;
同时它们本身就是回归护栏:改动 tick 周期或每 tick 动作数会立刻反映在数字上。

无 AutoHotkey v2 时自动跳过。
"""

import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:
    pytest = None

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AHK_SCRIPT = os.path.join(REPO, "hold_server_extended.ahk")

_AHK_CANDIDATES = [
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
]


def _find_ahk():
    for p in _AHK_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


AHK_EXE = _find_ahk()

if pytest is not None:
    pytestmark = pytest.mark.skipif(
        AHK_EXE is None or not os.path.isfile(AHK_SCRIPT),
        reason="需要 AutoHotkey v2 与 hold_server_extended.ahk",
    )

_EXTRACT = [
    "EnqueueAction",
    "ProcessQueue",
    "ExecuteAction",
    "IncrementQueueCount",
    "DecrementQueueCount",
    "ClearNonEmergencyQueues",
    "ClearQueue",
    "IsAllowedDuringPause",
    "EnforceQueueBudget",
    "DropOldestDroppableFrom",
    "IsDroppableAction",
    "NotifyQueueOverload",
    "AtomCount",
    "PendingAtomCount",
    "NextToExecuteQueueName",
    "PushFrontAction",
    "QueueByName",
    "QueueItem",
    "PriorityOfQueueName",
    "CachedStrSplit",
    "MonotonicMs",
    "PushFrontWait",
    "PushFrontItem",
    "SendStatsToPython",
]


def _extract_function(src_lines, name):
    """逐字抽取 `name(...) {` 到配对 `}` 的函数体(忽略注释里的花括号)。"""
    start = None
    for i, line in enumerate(src_lines):
        if re.match(rf"^{re.escape(name)}\(.*\)\s*\{{\s*$", line):
            start = i
            break
    if start is None:
        raise AssertionError(f"未能在 hold_server_extended.ahk 中找到函数 {name}")
    depth = 0
    out = []
    for line in src_lines[start:]:
        out.append(line)
        code = re.sub(r";.*$", "", line)
        depth += code.count("{") - code.count("}")
        if depth == 0:
            return "\n".join(out)
    raise AssertionError(f"函数 {name} 的花括号不配对")


_STUBS = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off

global ResultFile := A_Args.Length >= 1 ? A_Args[1] : (A_ScriptDir "\out.txt")

; ---- 与 hold_server_extended.ahk 同名同初值的队列全局 ----
global EmergencyQueue := []
global HighQueue := []
global NormalQueue := []
global LowQueue := []
global QueueCounts := Map("emergency", 0, "high", 0, "normal", 0, "low", 0)
global TotalQueueCount := 0
global QueueStats := Map("emergency", 0, "high", 0, "normal", 0, "low", 0, "processed", 0, "dropped", 0, "expired", 0)
global MAX_PENDING_ATOMS := __MAX_PENDING_ATOMS__
global LastOverloadNotifyAt := 0
global PendingOverloadNotify := false
global OverloadNotifications := []
global IsPaused := false
global SpecialKeysPaused := false
global RuntimeAcceptingActions := true
global ManagedDelayUntil := 0
global ActiveManagedKeys := Map()
global SkillHeldKeys := Map()
global SkillHeldOrder := []
global ManagedHoldTargets := Map()
global ACTION_PRESS := "press"
global ACTION_HOLD := "hold"
global ACTION_RELEASE := "release"
global ACTION_SEQUENCE := "sequence"
global ACTION_CLEANUP := "cleanup"
global ACTION_MOUSE_CLICK := "click"
global ACTION_DELAY := "delay"
global ACTION_NOTIFY := "notify"
global ACTION_SEQ_RUNNING := "seqrun"
global STALE_MS := __STALE_MS__
; 真实 CachedStrSplit 的缓存(必须用真实现:此前的"每次新建数组"桩掩盖了
; ExecuteAction 原地修改共享缓存数组、重复执行同一序列逐轮丢键的 HIGH 级 BUG)
global StringSplitCache := Map()
global MaxCacheSize := 100

; ---- 计量:执行记录(动作名 + 执行时所在的 tick 序号)----
global ExecLog := []            ; ["<action>@<tick>", ...]
global CurrentTick := 0

; ---- 桩:物理发键换成记录,持键账本已由 test_ahk_hold_ledger 覆盖 ----
SendPress(key, forceMoveBypass := false) {
    global ExecLog, CurrentTick
    ExecLog.Push("press:" key "@" CurrentTick)
    return true
}
SendDown(key) {
    return true
}
SendUp(key) {
    return true
}
ShouldBlockMouseInStationary(key) {
    return false
}
ForgetSkillHeldKey(key) {
    return false
}
ReconcileSkillHoldKeys() {
}
MarkManagedHoldTarget(key) {
}
ClearManagedHoldTarget(key) {
}
ReleaseAllManagedHoldTargets() {
    return false
}
ClearManagedKeyMark(key) {
}
ExecuteMouseClick(data) {
}
SendEventToPython(data, bypassBackoff := false, armBackoff := true) {
    global OverloadNotifications
    OverloadNotifications.Push(data)
}
IsEmergencyAction(action) {
    ; 与实现一致的判定形态:emergency 队列里的 press:hp/mp 属于救命动作
    return InStr(action, "press:hp") = 1 || InStr(action, "press:mp") = 1
}

ReleaseAllSkillHoldKeys() {
}
StopMacro() {
}

; 驱动一个 tick(= 真实实现里 SetTimer(ProcessQueue, QUEUE_TICK_MS) 的一次触发)
Tick() {
    global CurrentTick
    CurrentTick += 1
    ProcessQueue()
}

ResetAll() {
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue, QueueCounts, TotalQueueCount
    global QueueStats, IsPaused, SpecialKeysPaused, RuntimeAcceptingActions
    global ManagedDelayUntil, ExecLog, CurrentTick
    global PendingOverloadNotify, LastOverloadNotifyAt, OverloadNotifications
    global MAX_PENDING_ATOMS, STALE_MS, StringSplitCache
    StringSplitCache := Map()
    STALE_MS := __STALE_MS__
    PendingOverloadNotify := false
    LastOverloadNotifyAt := 0
    OverloadNotifications := []
    MAX_PENDING_ATOMS := __MAX_PENDING_ATOMS__   ; 取自实现;个别场景会显式抬高以测基线行为
    EmergencyQueue := []
    HighQueue := []
    NormalQueue := []
    LowQueue := []
    QueueCounts := Map("emergency", 0, "high", 0, "normal", 0, "low", 0)
    TotalQueueCount := 0
    QueueStats := Map("emergency", 0, "high", 0, "normal", 0, "low", 0, "processed", 0, "dropped", 0, "expired", 0)
    IsPaused := false
    SpecialKeysPaused := false
    RuntimeAcceptingActions := true
    ManagedDelayUntil := 0
    ExecLog := []
    CurrentTick := 0
}

global Results := Map()
Record(name, value) {
    global Results
    Results[name] := value
}
; 测试侧辅助:队列里**全部**待发原子(含队首)。PendingAtomCount 是预算口径
; (扣掉正在执行的队首决策),这里要的是绝对总量,用来断言"总量仍然有界"。
TotalPendingAtoms() {
    global HighQueue, NormalQueue, LowQueue
    total := 0
    for i, a in HighQueue {
        total += AtomCount(a.action)
    }
    for i, a in NormalQueue {
        total += AtomCount(a.action)
    }
    for i, a in LowQueue {
        total += AtomCount(a.action)
    }
    return total
}
JoinLog() {
    global ExecLog
    out := ""
    for i, e in ExecLog {
        out .= (i > 1 ? "," : "") e
    }
    return out
}
"""


_SCENARIOS = r"""
; =====================================================================
; S1 吞吐上限:每 tick 最多执行一个动作
; =====================================================================
ResetAll()
MAX_PENDING_ATOMS := 1000000   ; 测的是排空速率本身,先让预算不参与
STALE_MS := 1000000            ; 只测排空速率,慢机上跑满 100 tick 也不许过期
loop 100 {
    EnqueueAction(2, "press:k" A_Index)
}
Record("s1_enqueued_total", TotalQueueCount)
ticksUsed := 0
loop 500 {
    if (TotalQueueCount = 0) {
        break
    }
    Tick()
    ticksUsed += 1
}
Record("s1_ticks_to_drain", ticksUsed)
Record("s1_executed", ExecLog.Length)

; =====================================================================
; S2 序列放大:占 1 个队列项(一个决策),但要 N 个 tick 才发得完(N 个原子)
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:q,w,e")
Record("s2_items_for_3key_sequence", TotalQueueCount)
Record("s2_atoms_for_3key_sequence", AtomCount(NormalQueue[1].action))
ResetAll()
EnqueueAction(2, "sequence:q,delay100,w")
Record("s2_items_with_delay", TotalQueueCount)
Record("s2_atoms_with_delay", AtomCount(NormalQueue[1].action))
; 按序走完:每 tick 推进一个原子,剩余部分回到队首
ResetAll()
EnqueueAction(2, "sequence:q,w,e")
loop 5 {
    Tick()
}
Record("s2_exec_order", JoinLog())

; =====================================================================
; S3 按队列延时:低优先级的 delay 只挡自己的队列,高优先级照常执行
;     (裸 delay:N 转成本队列等待哨兵,到期出队即完成,不重置延时)
; =====================================================================
ResetAll()
EnqueueAction(3, "delay:100")        ; low 优先级的一个裸 delay
Tick()                                ; 取出 delay → low 队首变等待哨兵(notBefore=+100)
EnqueueAction(1, "press:high_skill") ; 之后到达的 high 优先级技能
Tick()                                ; low 在等,high **照常执行**
Record("s3_high_ran_during_low_delay", InStr(JoinLog(), "press:high_skill@") ? 1 : 0)
; low 自己仍被挡住
EnqueueAction(3, "press:low_after")
Tick()
Record("s3_low_blocked_while_waiting", InStr(JoinLog(), "press:low_after@") ? 1 : 0)
Record("s3_low_sentinel_present", LowQueue.Length > 0 && InStr(LowQueue[1].action, "seqrun:") = 1 ? 1 : 0)
; 到期(测试拨快时钟):哨兵出队消化一个 tick,随后 low 恢复执行
if (LowQueue.Length > 0) {
    LowQueue[1].notBefore := MonotonicMs() - 1
}
Tick()
Tick()
Record("s3_after_delay", JoinLog())
Record("s3_low_after_ran", InStr(JoinLog(), "press:low_after@") ? 1 : 0)
; 哨兵不得重置延时:到期消化后队列里不应再有 seqrun 残留
s3SentinelLeft := 0
for i, it in LowQueue {
    if (InStr(it.action, "seqrun:") = 1) {
        s3SentinelLeft += 1
    }
}
Record("s3_sentinel_gone", s3SentinelLeft = 0 ? 1 : 0)

; =====================================================================
; S22 尾部 delay 不消失:"q,delay100" 发完 q 后必须留空哨兵占住队首
;     (否则序列末尾的间隔语义直接蒸发,后续动作提前打出)
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:q,delay100")
EnqueueAction(2, "press:w")           ; 排在序列后面,应等满间隔再执行
Tick()                                ; 发 q
Tick()                                ; delay 原子 → 空哨兵占队首(notBefore=+100)
Record("s22_head_is_sentinel", NormalQueue.Length > 0 && InStr(NormalQueue[1].action, "seqrun:") = 1 ? 1 : 0)
Record("s22_sentinel_wait_ms", NormalQueue.Length > 0 ? NormalQueue[1].notBefore - MonotonicMs() : -99999)
; 等待期间探**两个** tick:只探一个的话,"忽略 notBefore 提前消化哨兵"的回归
; 恰好用哨兵的空转吃掉那个 tick,w 看起来仍被挡住 —— 两个 tick 就会露馅
Tick()
Tick()
Record("s22_w_blocked_during_wait", InStr(JoinLog(), "press:w@") ? 1 : 0)
NormalQueue[1].notBefore := MonotonicMs() - 1
Tick()                                ; 哨兵出队消化
Tick()                                ; w 执行
Record("s22_w_ran_after", InStr(JoinLog(), "press:w@") ? 1 : 0)
s22SentinelLeft := 0
for i, it in NormalQueue {
    if (InStr(it.action, "seqrun:") = 1) {
        s22SentinelLeft += 1
    }
}
Record("s22_sentinel_gone", s22SentinelLeft = 0 ? 1 : 0)

; =====================================================================
; S23 序列中段 delay:挡自己队列的同时,别的队列照常;到期后按序完成
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:q,delay100,w")
Tick()                                ; 发 q
Tick()                                ; delay → seqrun:press:w 带 notBefore
EnqueueAction(1, "press:hi")
Tick()                                ; normal 在等,high 执行
Record("s23_high_ran_during_normal_delay", InStr(JoinLog(), "press:hi@") ? 1 : 0)
Record("s23_w_not_yet", InStr(JoinLog(), "press:w@") ? 0 : 1)
if (NormalQueue.Length > 0) {
    NormalQueue[1].notBefore := MonotonicMs() - 1
}
Tick()                                ; seqrun:press:w 到期 → 发 w
Record("s23_exec", JoinLog())

; =====================================================================
; S25 delay_clear 保持全局独占(管理键专利):窗口内清空非紧急队列,
;     只放行 HP/MP 救命药剂
; =====================================================================
ResetAll()
EnqueueAction(2, "press:will_be_cleared")
EnqueueAction(0, "delay_clear:100")
EnqueueAction(0, "press:hp")          ; 排在 delay_clear 后面的救命药剂
EnqueueAction(1, "press:hi_blocked")
Tick()                                ; emergency 取出 delay_clear → 设 ManagedDelayUntil
Record("s25_managed_delay_active", ManagedDelayUntil > 0 ? 1 : 0)
Tick()                                ; 窗口内:清空非紧急 + 放行 press:hp
Record("s25_nonemergency_cleared", QueueCounts["normal"] = 0 && QueueCounts["high"] = 0 ? 1 : 0)
Record("s25_hp_ran", InStr(JoinLog(), "press:hp@") ? 1 : 0)
Record("s25_blocked_never_ran", (InStr(JoinLog(), "hi_blocked") || InStr(JoinLog(), "will_be_cleared")) ? 0 : 1)

; =====================================================================
; S4 过载:生产速率 = 消费速率的 2 倍(每 tick 入队 2 个,只执行 1 个)
;    模拟 10 秒 = 500 个 tick
; =====================================================================
ResetAll()
produced := 0
loop 500 {
    EnqueueAction(2, "press:a" produced)
    produced += 1
    EnqueueAction(2, "press:a" produced)
    produced += 1
    Tick()
}
Record("s4_produced", produced)
Record("s4_executed", ExecLog.Length)
Record("s4_backlog", TotalQueueCount)
Record("s4_dropped", QueueStats["dropped"])
; 队头动作的"年龄":最后执行的那个动作是第几个入队的
lastEntry := ExecLog[ExecLog.Length]
Record("s4_last_executed", lastEntry)
Record("s4_overload_notifications", OverloadNotifications.Length)
Record("s4_first_notification", OverloadNotifications.Length > 0 ? OverloadNotifications[1] : "")

; =====================================================================
; S7 release: 永不被丢弃(丢了就是游戏里卡键,用户只能重启客户端)
; =====================================================================
ResetAll()
EnqueueAction(2, "release:stuck_key")
loop 100 {
    EnqueueAction(2, "press:flood" A_Index)
}
releasesLeft := 0
for i, a in NormalQueue {
    if (InStr(a.action, "release:") = 1) {
        releasesLeft += 1
    }
}
Record("s7_release_survived", releasesLeft)
Record("s7_backlog", QueueCounts["normal"])

; =====================================================================
; S8 cleanup: 永不被丢弃(丢了 ActiveManagedKeys 锁残留 → 该管理键永远点不出来)
; =====================================================================
ResetAll()
EnqueueAction(2, "cleanup:managed_e")
loop 100 {
    EnqueueAction(2, "press:flood" A_Index)
}
cleanupsLeft := 0
for i, a in NormalQueue {
    if (InStr(a.action, "cleanup:") = 1) {
        cleanupsLeft += 1
    }
}
Record("s8_cleanup_survived", cleanupsLeft)

; =====================================================================
; S9 紧急队列不设上限:救命药剂宁可积压也绝不丢
; =====================================================================
ResetAll()
loop 100 {
    EnqueueAction(0, "press:hp")
}
Record("s9_emergency_backlog", QueueCounts["emergency"])
Record("s9_dropped", QueueStats["dropped"])

; =====================================================================
; S10 全是不可丢动作时:宁可暂时超深也不丢(不能为了深度去制造卡键)
; =====================================================================
ResetAll()
loop 40 {
    EnqueueAction(2, "release:k" A_Index)
}
Record("s10_all_release_backlog", QueueCounts["normal"])
Record("s10_dropped", QueueStats["dropped"])

; =====================================================================
; S12 长连招:作为**一个**队列决策排队,原子同生共死
; =====================================================================
ResetAll()
seq := "sequence:k1"
loop 19 {
    seq .= ",k" (A_Index + 1)
}
EnqueueAction(2, seq)                       ; 20 键连招,空队列,零过载
Record("s12_items", QueueCounts["normal"])
Record("s12_atoms", AtomCount(NormalQueue[1].action))
Record("s12_dropped", QueueStats["dropped"])

; 关键回归:随后再入队普通动作,不得从中间裁断连招
loop 5 {
    EnqueueAction(2, "press:later" A_Index)
}
Record("s12_after_more_dropped", QueueStats["dropped"])
laterLeft := 0
for i, a in NormalQueue {
    if (InStr(a.action, "press:later") = 1) {
        laterLeft += 1
    }
}
Record("s12_later_survived", laterLeft)
seqLeft := ""
for i, a in NormalQueue {
    if (InStr(a.action, "sequence:") = 1) {
        seqLeft := a.action
    }
}
Record("s12_sequence_items_left", seqLeft = "" ? 0 : 1)
Record("s12_sequence_atoms_left", seqLeft = "" ? 0 : AtomCount(seqLeft))
Record("s12_sequence_head", seqLeft = "" ? "<dropped>" : StrSplit(SubStr(seqLeft, 10), ",")[1])

; 执行顺序必须是完整的 k1..k20
ResetAll()
EnqueueAction(2, seq)
loop 25 {
    Tick()
}
Record("s12_exec_order", JoinLog())

; 带间隔的连招(delay 也算原子):"1,delay100,2,..." 形态
ResetAll()
seq2 := "sequence:1"
loop 8 {
    seq2 .= ",delay100,"  (A_Index + 1)
}
EnqueueAction(2, seq2)
Record("s12b_items", QueueCounts["normal"])
Record("s12b_atoms", AtomCount(NormalQueue[1].action))
Record("s12b_dropped", QueueStats["dropped"])

; =====================================================================
; S13 队头全是不可丢动作时,不得退化成"丢最新":刚到达的动作最该活下来
; =====================================================================
ResetAll()
loop 30 {
    EnqueueAction(2, "release:r" A_Index)
}
loop 30 {
    EnqueueAction(2, "press:p" A_Index)
}
survived := ""
for i, a in NormalQueue {
    if (InStr(a.action, "press:") = 1) {
        survived .= (survived = "" ? "" : ",") a.action
    }
}
Record("s13_press_survived", survived)
Record("s13_releases_kept", QueueCounts["normal"])

; =====================================================================
; S14 混合优先级:预算是**全局**的。按队列各限 16 的话三条队列能同时积压 48 项,
;     而出队是严格优先级的 —— 低优先级要等全部 48 项,延迟上限名存实亡。
; =====================================================================
ResetAll()
loop 40 {
    EnqueueAction(1, "press:h" A_Index)
    EnqueueAction(2, "press:n" A_Index)
    EnqueueAction(3, "press:l" A_Index)
}
Record("s14_total_nonemergency", QueueCounts["high"] + QueueCounts["normal"] + QueueCounts["low"])
Record("s14_atoms", PendingAtomCount())
Record("s14_total_atoms", TotalPendingAtoms())
Record("s14_high", QueueCounts["high"])
Record("s14_normal", QueueCounts["normal"])
Record("s14_low", QueueCounts["low"])

; =====================================================================
; S15 持续的高优先级生产:低优先级动作应被**丢弃并上报**,
;     而不是无限期留在队列里越变越陈旧(严格优先级下它永远排不上)
; =====================================================================
ResetAll()
EnqueueAction(3, "press:low_stale")
loop 100 {
    EnqueueAction(1, "press:h" A_Index)
    Tick()
}
; 懒判定:没轮到它之前留在队列里(预算没超,占位无害)
Record("s15_low_backlog_during", QueueCounts["low"])
; 年龄是**墙钟毫秒**,上面的 tick 循环瞬间跑完 —— 测试用直接改时间戳模拟真实等待
if (LowQueue.Length > 0) {
    LowQueue[1].at := MonotonicMs() - 1000
}
; 高优先级停产 → 下一 tick 轮到它,但真实已等 1 秒 → 丢弃,不执行
Tick()
Record("s15_low_backlog", QueueCounts["low"])
Record("s15_atoms", PendingAtomCount())
Record("s15_total_atoms", TotalPendingAtoms())
Record("s15_dropped", QueueStats["expired"])
Record("s15_low_executed", InStr(JoinLog(), "low_stale") ? 1 : 0)

; =====================================================================
; S18 年龄绑定在项上:清队列后新入队的动作不得"继承"旧年龄
;     (旧的每队列计数不清零:等了 31 tick → PAUSED 清队 → 新动作 1 tick 就被丢)
; =====================================================================
ResetAll()
EnqueueAction(3, "press:old_low")
loop 31 {
    EnqueueAction(1, "press:h" A_Index)
    Tick()
}
ClearQueue(-1)                       ; PAUSED 语义:全清
EnqueueAction(3, "press:fresh_low")
Tick()
Record("s18_fresh_executed", InStr(JoinLog(), "fresh_low") ? 1 : 0)
Record("s18_old_executed", InStr(JoinLog(), "old_low") ? 1 : 0)

; =====================================================================
; S19 排在队首后面、等了同样久的项也按自己的年龄判(旧计数只描述队首:
;     丢了前两个后计数清零,等了 64 tick 的第 3 个照样被执行)
; =====================================================================
ResetAll()
loop 16 {
    EnqueueAction(3, "press:stale" A_Index)
}
loop 64 {
    EnqueueAction(1, "press:h" A_Index)
    Tick()
}
; 墙钟语义:循环瞬间完成,直接改时间戳模拟 16 个都真实等了 1 秒
for i, item in LowQueue {
    item.at := MonotonicMs() - 1000
}
Tick()                               ; 高停产:16 个全部过期,应全部丢弃且零执行
staleExec := 0
loop 16 {
    if (InStr(JoinLog(), "press:stale" A_Index "@")) {
        staleExec += 1
    }
}
Record("s19_stale_executed", staleExec)
Record("s19_low_backlog", QueueCounts["low"])
Record("s19_dropped", QueueStats["expired"])
; 过期丢弃也要走 queue_drop 上报,且载荷里 expired= 必须等于刚才的丢弃数。
; 节流按真实毫秒计,场景瞬间跑完必然被节流(标志保留)—— 把"上次通知时刻"
; 拨回 2 秒前模拟真实等待,下一 tick 顶部就应把带 expired 的载荷发出去。
LastOverloadNotifyAt := MonotonicMs() - 2000
Tick()
Record("s19c_notify_payload", OverloadNotifications.Length > 0 ? OverloadNotifications[OverloadNotifications.Length] : "(none)")
Record("s19c_expected_expired", QueueStats["expired"])
; 时间戳本身必须是"现在"(强行标老的场景都覆写了 at,这里验证入队时记录的原始值)
ResetAll()
EnqueueAction(2, "press:probe")
Record("s19b_at_delta", MonotonicMs() - NormalQueue[1].at)

; =====================================================================
; S20 重复执行同一序列必须每轮完整(真实 CachedStrSplit 下的回归:
;     ExecuteAction 原地 RemoveAt 缓存共享数组 → 第二次只剩 w、第三次全空)
; =====================================================================
ResetAll()
loop 3 {
    EnqueueAction(2, "sequence:q,w")
    Tick()
    Tick()
    Record("s20_run" A_Index, JoinLog())
    ExecLog := []
}

; =====================================================================
; S21 不同优先级各有一条已开打序列(seqrun)时,预算必须收敛
;     (只豁免全局队首的话,被抢占的 normal seqrun 把预算永久顶爆,
;      两条都不可丢,此后所有新动作一到就被丢)
; =====================================================================
ResetAll()
seqN := "sequence:n1"
loop 39 {
    seqN .= ",n" (A_Index + 1)
}
EnqueueAction(2, seqN)
Tick()                               ; n1 发出,normal 队首变 seqrun(剩 39)
seqH := "sequence:h1"
loop 39 {
    seqH .= ",h" (A_Index + 1)
}
EnqueueAction(1, seqH)
Tick()                               ; h1 发出,high 队首变 seqrun(剩 39)
Record("s21_budget_with_two_seqruns", PendingAtomCount())
loop 5 {
    EnqueueAction(2, "press:extra" A_Index)
}
Record("s21_dropped_at_enqueue", QueueStats["dropped"])
extraLeft := 0
for i, a in NormalQueue {
    if (InStr(a.action, "press:extra") = 1) {
        extraLeft += 1
    }
}
Record("s21_extra_survived", extraLeft)
loop 200 {
    Tick()
}
hDone := 0
nDone := 0
loop 40 {
    if (InStr(JoinLog(), "press:h" A_Index "@")) {
        hDone += 1
    }
    if (InStr(JoinLog(), "press:n" A_Index "@")) {
        nDone += 1
    }
}
Record("s21_h_completed", hDone)
Record("s21_n_completed", nDone)

; =====================================================================
; S16 序列在过载下整体被丢,不留半截(丢一半 = 打出前三个键就停手)
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:a1,a2,a3,a4,a5,a6,a7,a8")
loop 30 {
    EnqueueAction(2, "press:flood" A_Index)
}
partial := 0
for i, a in NormalQueue {
    if (InStr(a.action, "sequence:") = 1) {
        partial += 1
    }
}
Record("s16_sequence_items_left", partial)
Record("s16_atoms", PendingAtomCount())
Record("s16_total_atoms", TotalPendingAtoms())

; =====================================================================
; S17 已经开打的连招不得被"饥饿丢弃"抹掉后半段
;     (高优先级持续生产 → normal 长期排不上 → 计龄会丢队首;
;      但队首是 seqrun: 已开打的连招,丢它 = 打一半停手)
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:c1,c2,c3,c4")
Tick()                          ; 发出 c1,剩余变成 seqrun:
Record("s17_head_after_first", NormalQueue.Length > 0 ? SubStr(NormalQueue[1].action, 1, 7) : "")
loop 120 {                      ; 持续高优先级生产,normal 的 seqrun 一直排不上
    EnqueueAction(1, "press:hp" A_Index)
    Tick()
}
; 把在飞 seqrun 强行标老:即便真实等了 1 秒,它也必须执行而不是被过期丢弃
if (NormalQueue.Length > 0) {
    NormalQueue[1].at := MonotonicMs() - 1000
}
seqrunLeft := 0
for i, a in NormalQueue {
    if (InStr(a.action, "seqrun:") = 1) {
        seqrunLeft += 1
    }
}
Record("s17_seqrun_survived", seqrunLeft)
Record("s17_dropped_something", QueueStats["dropped"] > 0 ? 1 : 0)
; 高优先级停产后,连招必须把剩下的原子按序打完
loop 20 {
    Tick()
}
Record("s17_exec_tail", JoinLog())


; =====================================================================
; S5 紧急动作不被积压饿死(救命药剂必须插队)
; =====================================================================
ResetAll()
MAX_PENDING_ATOMS := 1000000   ; 要的是"深积压"场景,先让预算不参与
loop 200 {
    EnqueueAction(2, "press:filler" A_Index)
}
EnqueueAction(0, "press:hp")
Tick()
Record("s5_first_exec_with_200_backlog", JoinLog())

; =====================================================================
; S6 单次 tick 的动作预算(静态断言的行为面):即便队列很深也只出一个
; =====================================================================
ResetAll()
MAX_PENDING_ATOMS := 1000000
loop 50 {
    EnqueueAction(1, "press:h" A_Index)
}
Tick()
Record("s6_exec_in_one_tick", ExecLog.Length)

; =====================================================================
; S26 stats 推送:e/h/n/l 必须是**实时**队列深度(累计入队数只涨不落,
;     看不出当前积压);p/d/x 是累计 处理/过载丢弃/等待过期
; =====================================================================
ResetAll()
EnqueueAction(0, "press:hp")
loop 3 {
    EnqueueAction(2, "press:s" A_Index)
}
EnqueueAction(1, "press:hs")
Tick()                               ; hp(emergency 优先)
Tick()                               ; hs(high)
Tick()                               ; s1(normal)
SendStatsToPython()
Record("s26_stats_payload", OverloadNotifications.Length > 0 ? OverloadNotifications[OverloadNotifications.Length] : "(none)")
Record("s26_depth_e", QueueCounts["emergency"])
Record("s26_depth_h", QueueCounts["high"])
Record("s26_depth_n", QueueCounts["normal"])
Record("s26_depth_l", QueueCounts["low"])
Record("s26_processed", QueueStats["processed"])

; ---- 输出 ----
out := ""
for k, v in Results {
    out .= k "=" v "`n"
}
try FileDelete(ResultFile)
FileAppend(out, ResultFile, "UTF-8")
ExitApp(0)
"""


def _build_and_run():
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src_lines = fp.read().splitlines()

    # 常量注入:桩里必须用**实现里的实际值**,抄一份会让常量变异测不出来
    parts = [_STUBS.replace("__MAX_PENDING_ATOMS__", str(MAX_PENDING_ATOMS))
                   .replace("__STALE_MS__", str(STALE_MS))]
    for name in _EXTRACT:
        parts.append(f"\n; ===== 原文抽取: {name} =====")
        parts.append(_extract_function(src_lines, name))
    parts.append(_SCENARIOS)
    script = "\n".join(parts)

    tmpdir = tempfile.mkdtemp(prefix="ahk_qthr_")
    script_path = os.path.join(tmpdir, "stress.ahk")
    out_path = os.path.join(tmpdir, "out.txt")
    with open(script_path, "w", encoding="utf-8") as fp:
        fp.write(script)

    proc = subprocess.run(
        [AHK_EXE, script_path, out_path],
        capture_output=True, text=True, timeout=180,
    )
    if not os.path.isfile(out_path):
        raise AssertionError(
            f"AHK 未产出结果文件 (exit={proc.returncode})\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}\n脚本: {script_path}"
        )
    data = {}
    # utf-8-sig: AHK 的 FileAppend(..., "UTF-8") 会写 BOM,不剥掉会污染第一个 key
    with open(out_path, "r", encoding="utf-8-sig") as fp:
        for line in fp:
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k] = v
    return data


_MEASURED = None


def _measure():
    global _MEASURED
    if _MEASURED is None:
        _MEASURED = _build_and_run()
    return _MEASURED


# ---------------------------------------------------------------------------
# 真实定时器周期(墙钟实测,不是 1000/周期 的算术)
# ---------------------------------------------------------------------------
def _ahk_constant(name):
    """从 hold_server_extended.ahk 读取常量的**实际**值。

    不要在测试里抄一份常量:抄了之后改实现的常量,行为测试仍按抄来的值跑,
    永远绿。(这里就踩过:硬编码 MAX_PENDING_ATOMS=16 的桩让"取消预算"
    这个变异完全没被发现。)
    """
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src = fp.read()
    m = re.search(rf"global {re.escape(name)}\s*:=\s*(\d+)", src)
    assert m, f"未能在 hold_server_extended.ahk 中找到常量 {name}"
    return int(m.group(1))


TICK_MS = _ahk_constant("QUEUE_TICK_MS")
MAX_PENDING_ATOMS = _ahk_constant("MAX_PENDING_ATOMS")
STALE_MS = _ahk_constant("STALE_MS")
# Windows 时钟粒度 ~15.6ms,请求 15ms 落在 1 个系统 tick 上 → 实测 ~15.8ms ≈ 63/s。
# (请求 20ms 会被凑成 2 个系统 tick = 31.6ms,只有一半吞吐 —— 这正是本次改动的原因。)
# 低于这个下限说明定时器被拖慢,队列会长期过载。
MIN_ACCEPTABLE_RATE = 45.0

# 绝对预算(**不**跟随上面两个常量):这才是用户真正拿到的保证。
# 相对断言(backlog <= MAX_PENDING_ATOMS)在"有人把上限调到 100 万"时照样成立,
# 测不出"预算被取消"这件事 —— 所以必须另有一条绝对上界。
ABSOLUTE_BACKLOG_CEILING = 32          # 2 × 文档里的预算
ABSOLUTE_LAG_CEILING_MS = 500          # 修复前是 10000ms 且随时间线性增长

# SetTimer 走 Windows 消息定时器,分辨率受系统时钟粒度(默认 ~15.6ms)限制:
# SetTimer(..., 20) 会被凑到 2 个系统 tick ≈ 31.2ms;请求 15ms 才落在 1 个 tick 上。
# 别的进程把全局定时器分辨率调高时数值还会变。所以吞吐上限是一个**区间**,
# 必须真量,不能拿常数算 —— 这正是"需压力测试"的地方。
_TIMER_PROBE = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off
Persistent

global Fires := 0
global StartAt := DllCall("Kernel32\GetTickCount64", "UInt64")
global OutFile := A_Args.Length >= 1 ? A_Args[1] : (A_ScriptDir "\timer.txt")

Fire() {
    global Fires
    Fires += 1
}
Finish() {
    global Fires, StartAt, OutFile
    elapsed := DllCall("Kernel32\GetTickCount64", "UInt64") - StartAt
    SetTimer(Fire, 0)
    try FileDelete(OutFile)
    FileAppend("elapsed_ms=" elapsed "`nfires=" Fires "`n", OutFile, "UTF-8")
    ExitApp(0)
}

SetTimer(Fire, %d)
SetTimer(Finish, -2000)
""" % TICK_MS

_TIMER_MEASURED = None


def _measure_timer_period():
    """返回 (实测周期ms, 实测动作/秒)。"""
    global _TIMER_MEASURED
    if _TIMER_MEASURED is not None:
        return _TIMER_MEASURED

    tmpdir = tempfile.mkdtemp(prefix="ahk_timer_")
    script_path = os.path.join(tmpdir, "timer.ahk")
    out_path = os.path.join(tmpdir, "timer.txt")
    with open(script_path, "w", encoding="utf-8") as fp:
        fp.write(_TIMER_PROBE)
    subprocess.run([AHK_EXE, script_path, out_path],
                   capture_output=True, text=True, timeout=60)
    vals = {}
    with open(out_path, "r", encoding="utf-8-sig") as fp:
        for line in fp:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                vals[k] = int(v)
    period = vals["elapsed_ms"] / max(1, vals["fires"])
    _TIMER_MEASURED = (period, 1000.0 / period)
    return _TIMER_MEASURED


def test_process_queue_tick_period():
    """吞吐上限的两个来源之一:定时器周期。改这里等于改整套吞吐预算,
    所以把它钉成断言 —— 顺手保证下面所有 tick→毫秒 的换算不会悄悄失效。

    ⚠️ 别把它改回 20:Windows 时钟粒度 ~15.6ms,20 会被向上凑成 2 个系统 tick(31.6ms),
    吞吐直接腰斩到 31.7/s,连仓库里的现成配置都跑不住。"""
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src = fp.read()
    m = re.search(r"global QUEUE_TICK_MS\s*:=\s*(\d+)", src)
    assert m, "未找到 QUEUE_TICK_MS"
    assert int(m.group(1)) == TICK_MS
    assert re.search(r"SetTimer\(ProcessQueue,\s*QUEUE_TICK_MS\)", src), (
        "ProcessQueue 的定时器没有使用 QUEUE_TICK_MS —— 常量与实际周期会脱钩"
    )


def test_throughput_ceiling_is_one_action_per_tick():
    """吞吐上限的另一半:每 tick 最多执行一个动作。
    这不是配置项,而是 ProcessQueue 结构决定的硬上限;
    与实测的定时器周期相乘才是真正的动作/秒。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s1_enqueued_total"]) == 100
    assert int(d["s1_executed"]) == 100
    ticks = int(d["s1_ticks_to_drain"])
    assert ticks == 100, f"100 个动作用了 {ticks} 个 tick,不是 1 动作/tick"


def test_real_timer_period_and_throughput_ceiling():
    """吞吐上限必须**墙钟实测**,不能用 1000/20 算。

    SetTimer 走 Windows 消息定时器,受系统时钟粒度(默认 ~15.6ms)限制:
    请求 20ms 实际会被凑到 2 个系统 tick ≈ 31.2ms。所以真实上限是
    一个区间而不是一个定值 —— 按名义值做容量规划会高估一倍。
    """
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    period, per_sec = _measure_timer_period()
    assert 12.0 <= period <= 25.0, f"定时器周期 {period:.1f}ms 超出合理范围"
    assert per_sec >= MIN_ACCEPTABLE_RATE, (
        f"实测吞吐 {per_sec:.1f}/s 过低(应 ≈63/s),队列会长期过载 —— "
        f"检查 QUEUE_TICK_MS 是否被改回了 20"
    )
    latency_budget = MAX_PENDING_ATOMS * period
    print(f"  [实测] SetTimer({TICK_MS}) 真实周期 {period:.1f}ms → 吞吐上限 "
          f"{per_sec:.1f} 动作/秒(改动前:请求 20ms → 31.6ms → 31.7/s)")
    print(f"  [实测] 预算 {MAX_PENDING_ATOMS} 原子 → 最坏排队延迟 {latency_budget:.0f}ms")


def test_single_tick_emits_at_most_one_action():
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s6_exec_in_one_tick"]) == 1, (
        "一个 tick 执行了多个动作 —— 吞吐模型变了"
    )


def test_sequence_is_one_decision_but_many_atoms():
    """一次 send_sequence 占**一个队列项**(一个决策),但要 N 个 tick 才发得完。
    预算之所以按原子算正是因为这个落差 —— 按项算的话 16 个 20 原子的序列
    = 320 个 tick ≈ 5 秒,延迟上限名存实亡。
    生产侧按"一个技能一次调度"计费,消费侧按"一个原子一个 tick"计费,
    这个放大系数是过载最容易被忽视的来源。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s2_items_for_3key_sequence"]) == 1, "序列被展开成多个队列项了"
    assert int(d["s2_atoms_for_3key_sequence"]) == 3
    assert int(d["s2_items_with_delay"]) == 1
    assert int(d["s2_atoms_with_delay"]) == 3
    # 每 tick 推进一个原子,顺序不变
    assert d["s2_exec_order"] == "press:q@1,press:w@2,press:e@3", (
        f"序列执行顺序不对: {d['s2_exec_order']}"
    )
    print(f"  [实测] 'q,w,e' = 1 个决策 / 3 个原子 → 至少 {3 * TICK_MS}ms 才能发完")


def test_low_priority_delay_no_longer_blocks_high_priority():
    """按队列延时(修复了旧的全局 DelayUntil 队头阻塞):low 的 delay 只挡
    low 自己的队列,high 技能照常执行 —— 优先级在 delay 面前恢复成立。
    裸 delay:N 转成本队列等待哨兵:到期出队即完成,不重置延时。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s3_high_ran_during_low_delay"] == "1", (
        "low 的 delay 又把 high 压住了 —— 回归到全局队头阻塞"
    )
    assert d["s3_low_blocked_while_waiting"] == "0", (
        "delay 没有挡住自己所在的 low 队列"
    )
    assert d["s3_low_sentinel_present"] == "1", "等待哨兵没有占住 low 队首"
    assert d["s3_low_after_ran"] == "1", (
        f"delay 到期后 low 动作没有恢复执行: {d['s3_after_delay']}"
    )
    assert d["s3_sentinel_gone"] == "1", "等待哨兵到期后重置了延时(残留在队列里)"
    print("  [实测] low 的 delay:100 只挡 low 队列,high 技能照常执行")


def test_trailing_delay_keeps_a_wait_sentinel():
    """尾部 delay 不消失:"q,delay100" 发完 q 后留**空 seqrun: 哨兵**占住队首,
    排在后面的动作等满间隔才执行;哨兵到期出队即完成,不残留、不重置。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s22_head_is_sentinel"] == "1", "尾部 delay 没有留下等待哨兵(延时蒸发)"
    wait = int(d["s22_sentinel_wait_ms"])
    assert 0 < wait <= 100, f"哨兵等待时长 {wait}ms,应在 (0,100] 内"
    assert d["s22_w_blocked_during_wait"] == "0", "等待期间后续动作提前打出"
    assert d["s22_w_ran_after"] == "1", "到期后后续动作没有执行"
    assert d["s22_sentinel_gone"] == "1", "哨兵到期后没有消失"


def test_sequence_delay_blocks_only_its_own_queue():
    """序列中段 delay:挡自己队列的同时,别的队列照常执行;
    到期后序列按序走完(因果完整)。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s23_high_ran_during_normal_delay"] == "1", (
        "normal 序列的 delay 把 high 队列也压住了"
    )
    assert d["s23_w_not_yet"] == "1", "delay 未到期,序列后半段就打出去了"
    order = [e.split("@")[0] for e in d["s23_exec"].split(",") if e]
    assert order == ["press:q", "press:hi", "press:w"], (
        f"执行顺序不对(应 q → hi(插队) → w): {order}"
    )


def test_stats_push_reports_realtime_depths_not_cumulative():
    """队列观测(P3):stats 载荷的 e/h/n/l 必须是**实时**深度(QueueCounts),
    不是累计入队数(QueueStats 的 per-priority 计数只涨不落,OSD 上看不出
    "现在积压多少")。p/d/x 是累计 处理/过载丢弃/等待过期。
    场景里执行过 3 个动作后两种口径必然不同:实时 (0,0,2,0) vs 累计 (1,1,3,0)。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    payload = d["s26_stats_payload"]
    m = re.fullmatch(
        r"stats:e=(\d+),h=(\d+),n=(\d+),l=(\d+),p=(\d+),d=(\d+),x=(\d+)", payload
    )
    assert m, f"stats 载荷格式不对: {payload}"
    got = tuple(int(g) for g in m.groups())
    want = (
        int(d["s26_depth_e"]),
        int(d["s26_depth_h"]),
        int(d["s26_depth_n"]),
        int(d["s26_depth_l"]),
        int(d["s26_processed"]),
        0,
        0,
    )
    assert got == want, f"stats 载荷 {got} 与队列真实状态 {want} 不符"
    assert got[:4] == (0, 0, 2, 0), (
        f"实时深度应为 (0,0,2,0),载荷给的是 {got[:4]} —— e/h/n/l 用了累计入队数?"
    )
    # 推送必须由 AHK 端定时器驱动(每秒一次,不加轮询命令)
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src = fp.read()
    # ^ 行首锚定:被注释掉的 "; SetTimer(...)" 不能算数
    assert re.search(r"^SetTimer\(SendStatsToPython,\s*1000\)", src, re.M), (
        "缺少每秒一次的 stats 推送定时器 —— 观测通道又变回死代码"
    )


def test_managed_delay_clear_remains_global_exclusive():
    """delay_clear 保持全局独占(管理键专利,故意不改):窗口内清空非紧急队列,
    只放行 HP/MP 救命药剂 —— E 键闪避/强力技的独占语义。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s25_managed_delay_active"] == "1", "delay_clear 没有设置管理键独占窗口"
    assert d["s25_nonemergency_cleared"] == "1", "独占窗口没有清空非紧急队列"
    assert d["s25_hp_ran"] == "1", "独占窗口把救命药剂也压住了"
    assert d["s25_blocked_never_ran"] == "1", "被清掉的动作又被执行了"


def test_emergency_still_preempts_deep_backlog():
    """好消息侧:即便普通队列积压 200 项,紧急队列(HP/MP 救命药剂)
    仍在下一个 tick 就被执行 —— 过载不会饿死保命动作。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    first = d["s5_first_exec_with_200_backlog"]
    assert first.startswith("press:hp@"), f"紧急动作被积压挡住了: {first}"


def test_queue_budget_constants_are_the_expected_values():
    """预算跳闸线:这两个常量一起决定"最坏排队延迟"和"能否跟上生产速率"。
    改动它们是重大决定,必须连带重算 wiki/02 的吞吐表,所以在这里钉死。"""
    assert MAX_PENDING_ATOMS == 16, (
        f"MAX_PENDING_ATOMS 变成了 {MAX_PENDING_ATOMS};延迟上限 = 深度 × 实测周期,请重算文档"
    )
    assert TICK_MS == 15, (
        f"QUEUE_TICK_MS 变成了 {TICK_MS};注意 20 会被 Windows 凑成 31.6ms、吞吐腰斩"
    )
    assert STALE_MS == 500, (
        f"STALE_MS 变成了 {STALE_MS};这是'过期决策'的墙钟定义,改动请同步文档与诊断文案"
    )


def test_overload_backlog_stays_bounded():
    """过载后果的量化。生产速率 = 消费速率的 2 倍,跑 500 个 tick:

    修复前:队列无上界 → 积压 500 项,正在打出去的是 10 秒前决策的按键,
            而且随时间线性恶化,不报任何错(表现为"技能乱放")。
    修复后:全局预算把积压钉在 16 原子以内 → 排队延迟恒 ≤250ms,
            超出的最旧动作被丢弃并上报,过载变成可见的。
    """
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    produced = int(d["s4_produced"])
    executed = int(d["s4_executed"])
    backlog = int(d["s4_backlog"])
    dropped = int(d["s4_dropped"])

    assert produced == 1000
    assert executed == 500, f"每 tick 一个动作,500 个 tick 只能执行 {executed} 个"
    assert backlog <= MAX_PENDING_ATOMS + 1, f"积压 {backlog} 项,超过预算"
    # ⚠️ 绝对上界,不跟着常量走:上面那条断言在"有人把上限调到 100 万"时同样成立
    # (相对断言测不出"上限被取消"),而用户真正拿到的保证是绝对的毫秒数。
    assert backlog <= ABSOLUTE_BACKLOG_CEILING, (
        f"积压 {backlog} 项 —— 预算形同虚设(队列又变回无界了?)"
    )
    assert produced == executed + backlog + dropped, (
        f"动作账不平: 生产 {produced} != 执行 {executed} + 积压 {backlog} + 丢弃 {dropped}"
    )

    # 最后执行的动作是第几个入队的 → 实际排队延迟
    last = d["s4_last_executed"]          # 形如 "press:a984@500"
    idx = int(re.search(r"press:a(\d+)@", last).group(1))
    lag_ms = (produced - 1 - idx) * TICK_MS
    # 上界是"预算 + 正在执行的那个决策"(队首不计入预算,见 PendingAtomCount 的说明)。
    # 这里全是单原子动作,所以 +1。
    budget_ms = (MAX_PENDING_ATOMS + 1) * TICK_MS
    assert lag_ms <= budget_ms, (
        f"排队延迟 {lag_ms}ms 超过预算对应的 {budget_ms}ms"
    )
    assert lag_ms <= ABSOLUTE_LAG_CEILING_MS, (
        f"排队延迟 {lag_ms}ms 超过绝对预算 {ABSOLUTE_LAG_CEILING_MS}ms —— "
        f"这才是用户拿到的保证(修复前是 10000ms 且持续增长)"
    )
    print(f"  [实测] 10 秒过载后:积压 {backlog} 项(上限 {MAX_PENDING_ATOMS}),"
          f"丢弃 {dropped} 项,排队延迟 {lag_ms}ms(修复前是 10000ms 且持续增长)")


def test_overload_is_reported_not_silent():
    """静默丢弃会让用户以为是技能配置问题,必须上报。
    载荷钉完整格式(而不只是前缀):Python 端 `_on_queue_drop` 按
    `overload=N,expired=M` 解析,两边格式脱钩会让诊断静默失效。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s4_overload_notifications"]) >= 1, "过载被静默丢弃,没有任何上报"
    m = re.fullmatch(r"queue_drop:overload=(\d+),expired=(\d+)", d["s4_first_notification"])
    assert m, f"上报事件格式不对: {d['s4_first_notification']}"
    assert int(m.group(1)) > 0, "过载场景的载荷里 overload 计数为 0"


def test_expired_drops_are_reported_with_expired_payload():
    """过期丢弃走同一条 queue_drop 通道,但计入 expired= 而不是 overload= ——
    两种原因诊断建议不同(过载→调生产侧;过期→查长 delay/优先级压制),
    混在一个计数里会把"只是配了个长 delay"的用户引去调技能生产率。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    payload = d["s19c_notify_payload"]
    m = re.fullmatch(r"queue_drop:overload=(\d+),expired=(\d+)", payload)
    assert m, f"过期丢弃后的上报载荷格式不对: {payload}"
    assert int(m.group(2)) == int(d["s19c_expected_expired"]) == 16, (
        f"expired 载荷 {m.group(2)} 与实际过期丢弃数 {d['s19c_expected_expired']} 不符"
    )
    assert int(m.group(1)) == 0, (
        f"纯过期场景的载荷里 overload={m.group(1)} —— 过期被误计成过载"
    )


def test_ahk_clock_is_gettickcount64_not_a_tickcount():
    """回归(时钟回绕):A_TickCount 是 32 位 GetTickCount,~49.7 天回绕。
    回绕瞬间 now - at 变成巨大负数:过期判定失效(跨回绕的旧动作照常执行)、
    通知节流最长再压制 49.7 天、DelayUntil / MacroDueTime 比较冻结。
    所有时刻必须走 MonotonicMs()(GetTickCount64,无回绕,同样含休眠时间)。

    回绕行为无法在机器正常 uptime 下行为化复现(uptime < 49.7 天时两个时钟
    数值相同),所以守卫是静态的:钉实现 + 禁裸用。"""
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src = fp.read()
    # 1) MonotonicMs 的实现必须真的是 GetTickCount64
    assert re.search(
        r'MonotonicMs\(\)\s*\{\s*\n\s*return DllCall\("Kernel32\\GetTickCount64",\s*"UInt64"\)\s*\n\s*\}',
        src,
    ), "MonotonicMs() 的实现不再是 DllCall(GetTickCount64) —— 回绕保护失效"
    # 2) 代码行(去注释后)不得再出现裸 A_TickCount
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        code = re.sub(r";.*$", "", line)
        if "A_TickCount" in code:
            offenders.append(f"  行 {i}: {line.strip()}")
    assert not offenders, (
        "hold_server_extended.ahk 出现裸 A_TickCount(应使用 MonotonicMs(),"
        "否则 49.7 天回绕时年龄/节流/延迟比较全部失效):\n" + "\n".join(offenders)
    )


def test_overload_notification_is_throttled():
    """节流:过载持续发生时不能每次丢弃都发一条 —— 事件通道会被刷爆,
    而每条事件都是一次跨进程 SendMessage。485 次丢弃只应产生个位数通知。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    n = int(d["s4_overload_notifications"])
    dropped = int(d["s4_dropped"])
    assert n >= 1
    assert n * 50 < dropped, f"{dropped} 次丢弃发了 {n} 条通知 —— 节流没生效"
    print(f"  [实测] {dropped} 次丢弃 → {n} 条上报(每秒最多一条)")


def test_release_actions_are_never_dropped():
    """丢 release: 的后果是持久的:该键在游戏里一直按住,用户只能重启客户端。
    因此无论多过载都不丢 —— 这是丢弃策略里唯一不能妥协的一条。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s7_release_survived"]) == 1, "过载丢掉了 release: —— 会造成游戏内卡键"
    assert int(d["s7_backlog"]) <= MAX_PENDING_ATOMS + 1


def test_cleanup_actions_are_never_dropped():
    """丢 cleanup: → ActiveManagedKeys 锁残留 → 该管理键此后永远点不出来。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s8_cleanup_survived"]) == 1, "过载丢掉了 cleanup: —— 管理键会永久失效"


def test_emergency_queue_is_never_capped():
    """HP/MP 救命药剂宁可积压也绝不丢。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s9_emergency_backlog"]) == 100, "紧急队列被限深了"
    assert int(d["s9_dropped"]) == 0, "丢弃了紧急动作"


def test_long_sequence_survives_as_one_atomic_decision():
    """回归:一次 sequence 是**一个决策**,其原子有因果关系,必须同生共死。

    展开成 N 个队列项时有两个失效面,都实测过:
      1. 零过载也会自伤:整个展开在同一次 WM_COPYDATA 里完成,ProcessQueue 一个也
         排不出去,>16 原子的连招会自己吃掉前半段(20 键连招丢掉前 4 键)。
      2. 后续动作从中间裁断:20 步序列入队后再来一个普通动作,队头就从 k1 变成 k6。
    作为单个队列项则不可能出现半截连招。
    """
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s12_items"]) == 1, "20 键连招没有作为单个决策入队"
    assert int(d["s12_atoms"]) == 20
    assert int(d["s12_dropped"]) == 0

    # 队首那条连招不计入预算 → 排在它后面的少量动作不该被误伤
    # (把队首算进预算的话,20 原子的连招会让预算永远显示超标,后面的动作全被丢掉)
    assert int(d["s12_later_survived"]) == 5, (
        f"排在长连招后面的 5 个动作只剩 {d['s12_later_survived']} 个 —— "
        f"队首被计入预算了,正常动作被误伤"
    )
    assert int(d["s12_after_more_dropped"]) == 0

    # 关键回归:后续普通动作不得裁断连招
    assert int(d["s12_sequence_items_left"]) == 1, "连招被后续入队动作整条丢掉了"
    assert int(d["s12_sequence_atoms_left"]) == 20, (
        f"连招被裁断,只剩 {d['s12_sequence_atoms_left']} 个原子"
    )
    assert d["s12_sequence_head"] == "press:k1", (
        f"连招首键被丢,队头原子成了 {d['s12_sequence_head']}"
    )

    # 执行顺序必须是完整的 k1..k20
    order = [e.split("@")[0] for e in d["s12_exec_order"].split(",") if e]
    assert order == [f"press:k{i}" for i in range(1, 21)], (
        f"连招执行顺序被打断: {order}"
    )

    # 带间隔的连招:1 + 8×(delay + key) = 17 个原子,同样是 1 个决策
    assert int(d["s12b_items"]) == 1
    assert int(d["s12b_atoms"]) == 17
    assert int(d["s12b_dropped"]) == 0


def test_global_budget_bounds_mixed_priority_backlog():
    """回归:出队是**严格优先级**的(high 空了才轮到 normal)。按队列各限 16,
    三条非紧急队列能同时积压 48 项,低优先级要等 ~750ms —— "≤250ms" 只在
    单一优先级下成立。预算必须是全局的。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    total = int(d["s14_total_nonemergency"])
    atoms = int(d["s14_atoms"])
    total_atoms = int(d["s14_total_atoms"])
    assert atoms <= MAX_PENDING_ATOMS, f"排队等待的原子 {atoms} 超过全局预算"
    assert total <= ABSOLUTE_BACKLOG_CEILING, (
        f"三条非紧急队列共积压 {total} 项 —— 预算又变成按队列各算了"
        f"(high={d['s14_high']} normal={d['s14_normal']} low={d['s14_low']})"
    )
    # 绝对总量 = 预算 + 正在执行的那个决策(这里都是单原子动作,所以 +1)
    assert total_atoms <= MAX_PENDING_ATOMS + 1
    print(f"  [实测] 混合优先级积压 {total} 项 / 待发 {total_atoms} 原子 "
          f"(按队列各限 16 时是 48 项 ≈ 750ms)")


def test_starved_low_priority_is_dropped_not_left_to_rot():
    """严格优先级下,持续的高优先级生产会让低优先级永远排不上。
    既定语义不变(高优先级就是该赢),但真实等了 1 秒的低优先级动作在终于
    轮到它时必须**被丢弃并上报**,而不是执行一个 1.5 秒前的决策。
    判定在出队时做(懒判定):没轮到之前留在队列里,预算没超、占位无害。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s15_low_backlog_during"]) == 1, "懒判定下饥饿期间不该动它"
    assert int(d["s15_low_executed"]) == 0, (
        "真实等了 1 秒的低优先级动作被执行了 —— 过期决策打了出去"
    )
    assert int(d["s15_low_backlog"]) == 0
    assert int(d["s15_dropped"]) > 0, "发生了饥饿丢弃却没有任何上报"
    assert int(d["s15_atoms"]) <= MAX_PENDING_ATOMS


def test_age_is_bound_to_items_not_queues():
    """回归(计龄状态未绑定决策的两个实际错误):

    (a) 旧的每队列计数在清队列后不重置 —— low 等 31 tick → PAUSED 清队 →
        新 low 只等 1 tick 就"继承"旧年龄被丢。年龄绑在项上后,新项从 0 起算。
    (b) 旧计数只描述队首 —— 丢掉前两个后清零,排在后面、等了 64 tick 的
        第 3 个照样被执行。按项判后,16 个 stale 全部被丢,一个都不执行。
    """
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    # (a) 清队列后新入队的动作正常执行,不继承旧年龄
    assert int(d["s18_fresh_executed"]) == 1, "清队列后的新动作继承了旧年龄被丢"
    assert int(d["s18_old_executed"]) == 0   # 旧动作已被 ClearQueue(-1) 清掉
    # (b) 真实等了 1 秒的 16 个动作全部按自己的年龄被丢,零执行
    assert int(d["s19_stale_executed"]) == 0, (
        f"{d['s19_stale_executed']} 个真实等了 1 秒的动作仍被执行 —— 年龄没绑在项上"
    )
    assert int(d["s19_low_backlog"]) == 0
    assert int(d["s19_dropped"]) >= 16
    # 入队时记录的时间戳必须就是"现在"(否则上面的年龄判定全部建立在假数字上)
    delta = int(d["s19b_at_delta"])
    assert 0 <= delta < 200, f"QueueItem 记录的入队时刻偏离当前 {delta}ms"


def test_repeated_sequence_is_complete_every_run():
    """HIGH 回归:ExecuteAction 曾对 CachedStrSplit 返回的**共享缓存数组**原地
    RemoveAt —— 同一条序列第二次执行拿到残骸:第一次 q,w、第二次只剩 w、
    第三次什么都不发。本测试用**真实** CachedStrSplit(旧桩每次新建数组,
    正好把这个 BUG 挡在了测试之外)。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    for run in (1, 2, 3):
        actions = [e.split("@")[0] for e in d[f"s20_run{run}"].split(",") if e]
        assert actions == ["press:q", "press:w"], (
            f"第 {run} 次执行 sequence:q,w 发出的是 {actions} —— 共享缓存数组被啃了"
        )


def test_budget_converges_with_multiple_inflight_seqruns():
    """回归:seqrun 恒不可丢,而不同优先级可以**同时**各有一条在飞
    (normal 长序列开打后被 high 序列抢占)。只豁免全局队首的话,被抢占那条的
    剩余原子把预算永久顶爆 —— 两条都不可丢、无法收敛,所有新动作一到就被丢。
    每条队列队首的 seqrun 都要豁免(结构上每队列最多一条,豁免总量有界)。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s21_budget_with_two_seqruns"]) == 0, (
        f"两条在飞 seqrun 下预算口径 = {d['s21_budget_with_two_seqruns']},"
        f"被抢占序列的剩余原子仍被计入 —— 预算无法收敛"
    )
    assert int(d["s21_dropped_at_enqueue"]) == 0, "预算顶爆导致新动作一到就被丢"
    assert int(d["s21_extra_survived"]) == 5
    # 两条序列都必须完整走完(seqrun 不可丢 + 各在自己队列推进)
    assert int(d["s21_h_completed"]) == 40, f"high 序列只完成 {d['s21_h_completed']}/40"
    assert int(d["s21_n_completed"]) == 40, f"normal 序列只完成 {d['s21_n_completed']}/40"


def test_started_sequence_survives_starvation():
    """已经开打的连招不能被"饥饿丢弃"抹掉后半段 —— 打出前两个键就停手,
    比迟发整条更糟。未开始的 sequence: 仍可整条丢(原子性),
    开打后变成 seqrun: 就进入不可丢集合。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s17_head_after_first"] == "seqrun:", (
        f"连招开打后没有标记成 seqrun:,而是 {d['s17_head_after_first']}"
    )
    assert int(d["s17_seqrun_survived"]) == 1, "已开打的连招在饥饿中被丢掉了后半段"
    # 队列里唯一的项就是那条已开打的连招,它不可丢 → 计龄不该误伤任何东西
    assert int(d["s17_dropped_something"]) == 0, (
        "饥饿计龄丢掉了不可丢的队首(已开打的连招)"
    )
    tail = [e.split("@")[0] for e in d["s17_exec_tail"].split(",") if e]
    assert tail[:1] == ["press:c1"], f"连招首键不对: {tail[:1]}"
    assert tail[-3:] == ["press:c2", "press:c3", "press:c4"], (
        f"高优先级停产后连招没有按序打完: {tail[-3:]}"
    )


def test_sequence_is_dropped_whole_under_overload():
    """过载时序列整体被丢,不留半截 —— 打出连招的前三个键就停手比不打更糟。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    left = int(d["s16_sequence_items_left"])
    assert left in (0, 1), f"队列里出现了 {left} 个序列项(半截连招?)"
    assert int(d["s16_atoms"]) <= MAX_PENDING_ATOMS
    # 总量上界 = 预算 + 最长的单个决策(这里是 8 原子的连招)
    assert int(d["s16_total_atoms"]) <= MAX_PENDING_ATOMS + 8


def test_newest_arrival_is_never_the_one_dropped():
    """回归:队头堆满不可丢动作(30 个 release:)时,唯一"可丢"的就是刚到达的那个。
    修复前每个新 press 都在到达时被丢掉 —— 30 个全军覆没,一个都执行不到,
    策略从"丢最旧"整个翻转成了"丢最新"。

    修复后:最新到达的永远留下(较早的 press 仍按"丢最旧"策略被淘汰,这是对的 ——
    队列已被 30 个 release 顶到 1 秒延迟,只有最新决策还反映当前局面)。
    """
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    survived = [s for s in d["s13_press_survived"].split(",") if s]
    assert survived, "所有新动作都被丢了 —— 退化成了丢最新"
    assert survived[-1] == "press:p30", (
        f"最新到达的动作没能存活,队列里的是 {survived}"
    )
    # release: 一个都不能少(丢了就是游戏内卡键)
    assert int(d["s13_releases_kept"]) - len(survived) == 30


def test_all_undroppable_queue_exceeds_depth_rather_than_stick_keys():
    """整条队列都是不可丢动作时,宁可暂时超深也不丢 ——
    不能为了满足预算去制造卡键。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s10_all_release_backlog"]) == 40, "为了限深丢掉了 release:"
    assert int(d["s10_dropped"]) == 0


if __name__ == "__main__":
    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"ALL {len(tests)} PASSED")
