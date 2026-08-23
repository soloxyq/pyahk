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
import threading
from collections import deque
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
global PythonEventSession := "777"
global PythonReliableEventSeq := 0
global PendingPythonReliableEvents := []
global MAX_PENDING_PYTHON_RELIABLE_EVENTS := 64
global PYTHON_RELIABLE_F8_RESERVE := 4
global F8StopIntentPending := false
global MainModeArmed := false
global MainModeF8AwaitRelease := false
global MAIN_MODE_F8_RELEASE_POLL_MS := 25
global SimulatedF8PhysicalDown := false
global PhysicalStopLatched := false
global PythonReliableRetryUntil := 0
global PythonReliableRetryDelayMs := 100
global PYTHON_RELIABLE_RETRY_BASE_MS := 100
global PYTHON_RELIABLE_RETRY_MAX_MS := 1000
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
global ForceMoveReplacementKey := "f"
global ForceMovePassthroughKeys := Map()
global RuntimeAcceptingActions := true
global SendKeyMode := "direct"
global TargetWin := ""
global KeyLog := []
global SimulatedQueueCount := 0
global SimulatedMacroActive := false
global SimulatedHeldCount := 0
global StopBarrierCalls := 0
global InterceptKeysPressed := Map()
global INTERCEPT_REPEAT_WINDOW_MS := 1100
global ReplacementEventDuringSend := ""

MonotonicMs() {
    global FakeNow
    return FakeNow
}
CachedStrLower(s) {
    return StrLower(s)
}
SendWMCopyDataToPython(hwnd, eventData) {
    global SendSucceeds, SendAttempts, SentEvents, ReplacementEventDuringSend
    SendAttempts += 1
    SentEvents.Push(eventData)
    if (ReplacementEventDuringSend != "") {
        replacement := ReplacementEventDuringSend
        ReplacementEventDuringSend := ""
        QueuePythonReliableEvent(replacement, false)
    }
    return SendSucceeds
}
SetMacroManagedSuppressed(flag) {
}
SetMacroSpecialSuppressed(flag) {
}
ReconcileSkillHoldKeys() {
}
ClearQueue(priority) {
    global SimulatedQueueCount, SimulatedMacroActive, SimulatedHeldCount
    global StopBarrierCalls
    if (priority = -1) {
        StopBarrierCalls += 1
        SimulatedQueueCount := 0
        SimulatedMacroActive := false
        SimulatedHeldCount := 0
    }
}
FinishSpecialKeyPause() {
}
HandleManagedKey(key) {
}
HandleSpecialKeyDown(key) {
}
HandleSpecialKeyUp(key) {
}
ShouldBlockMouseInStationary(key) {
    return false
}
ShouldAddShiftModifier(key) {
    return false
}
SendKeyInternal(key) {
    global KeyLog
    KeyLog.Push(key)
    return true
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
KeyLogStr() {
    global KeyLog
    out := ""
    for index, key in KeyLog {
        out .= (index > 1 ? "," : "") key
    }
    return out
}
IsPhysicalKeyPressed(key) {
    global SimulatedF8PhysicalDown
    return StrUpper(key) = "F8" && SimulatedF8PhysicalDown
}
ResetAll() {
    global FakeNow, CachedPythonHwnd, PythonSendBackoffUntil
    global PendingPythonStateEvents, SendSucceeds, SendAttempts, SentEvents
    global PythonReliableEventSeq, PendingPythonReliableEvents
    global PythonReliableRetryUntil, PythonReliableRetryDelayMs
    global RegisteredHooks, SpecialKeysPressed, SpecialKeysPaused
    global ManagedKeysConfig, ActiveManagedKeys, MonitorKeysState
    global ForceMoveKey, ForceMoveActive, ForceMoveReplacementKey
    global ForceMovePassthroughKeys, RuntimeAcceptingActions, KeyLog
    global SimulatedQueueCount, SimulatedMacroActive, SimulatedHeldCount
    global StopBarrierCalls
    global InterceptKeysPressed, F8StopIntentPending, MainModeArmed
    global MainModeF8AwaitRelease, SimulatedF8PhysicalDown, PhysicalStopLatched
    global ReplacementEventDuringSend
    SetTimer(PollMainModeF8Release, 0)
    FakeNow := 100
    CachedPythonHwnd := 123
    PythonSendBackoffUntil := 0
    PendingPythonStateEvents := Map()
    PythonReliableEventSeq := 0
    PendingPythonReliableEvents := []
    PythonReliableRetryUntil := 0
    PythonReliableRetryDelayMs := 100
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
    ForceMoveReplacementKey := "f"
    ForceMovePassthroughKeys := Map()
    RuntimeAcceptingActions := true
    KeyLog := []
    SimulatedQueueCount := 0
    SimulatedMacroActive := false
    SimulatedHeldCount := 0
    StopBarrierCalls := 0
    InterceptKeysPressed := Map()
    F8StopIntentPending := false
    MainModeArmed := false
    MainModeF8AwaitRelease := false
    SimulatedF8PhysicalDown := false
    PhysicalStopLatched := false
    ReplacementEventDuringSend := ""
}
EnableScheduledRetrySuccess() {
    global FakeNow, CachedPythonHwnd, SendSucceeds
    FakeNow := 200
    CachedPythonHwnd := 123
    SendSucceeds := true
}
global Failures := []
global Checks := 0
"""


_SCENARIOS = r"""
Critical "On"
; 失败只尝试一次并开启退避;同通道只保留最新状态。
ResetAll()
QueuePythonStateEvent("monitor:A", "monitor_key_down:a", false)
FlushPendingPythonStateEvents()
Expect("s1-attempt", SendAttempts, 1)
Expect("s1-pending", PendingPythonStateEvents.Count, 1)
Expect("s1-backoff", PythonSendBackoffUntil, 1100)
QueuePythonStateEvent("monitor:A", "monitor_key_up:a", false)
QueuePythonStateEvent("special_pause", "special_key_pause:end", false)
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

; 人手热键只入 FIFO；flush 绕过已有状态退避，信封携带 session+seq。
ResetAll()
PythonSendBackoffUntil := 1100
SendSucceeds := true
QueuePythonReliableEvent("intercept_key_down:z", false)
Expect("s6-enqueued-without-inline-send", SendAttempts, 0)
FlushPendingPythonReliableEvents()
Expect("s6-intercept-bypasses-backoff", SendAttempts, 1)
Expect("s6-intercept-clears-backoff", PythonSendBackoffUntil, 0)
Expect("s6-sequenced-envelope", EventsStr(), "evt:777:1:intercept_key_down:z")
Expect("s6-fifo-cleared", PendingPythonReliableEvents.Length, 0)

; monitor 按住期间注销:本地立即归零,滞留 down 被最新 up 覆盖且不在接收栈内发送。
ResetAll()
RegisterHook("F13", "monitor")
ForceMoveKey := "F13"
MonitorKeysState["F13"] := true
ForceMoveActive := true
PendingPythonStateEvents["monitor:F13"] := "monitor_key_down:F13"
UnregisterHook("F13")
Expect("s7-monitor-hook-removed", RegisteredHooks.Count, 0)
Expect("s7-monitor-state-cleared", MonitorKeysState.Count, 0)
Expect("s7-force-move-cleared", ForceMoveActive ? 1 : 0, 0)
Expect("s7-up-replaces-pending-down",
    PendingPythonStateEvents["monitor:F13"], "monitor_key_up:F13")
Expect("s7-unregister-does-not-send-inline", SendAttempts, 0)

; CLEAR_HOOKS 进入 STOPPED:两类状态与所有待补发事件一并清空,不得跨轮迟到。
ResetAll()
RegisterHook("F13", "monitor")
RegisterHook("F14", "special")
MonitorKeysState["F13"] := true
ForceMoveActive := true
SpecialKeysPressed["F14"] := true
SpecialKeysPaused := true
PendingPythonStateEvents["monitor:A"] := "monitor_key_down:a"
PendingPythonStateEvents["special_pause"] := "special_key_pause:start"
QueuePythonReliableEvent("intercept_key_down:F7", false)
ClearAllConfigurableHooks()
Expect("s8-all-hooks-removed", RegisteredHooks.Count, 0)
Expect("s8-monitor-map-cleared", MonitorKeysState.Count, 0)
Expect("s8-force-move-cleared", ForceMoveActive ? 1 : 0, 0)
Expect("s8-special-map-cleared", SpecialKeysPressed.Count, 0)
Expect("s8-special-pause-cleared", SpecialKeysPaused ? 1 : 0, 0)
Expect("s8-pending-cleared", PendingPythonStateEvents.Count, 0)
Expect("s8-reliable-fifo-preserved", PendingPythonReliableEvents.Length, 1)
Expect("s8-clear-does-not-send-inline", SendAttempts, 0)

; AHK 协议边界也必须保护永久根热键。绕过 Python 直接发 F8:priority
; 不得覆盖永久 intercept handler；root 本就不进 RegisteredHooks，覆盖后无法清理。
ResetAll()
Expect("s8b-root-non-intercept-rejected", RegisterHook("F8", "priority") ? 1 : 0, 0)
Expect("s8b-rejected-root-not-recorded", RegisteredHooks.Count, 0)

; intercept 自动重复去重:up 前的重复 down 只发一次;up 后立刻再按必须承认。
ResetAll()
SendSucceeds := true
HandleInterceptKey("F8")
FlushPendingPythonReliableEvents()
FakeNow := 130                       ; 30ms 后的键盘自动重复
HandleInterceptKey("F8")
FakeNow := 160
HandleInterceptKey("F8")
Expect("s9-repeat-deduped", EventsStr(), "evt:777:1:intercept_key_down:F8")
HandleInterceptKeyUp("F8")
Expect("s9-up-sends-nothing", SendAttempts, 1)
FakeNow := 200                       ; 松开后 40ms 内快速二连按 —— 有意输入,必须承认
HandleInterceptKey("F8")
FlushPendingPythonReliableEvents()
Expect("s9-fast-double-press-accepted", EventsStr(),
    "evt:777:1:intercept_key_down:F8,evt:777:2:intercept_key_down:F8")
HandleInterceptKeyUp("F8")

; STOPPED 启动 F8 若仍按住，READY armed 后的 down 只是 Windows auto-repeat，
; 不能把一次长按误判成“启动后立即停止”。up 丢失时由物理键态轮询确认释放，
; 此后新的 down 才是 stop 意图。
ResetAll()
SendSucceeds := true
HandleInterceptKey("F8")
FlushPendingPythonReliableEvents()
Expect("s9b-stopped-start-sent", EventsStr(),
    "evt:777:1:intercept_key_down:F8")
; 故意不调用 HandleInterceptKeyUp("F8")，并让旧 gate 保持 true，arm 必须自行关掉。
SimulatedF8PhysicalDown := true
Expect("s9b-arm-main-succeeds", ArmMainMode() ? 1 : 0, 1)
Expect("s9b-arm-main-closes-stale-gate", RuntimeAcceptingActions ? 1 : 0, 0)
Expect("s9b-held-start-awaits-release", MainModeF8AwaitRelease ? 1 : 0, 1)
FakeNow := 130
HandleInterceptKey("F8")
Expect("s9b-start-auto-repeat-does-not-stop", PhysicalStopLatched ? 1 : 0, 0)
Expect("s9b-start-auto-repeat-queues-nothing", PendingPythonReliableEvents.Length, 0)
SimulatedF8PhysicalDown := false
PollMainModeF8Release()
Expect("s9b-poll-observes-missing-up", MainModeF8AwaitRelease ? 1 : 0, 0)
SimulatedF8PhysicalDown := true
FakeNow := 160
HandleInterceptKey("F8")
Expect("s9b-new-press-after-release-stops", PhysicalStopLatched ? 1 : 0, 1)
Expect("s9b-stop-envelope-queued", PendingPythonReliableEvents[1],
    "evt:777:2:intercept_key_down:f8_stop")
FlushPendingPythonReliableEvents()
Expect("s9b-stop-sent", EventsStr(),
    "evt:777:1:intercept_key_down:F8,evt:777:2:intercept_key_down:f8_stop")
FakeNow := 190
HandleInterceptKey("F8")
Expect("s9b-latched-auto-repeat-deduped", SendAttempts, 2)
Expect("s9b-repeat-does-not-queue-another-stop", PendingPythonReliableEvents.Length, 0)
HandleInterceptKeyUp("F8")

; up 边沿丢失(极端:安全桌面吞掉钩子):时间窗兜底,键至多"死"一个窗口而非永久。
ResetAll()
SendSucceeds := true
HandleInterceptKey("z")
FlushPendingPythonReliableEvents()
FakeNow := 100 + 1099                ; 窗口内(1099 < 1100):仍判自动重复并推进窗口
HandleInterceptKey("z")
Expect("s10-stale-within-window-deduped", SendAttempts, 1)
FakeNow := 100 + 1099 + 1100         ; 距上次(被推进的)时间戳满一个窗口:自愈
HandleInterceptKey("z")
FlushPendingPythonReliableEvents()
Expect("s10-stale-recovers-after-window", SendAttempts, 2)

; intercept 按住期间注销(如 STOPPED 注销 Z):按下状态随注销清除,
; 重注册后窗口内的第一次按下不得被误判为自动重复。
ResetAll()
SendSucceeds := true
RegisterHook("F15", "intercept")
HandleInterceptKey("F15")
FlushPendingPythonReliableEvents()
UnregisterHook("F15")
Expect("s11-intercept-state-cleared", InterceptKeysPressed.Count, 0)
FakeNow := 150
HandleInterceptKey("F15")
FlushPendingPythonReliableEvents()
Expect("s11-repress-after-reregister", SendAttempts, 2)

; 滚轮 intercept 键(如 BOSS 键配 WheelUp):up 变体永远不触发(实测),
; 且滚轮无键盘自动重复 —— 每个刻度都必须发出,不参与去重。
ResetAll()
SendSucceeds := true
HandleInterceptKey("WheelUp")
FakeNow := 130                       ; 快速连滚两格,间隔 30ms
HandleInterceptKey("WheelUp")
FlushPendingPythonReliableEvents()
FlushPendingPythonReliableEvents()
Expect("s12-wheel-notches-both-fire", SendAttempts, 2)
Expect("s12-wheel-not-tracked", InterceptKeysPressed.Count, 0)

; 注册/注销结果真实可见:非法模式不落表；合法成对 Hook 完整登记后可幂等注销。
ResetAll()
Expect("s13-invalid-mode-rejected", RegisterHook("F16", "unknown") ? 1 : 0, 0)
Expect("s13-invalid-mode-not-recorded", RegisteredHooks.Count, 0)
Expect("s13-wheel-special-rejected", RegisterHook("WheelUp", "special") ? 1 : 0, 0)
Expect("s13-wheel-monitor-rejected", RegisterHook("WheelUp", "monitor") ? 1 : 0, 0)
Expect("s13-valid-register", RegisterHook("F16", "special") ? 1 : 0, 1)
Expect("s13-valid-recorded", RegisteredHooks.Count, 1)
Expect("s13-valid-unregister", UnregisterHook("F16") ? 1 : 0, 1)
Expect("s13-unregistered", RegisteredHooks.Count, 0)

; 超时保留队首并指数退避；恢复后严格 FIFO，成功一条才弹一条。
ResetAll()
QueuePythonReliableEvent("intercept_key_down:F8", false)
QueuePythonReliableEvent("intercept_key_down:z", false)
FlushPendingPythonReliableEvents()
Expect("s14-timeout-keeps-head", PendingPythonReliableEvents.Length, 2)
Expect("s14-first-backoff", PythonReliableRetryUntil, 200)
FakeNow := 199
SendSucceeds := true
FlushPendingPythonReliableEvents()
Expect("s14-no-early-retry", SendAttempts, 1)
FakeNow := 200
CachedPythonHwnd := 123
FlushPendingPythonReliableEvents()
Expect("s14-one-removed", PendingPythonReliableEvents.Length, 1)
FlushPendingPythonReliableEvents()
Expect("s14-all-removed", PendingPythonReliableEvents.Length, 0)
Expect("s14-fifo-order", EventsStr(),
    "evt:777:1:intercept_key_down:F8,evt:777:1:intercept_key_down:F8,evt:777:2:intercept_key_down:z")

; 旧队首的 SendMessage 仍在飞时，物理 stop 可替换 FIFO。旧发送随后失败不得把
; 自己的退避写回去压住新 stop；新队首应保持立即可重试。
ResetAll()
QueuePythonReliableEvent("intercept_key_down:WheelUp", false)
RegisteredHooks["z"] := "intercept"
ReplacementEventDuringSend := "intercept_key_down:F8"
FlushPendingPythonReliableEvents()
Expect("s14b-inflight-old-head-replaced", PendingPythonReliableEvents.Length, 1)
Expect("s14b-replacement-is-stop", PendingPythonReliableEvents[1],
    "evt:777:2:intercept_key_down:f8_stop")
Expect("s14b-obsolete-failure-does-not-rearm-backoff", PythonReliableRetryUntil, 0)
Expect("s14b-retry-budget-reset", PythonReliableRetryDelayMs, 100)

; “立即 flush”不能覆盖 100ms 周期重试器。没有任何后续事件时，首次失败也必须
; 在退避到期后自行补发，而不是永久滞留到用户再按一次键。
ResetAll()
Critical "Off"
SetTimer(FlushPendingPythonEvents, 25)
QueuePythonReliableEvent("intercept_key_down:F8")
SetTimer(EnableScheduledRetrySuccess, -60)
Sleep 220
SetTimer(FlushPendingPythonEvents, 0)
Expect("s15-periodic-retry-attempted", SendAttempts, 2)
Expect("s15-periodic-retry-cleared", PendingPythonReliableEvents.Length, 0)

; 高频业务边沿不能吃掉停机 F8 的可靠队列容量；绝对满时 F8 驱逐一个非 F8。
ResetAll()
loop 60 {
    QueuePythonReliableEvent("intercept_key_down:WheelUp", false)
}
Expect("s16-business-soft-limit", PendingPythonReliableEvents.Length, 60)
Expect("s16-business-overflow-rejected",
    QueuePythonReliableEvent("intercept_key_down:z", false) ? 1 : 0, 0)
loop 4 {
    QueuePythonReliableEvent("intercept_key_down:F8", false)
}
Expect("s16-f8-uses-reserve", PendingPythonReliableEvents.Length, 64)
Expect("s16-f8-evicts-business-when-full",
    QueuePythonReliableEvent("intercept_key_down:F8", false) ? 1 : 0, 1)
Expect("s16-still-bounded", PendingPythonReliableEvents.Length, 64)

; 64 项全是普通 STOPPED F8 时也必须保持有界并接受最新 toggle；主模式随后活跃时，
; stop-only F8 直接取代整批旧世代 toggle，不能因“全是 F8、无业务项可驱逐”而失败。
ResetAll()
loop 64 {
    QueuePythonReliableEvent("intercept_key_down:F8", false)
}
Expect("s16b-all-f8-full", PendingPythonReliableEvents.Length, 64)
Expect("s16b-newest-f8-accepted",
    QueuePythonReliableEvent("intercept_key_down:F8", false) ? 1 : 0, 1)
Expect("s16b-oldest-f8-evicted", PendingPythonReliableEvents[1],
    "evt:777:2:intercept_key_down:F8")
RegisteredHooks["z"] := "intercept"
Expect("s16b-active-stop-always-accepted",
    QueuePythonReliableEvent("intercept_key_down:F8", false) ? 1 : 0, 1)
Expect("s16b-active-stop-invalidates-old-generation",
    PendingPythonReliableEvents.Length, 1)
Expect("s16b-active-stop-only-envelope", PendingPythonReliableEvents[1],
    "evt:777:66:intercept_key_down:f8_stop")

; monitor 物理边沿是强制移动执行态权威：即使 Python 事件始终发送失败，
; down 后也要立即替换普通技能，up 后立即恢复，不等待任何往返。
ResetAll()
SendSucceeds := false
HandleMonitorKey("a")
Expect("s17-local-down-activates", ForceMoveActive ? 1 : 0, 1)
SendPress("1")
Expect("s17-down-replaces-skill", KeyLogStr(), "f")
HandleMonitorKeyUp("a")
Expect("s17-local-up-deactivates", ForceMoveActive ? 1 : 0, 0)
SendPress("1")
Expect("s17-up-restores-skill", KeyLogStr(), "f,1")

; PAUSED 关闸期间 down 只记物理账本、不激活替换；开闸时若仍按住则当地恢复。
ResetAll()
RuntimeAcceptingActions := false
HandleMonitorKey("a")
Expect("s18-paused-down-stays-inactive", ForceMoveActive ? 1 : 0, 0)
RuntimeAcceptingActions := true
ReconcileForceMoveState()
Expect("s18-open-gate-reconciles-held-key", ForceMoveActive ? 1 : 0, 1)
RuntimeAcceptingActions := false
ReconcileForceMoveState()
Expect("s18-close-gate-clears-active", ForceMoveActive ? 1 : 0, 0)

; READY 两阶段入口在首个动态 Hook 前先 armed，但保持 gate 关闭。此窗口内的
; 物理 F8 必须仍是 stop-only，并在 Python 的慢捕获/OCR 返回前本地止血。
ResetAll()
SimulatedQueueCount := 2
SimulatedMacroActive := true
SimulatedHeldCount := 1
Expect("s18b-arm-main-succeeds", ArmMainMode() ? 1 : 0, 1)
Expect("s18b-main-is-armed", MainModeArmed ? 1 : 0, 1)
Expect("s18b-arm-does-not-open-gate", RuntimeAcceptingActions ? 1 : 0, 0)
HandleInterceptKey("F8")
Expect("s18b-f8-before-first-hook-latches", PhysicalStopLatched ? 1 : 0, 1)
Expect("s18b-f8-before-first-hook-stays-closed", RuntimeAcceptingActions ? 1 : 0, 0)
Expect("s18b-f8-before-first-hook-is-stop-only", PendingPythonReliableEvents[1],
    "evt:777:1:intercept_key_down:f8_stop")
Expect("s18b-latched-main-rearm-rejected", ArmMainMode() ? 1 : 0, 0)
SendSucceeds := true
FlushPendingPythonReliableEvents()
Expect("s18b-cleanup-succeeds", ClearAllConfigurableHooks() ? 1 : 0, 1)
Expect("s18b-cleanup-clears-main-armed", MainModeArmed ? 1 : 0, 0)
Expect("s18b-cleanup-clears-stop-latch", PhysicalStopLatched ? 1 : 0, 0)

; 动态 Hook 存在时 F8 是止血命令：即使前面有 60 条滚轮边沿，
; 所有尚未发送的旧世代边沿失效；重复按下只在 stop 信封仍在本地时合并。
Critical "On"
ResetAll()
Expect("s19-register-active-hook", RegisterHook("F16", "intercept") ? 1 : 0, 1)
loop 58 {
    QueuePythonReliableEvent("intercept_key_down:WheelUp", false)
}
QueuePythonReliableEvent("intercept_key_down:F7", false)
QueuePythonReliableEvent("intercept_key_down:F9", false)
SimulatedQueueCount := 3
SimulatedMacroActive := true
SimulatedHeldCount := 2
HandleInterceptKey("F8")
Expect("s19-f8-latches-physical-stop", PhysicalStopLatched ? 1 : 0, 1)
Expect("s19-f8-closes-local-gate", RuntimeAcceptingActions ? 1 : 0, 0)
Expect("s19-f8-clears-local-queue", SimulatedQueueCount, 0)
Expect("s19-f8-stops-local-macro", SimulatedMacroActive ? 1 : 0, 0)
Expect("s19-f8-releases-local-holds", SimulatedHeldCount, 0)
Expect("s19-f8-barrier-before-flush", StopBarrierCalls, 1)
Expect("s19-active-f8-at-head", PendingPythonReliableEvents[1],
    "evt:777:61:intercept_key_down:f8_stop")
Expect("s19-pre-stop-root-events-invalidated", PendingPythonReliableEvents.Length, 1)
Expect("s19-latched-open-rejected", SetRuntimeActionGate(true) ? 1 : 0, 0)
Expect("s19-rejected-open-stays-closed", RuntimeAcceptingActions ? 1 : 0, 0)
Expect("s19-duplicate-stop-coalesced",
    QueuePythonReliableEvent("intercept_key_down:F8", false) ? 1 : 0, 1)
Expect("s19-coalesced-length", PendingPythonReliableEvents.Length, 1)
; stop 之后才产生的新事件必须保留在其后，不能被旧世代清理误删。
QueuePythonReliableEvent("intercept_key_down:F7", false)
Expect("s19-post-stop-event-preserved", PendingPythonReliableEvents.Length, 2)
SendSucceeds := true
FlushPendingPythonReliableEvents()
Expect("s19-stop-sent-first", EventsStr(),
    "evt:777:61:intercept_key_down:f8_stop")
Expect("s19-stop-pop-clears-pending", F8StopIntentPending ? 1 : 0, 0)
Expect("s19-latch-survives-envelope-pop", PhysicalStopLatched ? 1 : 0, 1)
Expect("s19-post-stop-event-still-queued", PendingPythonReliableEvents[1],
    "evt:777:62:intercept_key_down:F7")
Expect("s19-clear-hooks-succeeds", ClearAllConfigurableHooks() ? 1 : 0, 1)
Expect("s19-full-cleanup-unlocks-stop-latch", PhysicalStopLatched ? 1 : 0, 0)
Expect("s19-fresh-open-after-cleanup", SetRuntimeActionGate(true) ? 1 : 0, 1)

; 动态 Hook 清理未完整成功时必须保持 fail-closed：不得只因 stop 信封已出队
; 就解除 latch。unknown 模式构造真实 UnregisterHook 失败路径，不依赖 Python 桩。
ResetAll()
RuntimeAcceptingActions := false
PhysicalStopLatched := true
RegisteredHooks["F17"] := "unknown"
Expect("s19b-partial-hook-cleanup-rejected", ClearAllConfigurableHooks() ? 1 : 0, 0)
Expect("s19b-failed-cleanup-keeps-latch", PhysicalStopLatched ? 1 : 0, 1)
Expect("s19b-failed-cleanup-rejects-open", SetRuntimeActionGate(true) ? 1 : 0, 0)
Expect("s19b-failed-cleanup-stays-closed", RuntimeAcceptingActions ? 1 : 0, 0)

; STOPPED 时 F8 是启动意图，不能借用停机优先级重写 F7→F8 的真实顺序。
ResetAll()
SendSucceeds := true
SimulatedQueueCount := 3
QueuePythonReliableEvent("intercept_key_down:F7", false)
HandleInterceptKey("F8")
Expect("s20-start-f8-does-not-close-gate", RuntimeAcceptingActions ? 1 : 0, 1)
Expect("s20-start-f8-does-not-clear-queue", SimulatedQueueCount, 3)
FlushPendingPythonReliableEvents()
FlushPendingPythonReliableEvents()
Expect("s20-stopped-root-events-stay-fifo", EventsStr(),
    "evt:777:1:intercept_key_down:F7,evt:777:2:intercept_key_down:F8")

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
        _extract_function(lines, "QueuePythonReliableEvent"),
        _extract_function(lines, "IsF8StopEvent"),
        _extract_function(lines, "IsF8Envelope"),
        _extract_function(lines, "IsF8StopEnvelope"),
        _extract_function(lines, "SchedulePythonEventFlush"),
        _extract_function(lines, "FlushPendingPythonEventsSoon"),
        _extract_function(lines, "FlushPendingPythonEvents"),
        _extract_function(lines, "FlushPendingPythonReliableEvents"),
        _extract_function(lines, "FlushPendingPythonStateEvents"),
        _extract_function(lines, "SendEventToPython"),
        _extract_function(lines, "RegisterHook"),
        _extract_function(lines, "UnregisterHook"),
        _extract_function(lines, "IsSupportedHookMode"),
        _extract_function(lines, "HookModeHasUpEdge"),
        _extract_function(lines, "EnableHookDown"),
        _extract_function(lines, "EnableHookUp"),
        _extract_function(lines, "DisableHookDown"),
        _extract_function(lines, "DisableHookUp"),
        _extract_function(lines, "ClearAllConfigurableHooks"),
        _extract_function(lines, "HandleInterceptKey"),
        _extract_function(lines, "HandleInterceptKeyUp"),
        _extract_function(lines, "HandleMonitorKey"),
        _extract_function(lines, "HandleMonitorKeyUp"),
        _extract_function(lines, "ReconcileForceMoveState"),
        _extract_function(lines, "ArmMainMode"),
        _extract_function(lines, "PollMainModeF8Release"),
        _extract_function(lines, "LatchPhysicalStop"),
        _extract_function(lines, "SetRuntimeActionGate"),
        _extract_function(lines, "SendPress"),
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
    latch = _extract_function(lines, "LatchPhysicalStop")
    stats = _extract_function(lines, "SendStatsToPython")
    assert 'QueuePythonReliableEvent("intercept_key_down:" key)' in intercept
    assert "QueuePythonReliableEvent(event, true, true)" in latch
    assert re.search(r"SendEventToPython\(stats,\s*false,\s*false\)", stats)


def test_reliable_events_are_deferred_bounded_and_sequenced():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        lines = fp.read().splitlines()
    queue = _extract_function(lines, "QueuePythonReliableEvent")
    flush = _extract_function(lines, "FlushPendingPythonReliableEvents")
    assert 'Format("evt:{}:{}:{}"' in queue
    assert "MAX_PENDING_PYTHON_RELIABLE_EVENTS" in queue
    assert "PYTHON_RELIABLE_F8_RESERVE" in queue
    assert "PendingPythonReliableEvents := []" in queue
    assert "IsF8Envelope(" in queue
    assert "SchedulePythonEventFlush()" in queue
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
    assert "SetTimer(FlushPendingPythonEvents, -1)" not in src
    assert "SetTimer(FlushPendingPythonEventsSoon, -1)" in src
    assert "PendingPythonReliableEvents[1]" in flush
    assert "PendingPythonReliableEvents.RemoveAt(1)" in flush
    assert re.search(r"if \(SendEventToPython\(envelope,\s*true,\s*false\)\)", flush)
    assert "F8StopIntentPending := false" in flush


def test_macro_uses_a_poll_budget_not_an_implicit_step_interval():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(r"MACRO_POLL_INTERVAL_MS\s*:=\s*5\b", src)
    assert "MACRO_TICK_MS" not in src
    assert "SetTimer(MacroTick, MACRO_POLL_INTERVAL_MS)" in src
    macro_tick = _extract_function(src.splitlines(), "MacroTick")
    assert "MacroDueTime" in macro_tick
    assert "Sleep" not in macro_tick


def test_key_press_duration_reaches_ahk_send_direct():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
        lines = src.splitlines()
    batch = _extract_function(lines, "UpdateBatchConfig")
    send_direct = _extract_function(lines, "SendDirect")
    assert 'case "key_press_duration"' in batch
    assert "KeyPressDurationMs := Min(Max(Integer(value), 1), 1000)" in batch
    assert "Sleep KeyPressDurationMs" in send_direct


def test_coordinate_click_action_is_validated_and_non_blocking():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
        lines = src.splitlines()
    enqueue = re.search(r"case CMD_ENQUEUE:(.*?)case CMD_SET_ACCEPTING_ACTIONS:", src, re.S)
    assert enqueue and "IsValidQueuedAction(action)" in enqueue.group(1)
    parser = _extract_function(lines, "ParseMouseClickAt")
    execute = _extract_function(lines, "ExecuteMouseClickAt")
    assert "values.Length != 3" in parser
    assert all(f"SysGet({metric})" in parser for metric in (76, 77, 78, 79))
    assert "MAX_MOUSE_CLICK_HOLD_MS" in parser
    assert "ClickMouseAtOnce(x, y)" in execute
    assert "MarkManagedHoldTarget(\"LButton\")" in execute
    assert 'PushFrontWait(priority, ACTION_RELEASE ":LButton", holdMs)' in execute
    assert not re.search(r"^\s*Sleep\b", execute, re.M)


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
    assert state._signal_connected is True
    # fake signal 只记录 connect，并没有真实 Qt 连接；避免析构时去全局桥解绑。
    state._signal_connected = False


def test_reliable_event_envelope_is_unwrapped_and_deduplicated():
    published = []
    state = object.__new__(AHKInputHandler)
    state.ahk_process = None
    state._signal_connected = False
    state.event_bus = SimpleNamespace(
        publish=lambda name, **data: published.append((name, data))
    )
    state._recent_ahk_event_ids = deque()
    state._recent_ahk_event_id_set = set()

    AHKInputHandler._on_ahk_event(
        state, "evt:session-a:7:intercept_key_down:F8"
    )
    AHKInputHandler._on_ahk_event(
        state, "evt:session-a:7:intercept_key_down:F8"
    )
    AHKInputHandler._on_ahk_event(
        state, "evt:session-b:7:special_key_pause:start"
    )
    AHKInputHandler._on_ahk_event(state, "monitor_key_up:A")

    assert published == [
        ("intercept_key_down", {"key": "F8"}),
        ("special_key_pause", {"action": "start"}),
        ("monitor_key_up", {"key": "A"}),
    ]


def test_reliable_event_dedupe_window_is_bounded():
    state = object.__new__(AHKInputHandler)
    state.ahk_process = None
    state._signal_connected = False
    state._recent_ahk_event_ids = deque()
    state._recent_ahk_event_id_set = set()
    with mock.patch.object(AHKInputHandler, "RECENT_AHK_EVENT_LIMIT", 2):
        assert state._unwrap_ahk_event("evt:s:1:a:") == "a:"
        assert state._unwrap_ahk_event("evt:s:2:b:") == "b:"
        assert state._unwrap_ahk_event("evt:s:3:c:") == "c:"
        assert len(state._recent_ahk_event_ids) == 2
        assert len(state._recent_ahk_event_id_set) == 2
        assert state._unwrap_ahk_event("evt:s:1:a:") == "a:"


def test_input_handler_stop_disconnects_global_signal_once():
    signal = SimpleNamespace(disconnect=mock.Mock())
    state = object.__new__(AHKInputHandler)
    state._signal_connected = True
    state.ahk_process = None
    with mock.patch.object(
        handler_mod, "ahk_signal_bridge", SimpleNamespace(ahk_event=signal)
    ):
        AHKInputHandler.stop(state)
        AHKInputHandler.stop(state)
    signal.disconnect.assert_called_once()
    slot = signal.disconnect.call_args.args[0]
    assert slot.__self__ is state
    assert slot.__func__ is AHKInputHandler._on_ahk_event


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
    stationary = []
    state._force_move_active = True
    state._stationary_mode_active = True
    state._prepared_mode = "combat"
    state._global_config = {
        "debug_mode": {"enabled": False},
        "stationary_mode_config": {"mode_type": "block_mouse"},
    }
    state.input_handler = SimpleNamespace(
        set_accepting_actions=lambda enabled: True,
        set_stationary_mode=lambda active, mode: stationary.append((active, mode)),
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
    assert state._stationary_mode_active is False
    assert stationary == [(False, "block_mouse")]
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
    assert "HookModeHasUpEdge(key, mode)" in unregister
    assert "DisableHookUp(key, mode)" in unregister
    # 按住期间注销的状态清理:残留会把重注册后的第一次按下误判为自动重复
    assert "InterceptKeysPressed.Delete(key)" in unregister


def test_hook_commands_return_transaction_results_and_register_commits_last():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
        lines = src.splitlines()
    register = _extract_function(lines, "RegisterHook")
    wm_register = re.search(r"case CMD_HOOK_REGISTER:(.*?)case CMD_HOOK_UNREGISTER:", src, re.S)
    wm_unregister = re.search(r"case CMD_HOOK_UNREGISTER:(.*?)case CMD_CLEAR_QUEUE:", src, re.S)
    assert wm_register and "RegisterHook(parts[1], parts[2]) ? 1 : AHK_RESULT_REJECTED" in wm_register.group(1)
    assert wm_unregister and "UnregisterHook(param) ? 1 : AHK_RESULT_REJECTED" in wm_unregister.group(1)
    assert register.index("RegisteredHooks[key] := mode") > register.index("} catch {")
    assert "DisableHookDown(key, mode)" in register
    assert 'is_root && mode != "intercept"' in register


def test_force_move_activation_is_gated_by_runtime_accepting_actions():
    """Python 回发不盲写执行态；闸门与物理账本共同决定。"""
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
    match = re.search(r"case CMD_SET_FORCE_MOVE_STATE:(.*?)\n\s*case ", src, re.S)
    assert match, "未找到 CMD_SET_FORCE_MOVE_STATE 分支"
    block = match.group(1)
    assert re.search(r'param\s*=\s*"true"\s*&&\s*!RuntimeAcceptingActions', block)
    assert "AHK_RESULT_REJECTED" in block
    assert "ReconcileForceMoveState()" in block
    assert not re.search(r"ForceMoveActive\s*:=", block)

    accepting = re.search(
        r"case CMD_SET_ACCEPTING_ACTIONS:(.*?)\n\s*case CMD_SHUTDOWN:", src, re.S
    )
    force_key = re.search(
        r"case CMD_SET_FORCE_MOVE_KEY:(.*?)\n\s*case CMD_SET_FORCE_MOVE_STATE:",
        src,
        re.S,
    )
    gate = _extract_function(src.splitlines(), "SetRuntimeActionGate")
    assert accepting and re.search(
        r"return SetRuntimeActionGate\(param = \"true\"\)\s*\?\s*1\s*:\s*AHK_RESULT_REJECTED",
        accepting.group(1),
    )
    assert "ReconcileForceMoveState()" in gate
    assert "ClearQueue(-1)" in gate
    assert "accepting && PhysicalStopLatched" in gate
    assert force_key and "ReconcileForceMoveState()" in force_key.group(1)


def test_runtime_gate_is_fail_closed_and_physical_f8_uses_shared_barrier():
    with open(AHK_SCRIPT, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(r"global RuntimeAcceptingActions\s*:=\s*false\b", src)
    assert re.search(r"global MainModeArmed\s*:=\s*false\b", src)
    assert re.search(r"global MainModeF8AwaitRelease\s*:=\s*false\b", src)
    assert re.search(r"global PhysicalStopLatched\s*:=\s*false\b", src)
    intercept = _extract_function(src.splitlines(), "HandleInterceptKey")
    latch = _extract_function(src.splitlines(), "LatchPhysicalStop")
    clear_hooks = _extract_function(src.splitlines(), "ClearAllConfigurableHooks")
    process_queue = _extract_function(src.splitlines(), "ProcessQueue")
    execute_action = _extract_function(src.splitlines(), "ExecuteAction")
    assert "MainModeArmed" in intercept
    assert "MainModeF8AwaitRelease" in intercept
    assert "RegisteredHooks.Count > 0" in intercept
    assert "PhysicalStopLatched || MainModeArmed || RegisteredHooks.Count > 0" in intercept
    assert intercept.index("isActiveF8Stop :=") < intercept.index(
        "if (!IsWheelKey(key))"
    )
    assert intercept.index("isActiveF8Stop && !PhysicalStopLatched") < intercept.index(
        "if (!IsWheelKey(key))"
    )
    assert intercept.index("LatchPhysicalStop(") < intercept.index(
        'QueuePythonReliableEvent("intercept_key_down:" key)'
    )
    arm = _extract_function(src.splitlines(), "ArmMainMode")
    assert arm.index("SetRuntimeActionGate(false)") < arm.index(
        "MainModeArmed := true"
    )
    assert latch.index("PhysicalStopLatched := true") < latch.index(
        "SetRuntimeActionGate(false)"
    )
    assert latch.index("SetRuntimeActionGate(false)") < latch.index(
        "QueuePythonReliableEvent(event, true, true)"
    )
    assert 'Critical "On"' in latch
    assert clear_hooks.index("SetRuntimeActionGate(false)") < clear_hooks.index(
        "ReconcileSkillHoldKeys()"
    ) < clear_hooks.index("PhysicalStopLatched := false")
    assert re.search(
        r"if \(!RuntimeAcceptingActions\)\s*\{\s*return\s*\}", process_queue
    )
    assert re.search(
        r"if \(!RuntimeAcceptingActions\)\s*\{\s*return\s*\}", execute_action
    )


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


def test_late_f8_stop_intent_retries_cleanup_without_restarting_stopped_engine():
    """stop-only F8 在 STOPPED 重试幂等清理，但绝不能执行 STOPPED -> READY。"""
    toggles = []
    cleanups = []
    state = SimpleNamespace(
        _state=MacroState.STOPPED,
        _transition_lock=threading.RLock(),
        _enter_stopped_state=lambda: cleanups.append("cleanup"),
        _handle_f8_press=lambda: toggles.append("direct"),
    )
    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        MacroEngine._handle_ahk_intercept_key(state, "f8_stop")
        publish.assert_not_called()
    assert toggles == []
    assert cleanups == ["cleanup"]

    state._state = MacroState.RUNNING
    MacroEngine._handle_ahk_intercept_key(state, "f8_stop")
    assert toggles == ["direct"]
    assert cleanups == ["cleanup"]


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
    """RUNNING 开闸后仍发一条纵深对齐，且不得晚于生产者恢复。"""
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
