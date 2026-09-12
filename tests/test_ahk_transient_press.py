#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK 非阻塞 press 账本的真实解释器行为测试。"""

import os
import re
import subprocess
import tempfile


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AHK_SCRIPT = os.path.join(REPO, "hold_server_extended.ahk")
_AHK_CANDIDATES = [
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
]


def _find_ahk():
    return next((path for path in _AHK_CANDIDATES if os.path.isfile(path)), None)


def _extract_function(lines, name):
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(rf"^{re.escape(name)}\(.*\)\s*\{{\s*$", line)
        ),
        None,
    )
    if start is None:
        raise AssertionError(f"未找到 AHK 函数 {name}")
    depth = 0
    output = []
    for line in lines[start:]:
        output.append(line)
        code = re.sub(r";.*$", "", line)
        depth += code.count("{") - code.count("}")
        if depth == 0:
            return "\n".join(output)
    raise AssertionError(f"AHK 函数 {name} 花括号不配对")


_STUBS = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off

global FakeNow := 100
global KeyPressDurationMs := 50
global TransientPressKeys := Map()
global TransientPressOrder := []
global PersistentPressRoutes := Map()
global SkillHeldKeys := Map()
global SkillHeldOrder := []
global EdgeLog := []
global ReconcileCalls := 0
global FailControlKey := ""
global DirectAllowed := true
global SendKeyMode := "direct"
global TargetWin := ""
global ResultFile := A_Args[1]

MonotonicMs() {
    global FakeNow
    return FakeNow
}

CachedStrLower(value) {
    return StrLower(value)
}

ReconcileSkillHoldKeys() {
    global ReconcileCalls
    ReconcileCalls += 1
}

SendTransientKeyEdge(mode, target, key, isDown) {
    global EdgeLog, FailControlKey
    edge := isDown ? "down" : "up"
    if (mode = "control" && isDown && key = FailControlKey) {
        return false
    }
    EdgeLog.Push(mode ":" target ":" key ":" edge)
    return true
}

CanEmitDirectInput(target := "") {
    global DirectAllowed
    return DirectAllowed
}

ShouldBlockMouseInStationary(key) {
    return false
}

Join(values) {
    result := ""
    for index, value in values {
        result .= (index > 1 ? "," : "") value
    }
    return result
}

Expect(label, actual, expected) {
    global Checks, Failures
    Checks += 1
    if (actual != expected) {
        Failures.Push(label ": expected [" expected "] actual [" actual "]")
    }
}

ResetProbe() {
    global FakeNow, KeyPressDurationMs, TransientPressKeys, TransientPressOrder
    global PersistentPressRoutes, SkillHeldKeys, SkillHeldOrder, SendKeyMode, TargetWin
    global EdgeLog, ReconcileCalls, FailControlKey, DirectAllowed
    SetTimer(ReleaseDueTransientPressKeys, 0)
    FakeNow := 100
    KeyPressDurationMs := 50
    TransientPressKeys := Map()
    TransientPressOrder := []
    PersistentPressRoutes := Map()
    SkillHeldKeys := Map()
    SkillHeldOrder := []
    EdgeLog := []
    ReconcileCalls := 0
    FailControlKey := ""
    DirectAllowed := true
    SendKeyMode := "direct"
    TargetWin := ""
}

global Checks := 0
global Failures := []
"""


_SCENARIOS = r"""
ResetProbe()
usedDirect := false
Expect("plain-start", StartTransientPress("q", "direct", "", &usedDirect) ? 1 : 0, 1)
Expect("plain-is-direct", usedDirect ? 1 : 0, 1)
Expect("plain-down-now", Join(EdgeLog), "direct::q:down")
Expect("plain-ledger", TransientPressKeys.Count, 1)
FakeNow := 149
ReleaseDueTransientPressKeys()
Expect("plain-not-early", Join(EdgeLog), "direct::q:down")
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("plain-up-at-due", Join(EdgeLog), "direct::q:down,direct::q:up")
Expect("plain-ledger-empty", TransientPressKeys.Count, 0)
Expect("plain-reconcile-after-up", ReconcileCalls, 1)

; 重叠 press 每次都发 down，但早到的 release 不能剪断后一次保持窗口。
ResetProbe()
StartTransientPress("q", "direct", "", &usedDirect)
FakeNow := 120
StartTransientPress("q", "direct", "", &usedDirect)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("overlap-keeps-latest", Join(EdgeLog), "direct::q:down,direct::q:down")
FakeNow := 170
ReleaseDueTransientPressKeys()
Expect("overlap-one-final-up", Join(EdgeLog), "direct::q:down,direct::q:down,direct::q:up")

; base 先以普通 press 出现、后又进入 Shift chord 时，仍要先抬 base。
ResetProbe()
StartTransientPress("q", "direct", "", &usedDirect)
StartTransientPress("+q", "direct", "", &usedDirect)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("overlap-chord-releases-base-first", Join(EdgeLog),
    "direct::q:down,direct::Shift:down,direct::q:down,direct::q:up,direct::Shift:up")

; modifier 按下按正序，释放按 LIFO，与普通键共用同一 duration。
ResetProbe()
StartTransientPress("+1", "direct", "", &usedDirect)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("shift-chord", Join(EdgeLog),
    "direct::Shift:down,direct::1:down,direct::1:up,direct::Shift:up")

; 临时 chord 的 modifier up 也会剪断同名持久持键。配置键名为小写，
; ParseTransientPressKeys 输出 Shift，账本污染必须不区分大小写且覆盖全部 chord 键。
ResetProbe()
SkillHeldKeys["shift"] := true
SkillHeldOrder.Push("shift")
StartTransientPress("+1", "direct", "", &usedDirect)
Expect("held-modifier-forgotten", SkillHeldKeys.Count, 0)
Expect("held-modifier-order-forgotten", SkillHeldOrder.Length, 0)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("held-modifier-reconciled-after-up", ReconcileCalls, 1)

ResetProbe()
SkillHeldKeys["LButton"] := true
SkillHeldOrder.Push("LButton")
StartTransientPress("lbutton", "direct", "", &usedDirect)
Expect("held-base-forgotten-case-insensitive", SkillHeldKeys.Count, 0)

ResetProbe()
StartTransientPress("+", "direct", "", &usedDirect)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("literal-plus", Join(EdgeLog), "direct::+:down,direct::+:up")

ResetProbe()
Expect("text-alias-rejected", StartTransientPress("Ctrl+x", "direct", "", &usedDirect) ? 1 : 0, 0)
Expect("text-alias-no-edge", EdgeLog.Length, 0)

; ControlSend 成功不写全局键态，但仍必须使用同样的延迟 up。
ResetProbe()
StartTransientPress("^x", "control", "ahk_exe game.exe", &usedDirect)
Expect("control-not-global", usedDirect ? 1 : 0, 0)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("control-chord", Join(EdgeLog),
    "control:ahk_exe game.exe:Ctrl:down,control:ahk_exe game.exe:x:down,"
    "control:ahk_exe game.exe:x:up,control:ahk_exe game.exe:Ctrl:up")

; ControlSend 中途失败要先补齐已发的 up，然后整条回退 direct。
ResetProbe()
FailControlKey := "x"
StartTransientPress("^x", "control", "ahk_exe game.exe", &usedDirect)
Expect("fallback-is-global", usedDirect ? 1 : 0, 1)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("control-compensated-before-fallback", Join(EdgeLog),
    "control:ahk_exe game.exe:Ctrl:down,control:ahk_exe game.exe:Ctrl:up,"
    "direct::Ctrl:down,direct::x:down,direct::x:up,direct::Ctrl:up")

; 显式目标不在前台时，direct 与 ControlSend 的 direct fallback 都必须拒绝。
ResetProbe()
DirectAllowed := false
Expect("inactive-direct-rejected",
    StartTransientPress("q", "direct", "ahk_exe game.exe", &usedDirect) ? 1 : 0, 0)
Expect("inactive-direct-no-edge", EdgeLog.Length, 0)

ResetProbe()
DirectAllowed := false
FailControlKey := "x"
Expect("inactive-control-fallback-rejected",
    StartTransientPress("^x", "control", "ahk_exe game.exe", &usedDirect) ? 1 : 0, 0)
Expect("inactive-control-only-compensates", Join(EdgeLog),
    "control:ahk_exe game.exe:Ctrl:down,control:ahk_exe game.exe:Ctrl:up")

; 显式 down/up 必须沿同一路由配对；control 失败且目标不在前台时不许改投全局。
ResetProbe()
SendKeyMode := "control"
TargetWin := "ahk_exe game.exe"
Expect("persistent-control-down", SendDown("q") ? 1 : 0, 1)
Expect("persistent-control-route", PersistentPressRoutes.Count, 1)
Expect("persistent-control-up", SendUp("q") ? 1 : 0, 1)
Expect("persistent-control-edges", Join(EdgeLog),
    "control:ahk_exe game.exe:q:down,control:ahk_exe game.exe:q:up")

; press 后的显式 down 接管同一路由；旧计时器不能在持键期间插入 up。
for mode in ["direct", "control"] {
    ResetProbe()
    SendKeyMode := mode
    TargetWin := "ahk_exe game.exe"
    routeTarget := mode = "control" ? TargetWin : ""
    prefix := mode ":" routeTarget ":"
    StartTransientPress("q", mode, TargetWin, &usedDirect)
    FakeNow := 120
    Expect("takeover-down-" mode, SendDown("Q") ? 1 : 0, 1)
    FakeNow := 150
    ReleaseDueTransientPressKeys()
    Expect("takeover-no-early-up-" mode, Join(EdgeLog), prefix "q:down," prefix "Q:down")
    Expect("takeover-clears-transient-" mode, TransientPressKeys.Count, 0)
    Expect("takeover-clears-order-" mode, TransientPressOrder.Length, 0)
    ReleaseAllTransientPressKeys(false)
    Expect("takeover-still-held-" mode, EdgeLog.Length, 2)
    FakeNow := 620
    SendUp("q")
    Expect("takeover-final-up-" mode, Join(EdgeLog),
        prefix "q:down," prefix "Q:down," prefix "Q:up")
}

; 接管失败时，原 press 仍应按期释放。
ResetProbe()
StartTransientPress("q", "direct", "", &usedDirect)
DirectAllowed := false
Expect("failed-takeover", SendDown("q") ? 1 : 0, 0)
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("failed-takeover-still-releases", Join(EdgeLog), "direct::q:down,direct::q:up")

; 不同 control 目标的同名键不能互相接管。
ResetProbe()
StartTransientPress("q", "control", "old-target", &usedDirect)
SendKeyMode := "control"
TargetWin := "new-target"
SendDown("q")
FakeNow := 150
ReleaseDueTransientPressKeys()
SendUp("q")
Expect("takeover-keeps-other-target-release", Join(EdgeLog),
    "control:old-target:q:down,control:new-target:q:down,"
    "control:old-target:q:up,control:new-target:q:up")

; 显式修饰键持有者也能接管 chord 的临时 modifier，base 仍正常释放。
ResetProbe()
StartTransientPress("+q", "direct", "", &usedDirect)
SendDown("shift")
FakeNow := 150
ReleaseDueTransientPressKeys()
Expect("modifier-takeover-keeps-base-release", Join(EdgeLog),
    "direct::Shift:down,direct::q:down,direct::shift:down,direct::q:up")
ReleaseAllPersistentPressKeys()
Expect("modifier-takeover-cleanup", Join(EdgeLog),
    "direct::Shift:down,direct::q:down,direct::shift:down,direct::q:up,direct::shift:up")

ResetProbe()
SendKeyMode := "control"
TargetWin := "ahk_exe game.exe"
FailControlKey := "q"
DirectAllowed := false
Expect("persistent-unsafe-fallback-rejected", SendDown("q") ? 1 : 0, 0)
Expect("persistent-unsafe-fallback-no-global-edge", EdgeLog.Length, 0)

ResetProbe()
KeyPressDurationMs := 1000
startMs := DllCall("Kernel32\GetTickCount64", "UInt64")
StartTransientPress("q", "direct", "", &usedDirect)
elapsed := DllCall("Kernel32\GetTickCount64", "UInt64") - startMs
Expect("1000ms-duration-does-not-block", elapsed < 100 ? 1 : 0, 1)
ReleaseAllTransientPressKeys(false)
Expect("forced-release", Join(EdgeLog), "direct::q:down,direct::q:up")
Expect("forced-release-clears-ledger", TransientPressKeys.Count, 0)

report := "CHECKS=" Checks "`nRESULT=" (Failures.Length ? "FAIL" : "OK") "`n"
for index, failure in Failures {
    report .= "FAIL " failure "`n"
}
try FileDelete(ResultFile)
FileAppend(report, ResultFile, "UTF-8")
ExitApp Failures.Length ? 1 : 0
"""


def _build_harness():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    names = (
        "ForgetSkillHeldKey",
        "ParseTransientPressKeys",
        "TransientPressId",
        "IsTransientModifierKey",
        "TrackTransientPressKey",
        "StartTransientPress",
        "ScheduleTransientPressRelease",
        "ReleaseDueTransientPressKeys",
        "ReleaseAllTransientPressKeys",
        "IsTransientGlobalPressActive",
        "PersistentPressId",
        "TrackPersistentPressRoute",
        "ReleaseAllPersistentPressKeys",
        "SendDown",
        "SendUp",
    )
    return "\n".join([_STUBS, _SCENARIOS, *(_extract_function(lines, n) for n in names)])


def test_transient_press_ledger_with_real_ahk():
    ahk = _find_ahk()
    if ahk is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    with tempfile.TemporaryDirectory(prefix="pyahk_transient_press_") as temp_dir:
        script = os.path.join(temp_dir, "transient_press.ahk")
        result = os.path.join(temp_dir, "result.txt")
        with open(script, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(_build_harness())
        proc = subprocess.run(
            [ahk, "/ErrorStdOut", script, result],
            capture_output=True,
            timeout=30,
        )
        stderr = proc.stderr.decode("utf-8", "replace")
        assert os.path.isfile(result), f"AHK 未产生结果(exit={proc.returncode}): {stderr}"
        with open(result, encoding="utf-8-sig") as fp:
            report = fp.read()
        assert "RESULT=OK" in report, f"{report}\n{stderr}"
        assert proc.returncode == 0, f"exit={proc.returncode}\n{report}\n{stderr}"


def test_no_synchronous_sleep_remains_in_press_path():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    for name in ("SendPress", "SendKeyInternal", "SendDirect", "StartTransientPress"):
        assert "Sleep" not in _extract_function(lines, name)


def test_real_edge_sender_function_parses_in_ahk_v2():
    """主行为探针用 stub 避免影响真实键盘；这里单独让真实
    Send/ControlSend down-up 实现经过 AHK v2 解析器。"""
    ahk = _find_ahk()
    if ahk is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        function = _extract_function(fp.read().splitlines(), "SendTransientKeyEdge")
    with tempfile.TemporaryDirectory(prefix="pyahk_transient_syntax_") as temp_dir:
        script = os.path.join(temp_dir, "syntax.ahk")
        with open(script, "w", encoding="utf-8", newline="\n") as fp:
            fp.write("#Requires AutoHotkey v2.0\nExitApp 0\n" + function + "\n")
        proc = subprocess.run(
            [ahk, "/ErrorStdOut", script], capture_output=True, timeout=10
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
