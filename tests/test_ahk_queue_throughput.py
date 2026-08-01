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
    "EnforceQueueDepth",
    "DropOldestDroppable",
    "IsDroppableAction",
    "NotifyQueueOverload",
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
global QueueStats := Map("emergency", 0, "high", 0, "normal", 0, "low", 0, "processed", 0, "dropped", 0)
global MAX_QUEUE_DEPTH := __MAX_QUEUE_DEPTH__
global LastOverloadNotifyAt := 0
global PendingOverloadNotify := false
global OverloadNotifications := []
global IsPaused := false
global SpecialKeysPaused := false
global RuntimeAcceptingActions := true
global DelayUntil := 0
global DelayClearOthers := false
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
SendEventToPython(data) {
    global OverloadNotifications
    OverloadNotifications.Push(data)
}
IsEmergencyAction(action) {
    ; 与实现一致的判定形态:emergency 队列里的 press:hp/mp 属于救命动作
    return InStr(action, "press:hp") = 1 || InStr(action, "press:mp") = 1
}
CachedStrSplit(str, delim, omit := "", max := -1) {
    if (max > 0) {
        return StrSplit(str, delim, omit, max)
    }
    return StrSplit(str, delim, omit)
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
    global DelayUntil, DelayClearOthers, ExecLog, CurrentTick
    global PendingOverloadNotify, LastOverloadNotifyAt, OverloadNotifications
    global MAX_QUEUE_DEPTH
    PendingOverloadNotify := false
    LastOverloadNotifyAt := 0
    OverloadNotifications := []
    MAX_QUEUE_DEPTH := __MAX_QUEUE_DEPTH__   ; 取自实现;个别场景会显式抬高以测基线行为
    EmergencyQueue := []
    HighQueue := []
    NormalQueue := []
    LowQueue := []
    QueueCounts := Map("emergency", 0, "high", 0, "normal", 0, "low", 0)
    TotalQueueCount := 0
    QueueStats := Map("emergency", 0, "high", 0, "normal", 0, "low", 0, "processed", 0, "dropped", 0)
    IsPaused := false
    SpecialKeysPaused := false
    RuntimeAcceptingActions := true
    DelayUntil := 0
    DelayClearOthers := false
    ExecLog := []
    CurrentTick := 0
}

global Results := Map()
Record(name, value) {
    global Results
    Results[name] := value
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
MAX_QUEUE_DEPTH := 1000000   ; 测的是排空速率本身,先让深度上限不参与
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
; S2 序列放大:一次 send_sequence 展开成 N 个队列项,各占一个 tick
; =====================================================================
ResetAll()
EnqueueAction(2, "sequence:q,w,e")
Record("s2_items_for_3key_sequence", TotalQueueCount)
ResetAll()
EnqueueAction(2, "sequence:q,delay100,w")
Record("s2_items_with_delay", TotalQueueCount)

; =====================================================================
; S3 delay 是**全局**队头阻塞:低优先级的 delay 会压住高优先级动作
; =====================================================================
ResetAll()
EnqueueAction(3, "delay:100")        ; low 优先级的一个 delay
Tick()                                ; 取出 delay,设定全局 DelayUntil
EnqueueAction(1, "press:high_skill") ; 之后到达的 high 优先级技能
Tick()                                ; 仍在 delay 窗口内 → 被压住
Record("s3_exec_during_delay", JoinLog())
Record("s3_high_blocked_by_low_delay", ExecLog.Length = 0 ? 1 : 0)
Sleep(120)                            ; delay 过期
Tick()
Record("s3_after_delay", JoinLog())

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
    if (InStr(a, "release:") = 1) {
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
    if (InStr(a, "cleanup:") = 1) {
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
; S12 长连招不得被深度上限"自己吃掉自己":一次 sequence 是一个决策,
;     整个展开在同一次调用里完成,ProcessQueue 中途一个也排不出去。
; =====================================================================
ResetAll()
seq := "sequence:k1"
loop 19 {
    seq .= ",k" (A_Index + 1)
}
EnqueueAction(2, seq)                       ; 20 键连招,空队列,零过载
Record("s12_items", QueueCounts["normal"])
Record("s12_dropped", QueueStats["dropped"])
Record("s12_head", NormalQueue.Length > 0 ? NormalQueue[1] : "")

; 带间隔的连招(delay 也算原子):"delay50,1,delay100,2,..." 形态
ResetAll()
seq2 := "sequence:1"
loop 8 {
    seq2 .= ",delay100,"  (A_Index + 1)
}
EnqueueAction(2, seq2)
Record("s12b_items", QueueCounts["normal"])
Record("s12b_dropped", QueueStats["dropped"])
Record("s12b_head", NormalQueue.Length > 0 ? NormalQueue[1] : "")

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
    if (InStr(a, "press:") = 1) {
        survived .= (survived = "" ? "" : ",") a
    }
}
Record("s13_press_survived", survived)
Record("s13_releases_kept", QueueCounts["normal"])


; =====================================================================
; S5 紧急动作不被积压饿死(救命药剂必须插队)
; =====================================================================
ResetAll()
MAX_QUEUE_DEPTH := 1000000   ; 要的是"深积压"场景,先让深度上限不参与
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
MAX_QUEUE_DEPTH := 1000000
loop 50 {
    EnqueueAction(1, "press:h" A_Index)
}
Tick()
Record("s6_exec_in_one_tick", ExecLog.Length)

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
    parts = [_STUBS.replace("__MAX_QUEUE_DEPTH__", str(MAX_QUEUE_DEPTH))]
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
    永远绿。(这里就踩过:硬编码 MAX_QUEUE_DEPTH=16 的桩让"取消深度上限"
    这个变异完全没被发现。)
    """
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        src = fp.read()
    m = re.search(rf"global {re.escape(name)}\s*:=\s*(\d+)", src)
    assert m, f"未能在 hold_server_extended.ahk 中找到常量 {name}"
    return int(m.group(1))


TICK_MS = _ahk_constant("QUEUE_TICK_MS")
MAX_QUEUE_DEPTH = _ahk_constant("MAX_QUEUE_DEPTH")
# Windows 时钟粒度 ~15.6ms,请求 15ms 落在 1 个系统 tick 上 → 实测 ~15.8ms ≈ 63/s。
# (请求 20ms 会被凑成 2 个系统 tick = 31.6ms,只有一半吞吐 —— 这正是本次改动的原因。)
# 低于这个下限说明定时器被拖慢,队列会长期过载。
MIN_ACCEPTABLE_RATE = 45.0

# 绝对预算(**不**跟随上面两个常量):这才是用户真正拿到的保证。
# 相对断言(backlog <= MAX_QUEUE_DEPTH)在"有人把上限调到 100 万"时照样成立,
# 测不出"深度上限被取消"这件事 —— 所以必须另有一条绝对上界。
ABSOLUTE_BACKLOG_CEILING = 32          # 2 × 文档里的深度上限
ABSOLUTE_LAG_CEILING_MS = 500          # 修复前是 10000ms 且随时间线性增长

# SetTimer 走 Windows 消息定时器,分辨率受系统时钟粒度(默认 ~15.6ms)限制:
# SetTimer(..., 20) 实际会被凑到 2 个系统 tick ≈ 31.2ms,而不是 20ms。
# 别的进程把全局定时器分辨率调高时又会靠近 20ms。所以吞吐上限是一个**区间**,
# 必须真量,不能拿常数算 —— 这正是"需压力测试"的地方。
_TIMER_PROBE = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off
Persistent

global Fires := 0
global StartAt := A_TickCount
global OutFile := A_Args.Length >= 1 ? A_Args[1] : (A_ScriptDir "\timer.txt")

Fire() {
    global Fires
    Fires += 1
}
Finish() {
    global Fires, StartAt, OutFile
    elapsed := A_TickCount - StartAt
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
    latency_budget = MAX_QUEUE_DEPTH * period
    print(f"  [实测] SetTimer({TICK_MS}) 真实周期 {period:.1f}ms → 吞吐上限 "
          f"{per_sec:.1f} 动作/秒(改动前:请求 20ms → 31.6ms → 31.7/s)")
    print(f"  [实测] 深度上限 {MAX_QUEUE_DEPTH} 项 → 最坏排队延迟 {latency_budget:.0f}ms")


def test_single_tick_emits_at_most_one_action():
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s6_exec_in_one_tick"]) == 1, (
        "一个 tick 执行了多个动作 —— 吞吐模型变了"
    )


def test_sequence_amplifies_into_one_item_per_key():
    """一次 send_sequence 在入队处展开:3 键序列 = 3 个队列项 = 3 个 tick = 60ms。
    生产侧按"一个技能一次调度"计费,消费侧按"一个动作一个 tick"计费 ——
    这个放大系数是过载最容易被忽视的来源。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s2_items_for_3key_sequence"]) == 3
    assert int(d["s2_items_with_delay"]) == 3
    print(f"  [实测] 'q,w,e' 展开为 3 项 → 至少 {3 * TICK_MS}ms 才能发完")


def test_low_priority_delay_blocks_high_priority():
    """已知的队头阻塞:DelayUntil 是**全局**的,一个低优先级 delay 会把
    之后的高优先级技能一起压住(紧急队列除外)。优先级在 delay 面前不成立。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert d["s3_high_blocked_by_low_delay"] == "1", (
        f"delay 期间执行了动作: {d['s3_exec_during_delay']}"
    )
    # 只是被推迟,不是被丢弃 —— delay 过期后仍会执行
    assert d["s3_after_delay"].startswith("press:high_skill@"), (
        f"delay 过期后 high 动作没有恢复执行: {d['s3_after_delay']}"
    )
    print("  [实测] low 优先级的 delay:100 会阻塞 high 优先级技能约 100ms")


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
    assert MAX_QUEUE_DEPTH == 16, (
        f"MAX_QUEUE_DEPTH 变成了 {MAX_QUEUE_DEPTH};延迟上限 = 深度 × 实测周期,请重算文档"
    )
    assert TICK_MS == 15, (
        f"QUEUE_TICK_MS 变成了 {TICK_MS};注意 20 会被 Windows 凑成 31.6ms、吞吐腰斩"
    )


def test_overload_backlog_stays_bounded():
    """过载后果的量化。生产速率 = 消费速率的 2 倍,跑 500 个 tick:

    修复前:队列无上界 → 积压 500 项,正在打出去的是 10 秒前决策的按键,
            而且随时间线性恶化,不报任何错(表现为"技能乱放")。
    修复后:深度上限把积压钉在 16 项以内 → 排队延迟恒 ≤250ms,
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
    assert backlog <= MAX_QUEUE_DEPTH, f"积压 {backlog} 项,超过深度上限"
    # ⚠️ 绝对上界,不跟着常量走:上面那条断言在"有人把上限调到 100 万"时同样成立
    # (相对断言测不出"上限被取消"),而用户真正拿到的保证是绝对的毫秒数。
    assert backlog <= ABSOLUTE_BACKLOG_CEILING, (
        f"积压 {backlog} 项 —— 深度上限形同虚设(队列又变回无界了?)"
    )
    assert produced == executed + backlog + dropped, (
        f"动作账不平: 生产 {produced} != 执行 {executed} + 积压 {backlog} + 丢弃 {dropped}"
    )

    # 最后执行的动作是第几个入队的 → 实际排队延迟
    last = d["s4_last_executed"]          # 形如 "press:a984@500"
    idx = int(re.search(r"press:a(\d+)@", last).group(1))
    lag_ms = (produced - 1 - idx) * TICK_MS
    assert lag_ms <= MAX_QUEUE_DEPTH * TICK_MS, (
        f"排队延迟 {lag_ms}ms 超过深度上限对应的 {MAX_QUEUE_DEPTH * TICK_MS}ms"
    )
    assert lag_ms <= ABSOLUTE_LAG_CEILING_MS, (
        f"排队延迟 {lag_ms}ms 超过绝对预算 {ABSOLUTE_LAG_CEILING_MS}ms —— "
        f"这才是用户拿到的保证(修复前是 10000ms 且持续增长)"
    )
    print(f"  [实测] 10 秒过载后:积压 {backlog} 项(上限 {MAX_QUEUE_DEPTH}),"
          f"丢弃 {dropped} 项,排队延迟 {lag_ms}ms(修复前是 10000ms 且持续增长)")


def test_overload_is_reported_not_silent():
    """静默丢弃会让用户以为是技能配置问题,必须上报。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s4_overload_notifications"]) >= 1, "过载被静默丢弃,没有任何上报"
    assert d["s4_first_notification"].startswith("queue_overload:"), (
        f"上报事件格式不对: {d['s4_first_notification']}"
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
    assert int(d["s7_backlog"]) <= MAX_QUEUE_DEPTH + 1


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


def test_long_sequence_is_not_truncated_by_depth_cap():
    """回归:一次 sequence 是**一个决策**,其原子有因果关系。整个展开在同一个
    WM_COPYDATA 调用里完成,ProcessQueue 中途一个也排不出去 —— 所以逐项限深会让
    超过 16 个原子的连招在**队列全空、零过载**时就自己吃掉自己的前半段。"""
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    d = _measure()
    assert int(d["s12_items"]) == 20, (
        f"20 键连招只剩 {d['s12_items']} 项 —— 被深度上限截断了"
    )
    assert int(d["s12_dropped"]) == 0
    assert d["s12_head"] == "press:k1", f"连招首键被丢: 队头是 {d['s12_head']}"

    # 带间隔的连招:1 + 8×(delay + key) = 17 个原子
    assert int(d["s12b_items"]) == 17
    assert int(d["s12b_dropped"]) == 0
    assert d["s12b_head"] == "press:1", (
        f"带间隔连招的首键被丢,队头成了孤立的 delay: {d['s12b_head']}"
    )


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
    不能为了满足深度上限去制造卡键。"""
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
