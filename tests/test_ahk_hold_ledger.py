#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK 端持键账本的行为测试(跑**真实** AHK 代码,不是重写一份)。

做法:按函数名从 hold_server_extended.ahk 原文逐字抽取被测函数,拼上桩函数
(把 Send 换成记录到日志)后交给 AutoHotkey.exe 执行,再断言发出的按键事件序列。
因为被测函数是原文抽取的,测试不会与实现漂移。

覆盖 TriggerMode=2 声明式持键的核心不变量:
  (a) 幂等   (b) LIFO 释放   (c) 按下发顺序补按   (d) 只在真正发出 down 后记账
  (e) 抑制只推迟 down、释放永不推迟   (f) 抑制解除后补按
  (g) 队列动作(管理键 release:/press:)与持久持键同名时的账本修正
  (h) 安全收尾释放(含队列级临时持键 ManagedHoldTargets)

无 AutoHotkey v2 时自动跳过。
"""

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

# 从原文抽取的被测函数(顺序无关,AHK 函数可前向引用)
_EXTRACT = [
    "IsSkillHoldSuppressed",   # 真函数:承载闸门+抑制语义,不能桩
    "SetSkillHoldKeys",
    "ReconcileSkillHoldKeys",
    "ForgetSkillHeldKey",
    "ReleaseAllSkillHoldKeys",
    "MarkManagedHoldTarget",
    "ClearManagedHoldTarget",
    "ReleaseAllManagedHoldTargets",
    "ClearManagedKeyMark",
    "ExecuteAction",
    # 宏解释器一组:验证运行时闸门能压住 MacroTick
    "SetMacroSteps",
    "StartMacro",
    "StopMacro",
    "MacroTick",
    "TrackMacroDown",
    "TrackMacroUp",
    "ReleaseMacroHeldKeys",
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


# 桩:把物理发键换成事件记录;可控地模拟 block_mouse 吞键与抑制状态
_STUBS = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off

global KeyLog := []
global BlockMouseSim := false      ; 模拟 block_mouse 原地模式
global EmergencySim := false
global ResultFile := A_Args.Length >= 1 ? A_Args[1] : (A_ScriptDir "\out.txt")

; ---- 被测代码依赖的全局(与 hold_server_extended.ahk 同名同初值)----
global SkillHoldDesiredOrder := []
global SkillHeldKeys := Map()
global SkillHeldOrder := []
global ManagedHoldTargets := Map()
global ActiveManagedKeys := Map()
global SpecialKeysPaused := false
global RuntimeAcceptingActions := true
global DelayUntil := 0
global DelayClearOthers := false
global MacroSteps := []
global MacroActive := false
global MacroIndex := 1
global MacroDueTime := 0
global MacroHeldKeys := Map()
global MacroHeldOrder := []
global MacroSpecialSuppressed := false
global MacroManagedSuppressed := false
global ACTION_PRESS := "press"
global ACTION_HOLD := "hold"
global ACTION_RELEASE := "release"
global ACTION_SEQUENCE := "sequence"
global ACTION_CLEANUP := "cleanup"
global ACTION_MOUSE_CLICK := "click"
global ACTION_DELAY := "delay"
global ACTION_NOTIFY := "notify"
global ACTION_SEQ_RUNNING := "seqrun"

; ---- 桩函数(IsSkillHoldSuppressed 是真函数,从原文抽取,不在此列)----
ShouldBlockMouseInStationary(key) {
    global BlockMouseSim
    return BlockMouseSim && IsMouseButtonKeyStub(key)
}
IsMouseButtonKeyStub(key) {
    lower := StrLower(key)
    return (lower = "lbutton") || (lower = "rbutton") || (lower = "left") || (lower = "right")
}
SendDown(key) {
    global KeyLog
    if (ShouldBlockMouseInStationary(key)) {
        return false
    }
    KeyLog.Push("down:" key)
    return true
}
SendUp(key) {
    global KeyLog
    KeyLog.Push("up:" key)
    return true
}
SendPress(key, forceMoveBypass := false) {
    global KeyLog
    if (ShouldBlockMouseInStationary(key)) {
        return false
    }
    KeyLog.Push("press:" key)
    return true
}
CachedStrSplit(str, delim, omit := "", max := -1) {
    if (max > 0) {
        return StrSplit(str, delim, omit, max)
    }
    return StrSplit(str, delim, omit)
}
IsEmergencyAction(action) {
    global EmergencySim
    return EmergencySim
}
ExecuteMouseClick(data) {
}
; 序列推进会把剩余部分放回队首;本文件只测持键账本,不测队列,故记录即可
global PushedBack := []
PushFrontAction(priority, action) {
    global PushedBack
    PushedBack.Push(priority ":" action)
}
SendEventToPython(data) {
}
SetMacroManagedSuppressed(flag) {
}
MarkManagedKeyActiveStub(key) {
    global ActiveManagedKeys
    ActiveManagedKeys[key] := A_TickCount
}

; ---- 断言工具 ----
global Failures := []
global Checks := 0
Expect(label, actual, expected) {
    global Failures, Checks
    Checks += 1
    if (actual != expected) {
        Failures.Push(label ": 期望 [" expected "] 实际 [" actual "]")
    }
}
LogStr() {
    global KeyLog
    out := ""
    for i, e in KeyLog {
        out .= (i > 1 ? "," : "") e
    }
    return out
}
ResetAll() {
    global KeyLog, SkillHoldDesiredOrder, SkillHeldKeys, SkillHeldOrder
    global ManagedHoldTargets, ActiveManagedKeys, BlockMouseSim
    global SpecialKeysPaused, RuntimeAcceptingActions
    global MacroSteps, MacroActive, MacroIndex, MacroDueTime
    global MacroHeldKeys, MacroHeldOrder, MacroSpecialSuppressed, MacroManagedSuppressed
    KeyLog := []
    SkillHoldDesiredOrder := []
    SkillHeldKeys := Map()
    SkillHeldOrder := []
    ManagedHoldTargets := Map()
    ActiveManagedKeys := Map()
    BlockMouseSim := false
    SpecialKeysPaused := false
    RuntimeAcceptingActions := true
    MacroSteps := []
    MacroActive := false
    MacroIndex := 1
    MacroDueTime := 0
    MacroHeldKeys := Map()
    MacroHeldOrder := []
    MacroSpecialSuppressed := false
    MacroManagedSuppressed := false
}
"""

_SCENARIOS = r"""
; ============================ 场景 ============================

; (a) 幂等 + (c) 按下发顺序补按(鼠标键在前由 Python 排序保证)
ResetAll()
SetSkillHoldKeys("RButton`nq")
Expect("a1-首次按下", LogStr(), "down:RButton,down:q")
SetSkillHoldKeys("RButton`nq")
Expect("a2-重复下发不产生额外按键", LogStr(), "down:RButton,down:q")

; (b) LIFO 释放不再期望的键
ResetAll()
SetSkillHoldKeys("RButton`nq`ne")
Expect("b1", LogStr(), "down:RButton,down:q,down:e")
SetSkillHoldKeys("RButton")
Expect("b2-逆序释放 e 再 q", LogStr(), "down:RButton,down:q,down:e,up:e,up:q")

; (e) 抑制期(特殊键按住):释放立即执行,新增 down 被推迟
ResetAll()
SetSkillHoldKeys("q")
Expect("e1", LogStr(), "down:q")
SpecialKeysPaused := true
SetSkillHoldKeys("RButton")          ; q 不再期望 → 立即释放;RButton 新增 → 推迟
Expect("e2-抑制期释放不推迟、down 推迟", LogStr(), "down:q,up:q")
; (f) 抑制解除后补按
SpecialKeysPaused := false
ReconcileSkillHoldKeys()
Expect("f1-抑制解除后补按", LogStr(), "down:q,up:q,down:RButton")

; (d) block_mouse 吞掉 down → 不记账;关闭后 Reconcile 能补按
ResetAll()
BlockMouseSim := true
SetSkillHoldKeys("RButton`nq")
Expect("d1-鼠标键被吞、键盘键正常", LogStr(), "down:q")
Expect("d2-被吞的键未记账", SkillHeldKeys.Has("RButton") ? "yes" : "no", "no")
BlockMouseSim := false
ReconcileSkillHoldKeys()
Expect("d3-关闭原地模式后补按", LogStr(), "down:q,down:RButton")

; (g) 核心修复:管理键 hold_ms 的 release:X 与持久持键同名
ResetAll()
SetSkillHoldKeys("RButton")
Expect("g1", LogStr(), "down:RButton")
MarkManagedKeyActiveStub("RButton")           ; 管理键序列开始(抑制生效)
ExecuteAction("hold:RButton")
ExecuteAction("release:RButton")              ; 物理抬起持久持键
Expect("g2-抑制期内不立刻补按", LogStr(), "down:RButton,down:RButton,up:RButton")
Expect("g3-账本已失忆", SkillHeldKeys.Has("RButton") ? "yes" : "no", "no")
ExecuteAction("cleanup:RButton")              ; → ClearManagedKeyMark → Reconcile
Expect("g4-管理键序列结束后补按", LogStr(), "down:RButton,down:RButton,up:RButton,down:RButton")
Expect("g5-队列级临时持键已注销", ManagedHoldTargets.Count, 0)

; (g') hold_ms=0 分支:press:X 同样抬起持久持键,且未抑制时立即补按
ResetAll()
SetSkillHoldKeys("q")
ExecuteAction("press:q")
Expect("g6-press 后立即补按", LogStr(), "down:q,press:q,down:q")

; (g'') 与持久持键无关的 press 不应触碰账本
ResetAll()
SetSkillHoldKeys("q")
ExecuteAction("press:1")
Expect("g7-无关按键不触发补按", LogStr(), "down:q,press:1")

; (g''') legacy 裸动作(无 "press:" 前缀)与持久持键同名时同样要修账本
ResetAll()
SetSkillHoldKeys("q")
ExecuteAction("q")
Expect("g8-裸键也触发补按", LogStr(), "down:q,press:q,down:q")

; (h) 队列级临时持键:清队列丢弃 release:target 时必须补 up
ResetAll()
ExecuteAction("hold:e")
Expect("h1", LogStr(), "down:e")
Expect("h2-无返回值时不谎报", ReleaseAllManagedHoldTargets() ? "forgot" : "none", "none")
Expect("h3-补发被丢弃的 up", LogStr(), "down:e,up:e")
Expect("h4-账本清空", ManagedHoldTargets.Count, 0)

; (h') 队列级临时持键与持久持键同名:补发的 up 必须同步技能账本
; (对应 ClearQueue(0) 只清 emergency 的场景:持久持键仍应保持 → 需要补按)
ResetAll()
SetSkillHoldKeys("RButton")
ExecuteAction("hold:RButton")
Expect("h5", LogStr(), "down:RButton,down:RButton")
Expect("h6-返回已失忆", ReleaseAllManagedHoldTargets() ? "forgot" : "none", "forgot")
Expect("h7-账本已同步", SkillHeldKeys.Has("RButton") ? "yes" : "no", "no")
ReconcileSkillHoldKeys()
Expect("h8-补按持久持键", LogStr(), "down:RButton,down:RButton,up:RButton,down:RButton")

; (h) 安全收尾:LIFO 全释放且清空期望,之后 Reconcile 不得重按
ResetAll()
SetSkillHoldKeys("RButton`nq")
ReleaseAllSkillHoldKeys()
Expect("h9-LIFO 全释放", LogStr(), "down:RButton,down:q,up:q,up:RButton")
ReconcileSkillHoldKeys()
Expect("h10-清空后不重按", LogStr(), "down:RButton,down:q,up:q,up:RButton")

; 空 payload = 释放全部
ResetAll()
SetSkillHoldKeys("q")
SetSkillHoldKeys("")
Expect("i1-空集合释放全部", LogStr(), "down:q,up:q")

; (j) 运行时闸门视同抑制:关闸期间释放照常、down 被压住;开闸 Reconcile 补按
; (协议层对非空集合是直接拒绝的;这里直接调 SetSkillHoldKeys 测的是防御第二层)
ResetAll()
SetSkillHoldKeys("q`ne")
Expect("j1", LogStr(), "down:q,down:e")
RuntimeAcceptingActions := false
SetSkillHoldKeys("q`nRButton")       ; e 不再期望 → 立即释放;RButton 新增 → 压住
Expect("j2-关闸期间释放不受影响、down 压住", LogStr(), "down:q,down:e,up:e")
RuntimeAcceptingActions := true
ReconcileSkillHoldKeys()
Expect("j3-开闸后补按", LogStr(), "down:q,down:e,up:e,down:RButton")

; (k) 运行时闸门压住宏解释器:关闸后 MacroTick 一键都不许再发
ResetAll()
SetMacroSteps("down:x`npress:y`nup:x")
StartMacro()
MacroTick()
Expect("k1-宏正常推进", LogStr(), "down:x")
RuntimeAcceptingActions := false     ; 只关闸,不 StopMacro —— 模拟关闸与清场之间的间隙
MacroTick()
MacroTick()
Expect("k2-关闸后 tick 静默", LogStr(), "down:x")
StopMacro()                          ; 清场:释放宏持键(up 永不被闸门拦截)
Expect("k3-停宏释放宏持键", LogStr(), "down:x,up:x")
RuntimeAcceptingActions := true
MacroTick()
Expect("k4-MacroActive 已复位,开闸也不复活", LogStr(), "down:x,up:x")

; ============================ 汇总 ============================
report := "CHECKS=" Checks "`n"
if (Failures.Length = 0) {
    report .= "RESULT=OK`n"
} else {
    report .= "RESULT=FAIL`n"
    for i, f in Failures {
        report .= "FAIL " f "`n"
    }
}
try {
    FileDelete ResultFile
} catch {
}
FileAppend report, ResultFile, "UTF-8"
ExitApp Failures.Length = 0 ? 0 : 1
"""


def _build_harness() -> str:
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().split("\n")
    parts = [_STUBS, _SCENARIOS, "\n; ===== 以下为从 hold_server_extended.ahk 原文抽取的被测函数 =====\n"]
    for name in _EXTRACT:
        parts.append(_extract_function(lines, name))
        parts.append("")
    return "\n".join(parts)


def test_ahk_skill_hold_ledger_invariants():
    """在真实 AHK 解释器里跑账本状态机,断言发出的按键事件序列。"""
    harness = _build_harness()
    tmpdir = tempfile.mkdtemp(prefix="pyahk_ledger_")
    script = os.path.join(tmpdir, "ledger_harness.ahk")
    result = os.path.join(tmpdir, "result.txt")
    with open(script, "w", encoding="utf-8") as fp:
        fp.write(harness)

    proc = subprocess.run(
        [AHK_EXE, "/ErrorStdOut", script, result],
        capture_output=True,
        timeout=60,
    )
    stderr = proc.stderr.decode("utf-8", "replace").strip()
    assert os.path.isfile(result), f"AHK 未产出结果文件 (exit={proc.returncode}): {stderr}"
    with open(result, encoding="utf-8") as fp:
        report = fp.read()

    assert "RESULT=OK" in report, f"AHK 持键账本不变量失败:\n{report}\n{stderr}"
    assert proc.returncode == 0, f"exit={proc.returncode}\n{report}"


def test_ahk_server_script_syntax_is_valid():
    """hold_server_extended.ahk 必须能通过 AHK v2 加载期语法校验。"""
    proc = subprocess.run(
        [AHK_EXE, "/ErrorStdOut", "/validate", AHK_SCRIPT],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


if __name__ == "__main__":
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        sys.exit(0)
    fns = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    for name, fn in fns:
        fn()
        print("PASS", name)
    print(f"ALL {len(fns)} PASSED")
