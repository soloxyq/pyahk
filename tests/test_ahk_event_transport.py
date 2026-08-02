#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK→Python 事件通道的真实 AHK 行为测试。

覆盖:退避与状态补发、intercept 自动重复去重、QueuedConnection 队列投递、
以及"迟到事件跨状态转换"的 Python 侧门禁(STOPPED 后不得复活瞬态输入状态)。
"""

import os
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QCoreApplication, Qt

import torchlight_assistant.core.ahk_input_handler as handler_mod
import torchlight_assistant.core.macro_engine as macro_mod
from torchlight_assistant.core.ahk_input_handler import AHKInputHandler
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.signal_bridge import SignalBridge
from torchlight_assistant.core.states import MacroState


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AHK_SCRIPT = os.path.join(REPO, "hold_server_extended.ahk")
_AHK_CANDIDATES = [
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
    r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
]


def _find_ahk():
    for path in _AHK_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


AHK_EXE = _find_ahk()


def _extract_function(src_lines, name):
    start = None
    for index, line in enumerate(src_lines):
        if re.match(rf"^{re.escape(name)}\(.*\)\s*\{{\s*$", line):
            start = index
            break
    if start is None:
        raise AssertionError(f"未找到 AHK 函数 {name}")
    depth = 0
    output = []
    for line in src_lines[start:]:
        output.append(line)
        code = re.sub(r";.*$", "", line)
        depth += code.count("{") - code.count("}")
        if depth == 0:
            return "\n".join(output)
    raise AssertionError(f"函数 {name} 花括号不配对")


_STUBS = r"""
#Requires AutoHotkey v2.0
#SingleInstance Off

global FakeNow := 100
global CurrentPythonWindow := "unused"
global CachedPythonHwnd := 123
global PythonSendBackoffUntil := 0
global PYTHON_SEND_BACKOFF_MS := 1000
global PendingPythonStateEvents := Map()
global SendSucceeds := false
global SendAttempts := 0
global SentEvents := []
global ResultFile := A_Args[1]
global RegisteredHooks := Map()
global SpecialKeysPressed := Map()
global SpecialKeysPaused := false
global ManagedKeysConfig := Map()
global ActiveManagedKeys := Map()
global MonitorKeysState := Map()
global ForceMoveKey := "a"
global ForceMoveActive := false
global InterceptKeysPressed := Map()
global INTERCEPT_REPEAT_WINDOW_MS := 1100

MonotonicMs() {
    global FakeNow
    return FakeNow
}
CachedStrLower(s) {
    return StrLower(s)
}
SendWMCopyDataToPython(hwnd, eventData) {
    global SendSucceeds, SendAttempts, SentEvents
    SendAttempts += 1
    SentEvents.Push(eventData)
    return SendSucceeds
}
SetMacroManagedSuppressed(flag) {
}
SetMacroSpecialSuppressed(flag) {
}
ReconcileSkillHoldKeys() {
}
FinishSpecialKeyPause() {
}
Expect(label, actual, expected) {
    global Failures, Checks
    Checks += 1
    if (actual != expected) {
        Failures.Push(label ": expected [" expected "] actual [" actual "]")
    }
}
EventsStr() {
    global SentEvents
    out := ""
    for index, event in SentEvents {
        out .= (index > 1 ? "," : "") event
    }
    return out
}
ResetAll() {
    global FakeNow, CachedPythonHwnd, PythonSendBackoffUntil
    global PendingPythonStateEvents, SendSucceeds, SendAttempts, SentEvents
    global RegisteredHooks, SpecialKeysPressed, SpecialKeysPaused
    global ManagedKeysConfig, ActiveManagedKeys, MonitorKeysState
    global ForceMoveKey, ForceMoveActive, InterceptKeysPressed
    FakeNow := 100
    CachedPythonHwnd := 123
    PythonSendBackoffUntil := 0
    PendingPythonStateEvents := Map()
    SendSucceeds := false
    SendAttempts := 0
    SentEvents := []
    RegisteredHooks := Map()
    SpecialKeysPressed := Map()
    SpecialKeysPaused := false
    ManagedKeysConfig := Map()
    ActiveManagedKeys := Map()
    MonitorKeysState := Map()
    ForceMoveKey := "a"
    ForceMoveActive := false
    InterceptKeysPressed := Map()
}
global Failures := []
global Checks := 0
"""


_SCENARIOS = r"""
; 失败只尝试一次并开启退避;同通道只保留最新状态。
ResetAll()
QueuePythonStateEvent("monitor:A", "monitor_key_down:a")
Expect("s1-attempt", SendAttempts, 1)
Expect("s1-pending", PendingPythonStateEvents.Count, 1)
Expect("s1-backoff", PythonSendBackoffUntil, 1100)
QueuePythonStateEvent("monitor:A", "monitor_key_up:a")
QueuePythonStateEvent("special_pause", "special_key_pause:end")
Expect("s1-no-retry-during-backoff", SendAttempts, 1)
Expect("s1-two-state-channels", PendingPythonStateEvents.Count, 2)

; 未到退避终点仍不发送。
FakeNow := 1099
CachedPythonHwnd := 123
SendSucceeds := true
FlushPendingPythonStateEvents()
Expect("s2-still-backed-off", SendAttempts, 1)

; 到期后每次 flush 只补一个;monitor 补的是最新 up,不是已过时 down。
FakeNow := 1100
FlushPendingPythonStateEvents()
Expect("s3-first-retry", SendAttempts, 2)
Expect("s3-one-left", PendingPythonStateEvents.Count, 1)
FlushPendingPythonStateEvents()
Expect("s3-second-retry", SendAttempts, 3)
Expect("s3-empty", PendingPythonStateEvents.Count, 0)
Expect("s3-latest-state", EventsStr(),
    "monitor_key_down:a,monitor_key_up:a,special_key_pause:end")

; WM_COPYDATA 栈内可只入 pending,由 timer/显式 flush 后发送。
ResetAll()
SendSucceeds := true
QueuePythonStateEvent("special_pause", "special_key_pause:end", false)
Expect("s4-deferred-no-send", SendAttempts, 0)
Expect("s4-deferred-pending", PendingPythonStateEvents.Count, 1)
FlushPendingPythonStateEvents()
Expect("s4-flushed", SendAttempts, 1)
Expect("s4-cleared", PendingPythonStateEvents.Count, 0)

; stats 失败只丢观测,不能武装共享退避门。
ResetAll()
SendEventToPython("stats:e=0", false, false)
Expect("s5-stats-attempt", SendAttempts, 1)
Expect("s5-stats-no-backoff", PythonSendBackoffUntil, 0)

; 人手热键必须绕过已有退避门；成功说明通道恢复并清掉旧退避。
ResetAll()
PythonSendBackoffUntil := 1100
SendSucceeds := true
SendEventToPython("intercept_key_down:z", true)
Expect("s6-intercept-bypasses-backoff", SendAttempts, 1)
Expect("s6-intercept-clears-backoff", PythonSendBackoffUntil, 0)

; monitor 按住期间注销:本地立即归零,滞留 down 被最新 up 覆盖且不在接收栈内发送。
ResetAll()
RegisteredHooks["a"] := "monitor"
MonitorKeysState["A"] := true
ForceMoveActive := true
PendingPythonStateEvents["monitor:A"] := "monitor_key_down:a"
UnregisterHook("a")
Expect("s7-monitor-hook-removed", RegisteredHooks.Count, 0)
Expect("s7-monitor-state-cleared", MonitorKeysState.Count, 0)
Expect("s7-force-move-cleared", ForceMoveActive ? 1 : 0, 0)
Expect("s7-up-replaces-pending-down",
    PendingPythonStateEvents["monitor:A"], "monitor_key_up:a")
Expect("s7-unregister-does-not-send-inline", SendAttempts, 0)

; CLEAR_HOOKS 进入 STOPPED:两类状态与所有待补发事件一并清空,不得跨轮迟到。
ResetAll()
RegisteredHooks["a"] := "monitor"
RegisteredHooks["Space"] := "special"
MonitorKeysState["A"] := true
ForceMoveActive := true
SpecialKeysPressed["Space"] := true
SpecialKeysPaused := true
PendingPythonStateEvents["monitor:A"] := "monitor_key_down:a"
PendingPythonStateEvents["special_pause"] := "special_key_pause:start"
ClearAllConfigurableHooks()
Expect("s8-all-hooks-removed", RegisteredHooks.Count, 0)
Expect("s8-monitor-map-cleared", MonitorKeysState.Count, 0)
Expect("s8-force-move-cleared", ForceMoveActive ? 1 : 0, 0)
Expect("s8-special-map-cleared", SpecialKeysPressed.Count, 0)
Expect("s8-special-pause-cleared", SpecialKeysPaused ? 1 : 0, 0)
Expect("s8-pending-cleared", PendingPythonStateEvents.Count, 0)
Expect("s8-clear-does-not-send-inline", SendAttempts, 0)

; intercept 自动重复去重:up 前的重复 down 只发一次;up 后立刻再按必须承认。
ResetAll()
SendSucceeds := true
HandleInterceptKey("F8")
FakeNow := 130                       ; 30ms 后的键盘自动重复
HandleInterceptKey("F8")
FakeNow := 160
HandleInterceptKey("F8")
Expect("s9-repeat-deduped", EventsStr(), "intercept_key_down:F8")
HandleInterceptKeyUp("F8")
Expect("s9-up-sends-nothing", SendAttempts, 1)
FakeNow := 200                       ; 松开后 40ms 内快速二连按 —— 有意输入,必须承认
HandleInterceptKey("F8")
Expect("s9-fast-double-press-accepted", EventsStr(),
    "intercept_key_down:F8,intercept_key_down:F8")
HandleInterceptKeyUp("F8")

; up 边沿丢失(极端:安全桌面吞掉钩子):时间窗兜底,键至多"死"一个窗口而非永久。
ResetAll()
SendSucceeds := true
HandleInterceptKey("z")
FakeNow := 100 + 1099                ; 窗口内(1099 < 1100):仍判自动重复并推进窗口
HandleInterceptKey("z")
Expect("s10-stale-within-window-deduped", SendAttempts, 1)
FakeNow := 100 + 1099 + 1100         ; 距上次(被推进的)时间戳满一个窗口:自愈
HandleInterceptKey("z")
Expect("s10-stale-recovers-after-window", SendAttempts, 2)

; intercept 按住期间注销(如 STOPPED 注销 Z):按下状态随注销清除,
; 重注册后窗口内的第一次按下不得被误判为自动重复。
ResetAll()
SendSucceeds := true
RegisteredHooks["z"] := "intercept"
HandleInterceptKey("z")
UnregisterHook("z")
Expect("s11-intercept-state-cleared", InterceptKeysPressed.Count, 0)
FakeNow := 150
HandleInterceptKey("z")
Expect("s11-repress-after-reregister", SendAttempts, 2)

; 滚轮 intercept 键(如 BOSS 键配 WheelUp):up 变体永远不触发(实测),
; 且滚轮无键盘自动重复 —— 每个刻度都必须发出,不参与去重。
ResetAll()
SendSucceeds := true
HandleInterceptKey("WheelUp")
FakeNow := 130                       ; 快速连滚两格,间隔 30ms
HandleInterceptKey("WheelUp")
Expect("s12-wheel-notches-both-fire", SendAttempts, 2)
Expect("s12-wheel-not-tracked", InterceptKeysPressed.Count, 0)

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
    functions = [
        _extract_function(lines, "QueuePythonStateEvent"),
        _extract_function(lines, "FlushPendingPythonStateEvents"),
        _extract_function(lines, "SendEventToPython"),
        _extract_function(lines, "UnregisterHook"),
        _extract_function(lines, "ClearAllConfigurableHooks"),
        _extract_function(lines, "HandleInterceptKey"),
        _extract_function(lines, "HandleInterceptKeyUp"),
        _extract_function(lines, "IsWheelKey"),
    ]
    return "\n".join([_STUBS, _SCENARIOS, *functions])


def test_event_backoff_and_state_retry_with_real_ahk():
    if AHK_EXE is None:
        print("SKIP: 未找到 AutoHotkey v2")
        return
    temp_dir = tempfile.mkdtemp(prefix="pyahk_event_transport_")
    script = os.path.join(temp_dir, "event_transport.ahk")
    result = os.path.join(temp_dir, "result.txt")
    with open(script, "w", encoding="utf-8") as fp:
        fp.write(_build_harness())
    proc = subprocess.run(
        [AHK_EXE, "/ErrorStdOut", script, result],
        capture_output=True,
        timeout=60,
    )
    stderr = proc.stderr.decode("utf-8", "replace")
    assert os.path.isfile(result), f"AHK 未产生结果(exit={proc.returncode}): {stderr}"
    with open(result, encoding="utf-8-sig") as fp:
        report = fp.read()
    assert "RESULT=OK" in report, f"{report}\n{stderr}"
    assert proc.returncode == 0, f"exit={proc.returncode}\n{report}\n{stderr}"


def test_event_call_sites_use_their_required_backoff_policy():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    intercept = _extract_function(lines, "HandleInterceptKey")
    stats = _extract_function(lines, "SendStatsToPython")
    assert re.search(r'SendEventToPython\("intercept_key_down:" key,\s*true\)', intercept)
    assert re.search(r"SendEventToPython\(stats,\s*false,\s*false\)", stats)


def test_wm_copydata_cleanup_never_sends_reverse_events_inline():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    unregister = _extract_function(lines, "UnregisterHook")
    clear_all = _extract_function(lines, "ClearAllConfigurableHooks")
    assert "SendEventToPython(" not in unregister
    assert "SendEventToPython(" not in clear_all
    assert 'QueuePythonStateEvent(channel, "monitor_key_up:" key, false)' in unregister
    assert re.search(r"PendingPythonStateEvents\s*:=\s*Map\(\)", clear_all)


def test_python_event_receiver_is_connected_with_queued_delivery():
    state = object.__new__(AHKInputHandler)
    state.ahk_window = "TEST_AHK"
    state.ahk_process = None
    state._start_ahk_server = lambda: True
    connect = mock.Mock()
    fake_bridge = SimpleNamespace(ahk_event=SimpleNamespace(connect=connect))
    fake_sender = SimpleNamespace(set_target_window=lambda target: True)

    with mock.patch.object(handler_mod, "ahk_signal_bridge", fake_bridge), \
         mock.patch.object(handler_mod, "AHKCommandSender", return_value=fake_sender), \
         mock.patch.object(handler_mod.AHKConfig, "WINDOW_EXE", ""):
        AHKInputHandler._init_ahk_system(state)

    connect.assert_called_once()
    slot, connection_type = connect.call_args.args
    assert slot.__self__ is state
    assert slot.__func__ is AHKInputHandler._on_ahk_event
    assert connection_type == Qt.QueuedConnection


def test_queued_delivery_defers_plain_python_receiver_until_event_loop():
    app = QCoreApplication.instance() or QCoreApplication([])
    received = []

    class Receiver:
        def on_event(self, event):
            received.append(event)

    bridge = SignalBridge()
    receiver = Receiver()
    bridge.ahk_event.connect(receiver.on_event, Qt.QueuedConnection)
    bridge.ahk_event.emit("intercept_key_down:F8")
    assert received == [], "QueuedConnection 退化成同步直连"
    app.processEvents()
    assert received == ["intercept_key_down:F8"]


def test_stopped_state_resets_python_transient_input_flags():
    state = object.__new__(MacroEngine)
    drops = []
    state._force_move_active = True
    state._prepared_mode = "combat"
    state._global_config = {"debug_mode": {"enabled": False}}
    state.input_handler = SimpleNamespace(
        set_accepting_actions=lambda enabled: True,
        set_drop_non_emergency=drops.append,
        clear_queue=lambda: True,
        clear_all_configurable_hooks=lambda: True,
        dry_run_mode=False,
        set_dry_run_mode=lambda enabled: None,
    )
    state.skill_manager = SimpleNamespace(stop=lambda: None)
    state.pathfinding_manager = SimpleNamespace(stop=lambda: None)
    state.resource_manager = SimpleNamespace(stop=lambda: None)
    state.border_manager = SimpleNamespace(stop=lambda: None)
    state._set_boss_mode_active = lambda active, notify=False: None

    with mock.patch.object(macro_mod.event_bus, "publish"):
        MacroEngine._on_state_enter(state, MacroState.STOPPED)

    assert state._force_move_active is False
    assert drops == [False]


def test_intercept_hotkeys_pair_down_with_up_edge():
    """intercept 去重依赖 up 边沿:注册/注销必须成对处理 $key 与 $key up。"""
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    register = _extract_function(lines, "RegisterHook")
    unregister = _extract_function(lines, "UnregisterHook")
    assert (
        'Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key), "On")' in register
    ), "RegisterHook 的 intercept 分支未配对注册 up 边沿"
    assert 'Hotkey("$" key " up", "Off")' in unregister
    # 按住期间注销的状态清理:残留会把重注册后的第一次按下误判为自动重复
    assert "InterceptKeysPressed.Delete(key)" in unregister


def test_force_move_activation_is_gated_by_runtime_accepting_actions():
    """CMD_SET_FORCE_MOVE_STATE:闸门关闭时拒绝 true(迟到激活),false 始终放行。"""
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
    match = re.search(r"case CMD_SET_FORCE_MOVE_STATE:(.*?)\n\s*case ", src, re.S)
    assert match, "未找到 CMD_SET_FORCE_MOVE_STATE 分支"
    block = match.group(1)
    assert re.search(r'param\s*=\s*"true"\s*&&\s*!RuntimeAcceptingActions', block)
    assert "AHK_RESULT_REJECTED" in block
    # false 是安全方向(只会关闭替换),不得被闸门拦截
    assert not re.search(r'"false"[^\n]*RuntimeAcceptingActions', block)


def test_late_monitor_down_is_gated_in_stopped():
    """STOPPED 后迟到的 monitor down 不得重新点亮 force-move(up 永远不会再来)。"""
    presses = []
    state = SimpleNamespace(
        _state=MacroState.STOPPED,
        _current_force_move_key="a",
        _on_force_move_key_press=lambda: presses.append(True),
    )
    MacroEngine._handle_ahk_monitor_key_down(state, "a")
    assert presses == []
    state._state = MacroState.RUNNING
    MacroEngine._handle_ahk_monitor_key_down(state, "a")
    assert presses == [True]


def test_late_special_pause_start_is_gated_in_stopped_but_end_is_not():
    """迟到的 start 不得在 STOPPED 重开丢弃闸;end 是安全方向,任何状态都放行。"""
    drops = []
    state = SimpleNamespace(
        _state=MacroState.STOPPED,
        input_handler=SimpleNamespace(set_drop_non_emergency=drops.append),
    )
    MacroEngine._handle_ahk_special_key_pause(state, "start")
    assert drops == []
    MacroEngine._handle_ahk_special_key_pause(state, "end")
    assert drops == [False]
    state._state = MacroState.RUNNING
    MacroEngine._handle_ahk_special_key_pause(state, "start")
    assert drops == [False, True]


def test_late_managed_key_down_is_gated_in_stopped():
    """STOPPED 后迟到的管理键事件直接丢弃(队列已清空,force 清队徒耗超时预算)。"""
    clears = []
    state = SimpleNamespace(
        _state=MacroState.STOPPED,
        input_handler=SimpleNamespace(
            clear_non_emergency_queue=lambda: clears.append(True)
        ),
    )
    MacroEngine._handle_ahk_managed_key_down(state, "e")
    assert clears == []
    state._state = MacroState.RUNNING
    MacroEngine._handle_ahk_managed_key_down(state, "e")
    assert clears == [True]


def test_resume_from_paused_resyncs_force_move_state_after_gate_opens():
    """PAUSED 期间 AHK 拒绝 set_force_move_state(True);恢复 RUNNING 开闸后必须
    按 Python 账本重对齐,覆盖"按住强制移动键跨过 Z 恢复"的场景。"""
    order = []
    state = SimpleNamespace(
        _open_runtime_gate=lambda context: (order.append("gate"), True)[1],
        _force_move_active=True,
        _prepared_mode="combat",
        input_handler=SimpleNamespace(
            set_force_move_state=lambda active: order.append(("force_move", active)),
        ),
        skill_manager=SimpleNamespace(resume=lambda: order.append("skill_resume")),
        resource_manager=SimpleNamespace(resume=lambda: None),
        border_manager=SimpleNamespace(resume_capture=lambda: None),
    )
    with mock.patch.object(macro_mod.event_bus, "publish"):
        MacroEngine._on_state_enter(state, MacroState.RUNNING, MacroState.PAUSED)
    assert ("force_move", True) in order
    # 必须在开闸之后、生产者恢复之前(生产出的按键要立即遵循正确的替换状态)
    assert order.index(("force_move", True)) > order.index("gate")
    assert order.index(("force_move", True)) < order.index("skill_resume")

    # 开闸失败 = AHK 不可达:整个 RUNNING 初始化中止,不做重对齐
    order.clear()
    state._open_runtime_gate = lambda context: (order.append("gate"), False)[1]
    with mock.patch.object(macro_mod.event_bus, "publish"):
        MacroEngine._on_state_enter(state, MacroState.RUNNING, MacroState.PAUSED)
    assert order == ["gate"]


def test_activate_command_returns_before_winactivate_delay():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    wm_copydata = _extract_function(lines, "WM_COPYDATA")
    activate = _extract_function(lines, "ActivateTargetWindow")
    assert "SetTimer(ActivateTargetWindow, -1)" in wm_copydata
    assert "WinActivate(" not in wm_copydata
    assert "WinActivate(TargetWin)" in activate


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
