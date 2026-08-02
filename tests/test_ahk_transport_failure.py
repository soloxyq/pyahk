#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK 传输挂死防护回归(P1b)。

历史风险:hold_client 用阻塞的 SendMessageW,AHK 进程**退出**有
ahk_process_died 探测兜底,但进程活着而消息循环**挂死**时,调用线程
(常是 GUI 线程)会永久阻塞 —— 整个应用冻结,没有任何出路。

修复口径:
- 双向都使用 SendMessageTimeoutW,Python→AHK 命令为 500ms,AHK→Python 事件为
  50ms + 1s 失败退避;刻意不加 SMTO_BLOCK,允许交叉 WM_COPYDATA 在等待期间被泵浦;
- 传输失败分类:rejected(业务拒绝,通道正常)/ timeout(挂死)/
  no_window(窗口没了,进程可能退出)/ error(发送层异常兜底);
- timeout(进程仍活着)→ 一次性发布 ahk_transport_failed,与 ahk_process_died 汇入
  同一个延迟 STOPPED 流程,并锁定 F8 → READY 入口;STOPPED 安全清理命令
  force 绕过熔断,至少再真实尝试一次原子止血;
- no_window 不熔断/不锁 F8,下一条命令重新 FindWindow;
- 所有命令走 AHKCommandSender._send 统一包装器,异常不得在
  `_check_send(sender.xxx())` 的参数求值阶段逃逸。
- 失败类型按线程保存;首次 timeout 后普通命令熔断,shutdown 与安全清理命令
  仍可强制尝试。

分类逻辑是纯函数(_classify_send),直接测;流程用 SimpleNamespace 承载状态
经由类调用真实方法(不复制逻辑);接线用源码级钉死。
"""

import os
import re
import sys
import threading
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import hold_client
from hold_client import (
    AHK_RESULT_REJECTED,
    ERROR_TIMEOUT,
    SEND_ERROR,
    SEND_NO_WINDOW,
    SEND_OK,
    SEND_REJECTED,
    SEND_TIMEOUT,
    _classify_send,
)
import torchlight_assistant.core.ahk_command_sender as sender_mod
import torchlight_assistant.core.ahk_input_handler as handler_mod
import torchlight_assistant.core.macro_engine as me
from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
from torchlight_assistant.core.ahk_input_handler import AHKInputHandler
from torchlight_assistant.core.macro_engine import MacroEngine

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# hold_client:结果分类(纯函数)
# ---------------------------------------------------------------------------

def test_classify_processed_result_one_is_ok():
    assert _classify_send(ret=1, msg_result=1, last_error=0) == (True, SEND_OK)


def test_classify_explicit_business_rejection():
    """AHK 明确返回专用值 2:业务拒绝,通道本身正常。"""
    assert _classify_send(
        ret=1, msg_result=AHK_RESULT_REJECTED, last_error=0
    ) == (False, SEND_REJECTED)


def test_classify_processed_zero_is_unhandled_not_business_rejection():
    """0 保留给未处理/默认窗口过程,不能伪装成明确业务拒绝。"""
    assert _classify_send(ret=1, msg_result=0, last_error=0) == (
        False,
        SEND_ERROR,
    )


def test_ahk_business_rejections_use_dedicated_nonzero_result():
    """AHK 的 WM_COPYDATA 业务拒绝必须返回 2；0 只表示未处理。"""
    path = os.path.join(REPO, "hold_server_extended.ahk")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    start = src.index("WM_COPYDATA(wParam, lParam, msg, hwnd) {")
    end_marker = "\n}\n\n; ==============================================================================="
    end = src.index(end_marker, start) + 2
    body = src[start:end]
    assert re.search(r"AHK_RESULT_REJECTED\s*:=\s*2\b", src)
    assert "return AHK_RESULT_REJECTED" in body
    assert not re.search(r"^\s*return\s+0\s*$", body, re.M), (
        "WM_COPYDATA 又用 0 表示业务拒绝，协议结果将不可判别"
    )


def test_classify_timeout_error_is_timeout():
    assert _classify_send(ret=0, msg_result=0, last_error=ERROR_TIMEOUT) == (
        False,
        SEND_TIMEOUT,
    )


def test_classify_other_failures_are_no_window():
    """SMTO_ERRORONEXIT(窗口销毁/线程退出)与无效句柄:窗口没了。"""
    for err in (0, 1400):  # 1400 = ERROR_INVALID_WINDOW_HANDLE
        assert _classify_send(ret=0, msg_result=0, last_error=err) == (
            False,
            SEND_NO_WINDOW,
        )


def test_send_uses_timeout_api_with_reentrant_safe_flags():
    """双向同步 WM_COPYDATA 必须允许等待线程泵浦传入消息。"""
    path = os.path.join(REPO, "hold_client.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(
        r"SMTO_ABORTIFHUNG\s*\|\s*SMTO_ERRORONEXIT", src
    ), "SendMessageTimeoutW 的标志组合被改动"
    assert not re.search(r"^SMTO_BLOCK\s*=", src, re.M), (
        "双向同步 WM_COPYDATA 不能使用 SMTO_BLOCK,否则交叉发送会互相等待"
    )
    assert re.search(r"SEND_TIMEOUT_MS\s*=\s*500\b", src), "发送超时不再是 500ms"
    assert "ctypes.set_last_error(0)" in src, "发送前没有清 last-error,旧超时会污染本次分类"
    assert "SendMessageW(" not in src.replace("SendMessageTimeoutW(", ""), (
        "hold_client 出现无超时的裸 SendMessageW —— AHK 挂死会永久阻塞 GUI 线程"
    )


def test_ahk_to_python_events_also_have_timeout_and_no_block():
    """stats 主动推送不能在 Python GUI 挂死时反过来永久卡住整个 AHK。"""
    path = os.path.join(REPO, "hold_server_extended.ahk")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    fn = re.search(r"^SendWMCopyDataToPython\(.*?^}", src, re.S | re.M)
    assert fn, "未找到 AHK→Python WM_COPYDATA 发送函数"
    body = fn.group(0)
    assert "SendMessageTimeoutW" in body
    assert "SendMessageW\"" not in body
    assert "SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT" in body
    assert "SMTO_BLOCK" not in body
    assert re.search(r"PYTHON_SEND_TIMEOUT_MS\s*:=\s*50\b", src)
    assert re.search(r"PYTHON_SEND_BACKOFF_MS\s*:=\s*1000\b", src)


def test_cached_python_send_failure_opens_backoff_without_same_event_retry():
    """一次回发最多等一个短超时；失败后清缓存并开启退避。"""
    path = os.path.join(REPO, "hold_server_extended.ahk")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    fn = re.search(r"^SendEventToPython\(.*?^}", src, re.S | re.M)
    assert fn, "未找到 SendEventToPython"
    body = fn.group(0)
    cached_branch = body.split("if (CachedPythonHwnd != 0)", 1)[1].split(
        "CachedPythonHwnd := WinExist", 1
    )[0]
    assert cached_branch.count("SendWMCopyDataToPython") == 1
    assert "PythonSendBackoffUntil := now + PYTHON_SEND_BACKOFF_MS" in cached_branch
    assert re.search(r"CachedPythonHwnd\s*:=\s*0", cached_branch)


# ---------------------------------------------------------------------------
# AHKCommandSender._send:统一包装器
# ---------------------------------------------------------------------------

def _sender_state():
    state = object.__new__(AHKCommandSender)
    state.window_title = "TEST_WINDOW"
    state._send_state = threading.local()
    state._transport_failure_kind = ""
    return state


def test_send_wrapper_records_failure_kind_and_opens_circuit():
    state = _sender_state()
    with mock.patch.object(
        sender_mod, "send_ahk_cmd_ex", return_value=(False, SEND_TIMEOUT)
    ):
        assert AHKCommandSender._send(state, 1, "") is False
    assert state.last_failure_kind == SEND_TIMEOUT
    assert state._transport_failure_kind == SEND_TIMEOUT

    with mock.patch.object(
        sender_mod, "send_ahk_cmd_ex", return_value=(True, SEND_OK)
    ) as raw_send:
        assert AHKCommandSender._send(state, 1, "") is False
    raw_send.assert_not_called()


def test_force_send_bypasses_circuit_for_shutdown_and_safe_cleanup():
    state = _sender_state()
    state._transport_failure_kind = SEND_TIMEOUT
    with mock.patch.object(
        sender_mod, "send_ahk_cmd_ex", return_value=(True, SEND_OK)
    ) as raw_send:
        assert state.set_accepting_actions(False, force=True) is True
        assert state.clear_queue(-1, force=True) is True
        assert state.stop_macro(force=True) is True
        assert state.set_skill_hold_keys([], force=True) is True
        assert state.clear_all_configurable_hooks(force=True) is True
        assert state.shutdown() is True
    assert raw_send.call_count == 6
    assert state.last_failure_kind == ""

    with mock.patch.object(sender_mod, "send_ahk_cmd_ex") as blocked:
        assert state.set_accepting_actions(True) is False
        assert state.set_skill_hold_keys(["q"]) is False
    blocked.assert_not_called()


def test_no_window_does_not_open_circuit_and_next_send_can_recover():
    state = _sender_state()
    with mock.patch.object(
        sender_mod,
        "send_ahk_cmd_ex",
        side_effect=[(False, SEND_NO_WINDOW), (True, SEND_OK)],
    ) as raw_send:
        assert AHKCommandSender._send(state, 1, "") is False
        assert state._transport_failure_kind == ""
        assert AHKCommandSender._send(state, 1, "") is True
    assert raw_send.call_count == 2


def test_last_failure_kind_is_thread_local():
    state = _sender_state()
    state.last_failure_kind = SEND_TIMEOUT
    worker_values = []

    def worker():
        worker_values.append(state.last_failure_kind)
        state.last_failure_kind = SEND_NO_WINDOW
        worker_values.append(state.last_failure_kind)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert worker_values == ["", SEND_NO_WINDOW]
    assert state.last_failure_kind == SEND_TIMEOUT, "其他发送线程覆盖了本线程的失败原因"


def test_send_wrapper_never_raises():
    """异常在 `_check_send(sender.xxx())` 参数求值阶段逃逸会绕过全部故障处理。"""
    state = _sender_state()
    logged = []
    with mock.patch.object(
        sender_mod, "send_ahk_cmd_ex", side_effect=RuntimeError("boom")
    ), mock.patch.object(sender_mod, "LOG_ERROR", new=logged.append):
        ok = AHKCommandSender._send(state, 1, "")
    assert ok is False
    assert state.last_failure_kind == SEND_ERROR
    assert logged, "发送层异常被静默吞掉"


def test_all_sender_commands_go_through_the_wrapper():
    """源码级钉死:ahk_command_sender 里不得再出现绕开 _send 的直接发送。"""
    path = os.path.join(
        REPO, "torchlight_assistant", "core", "ahk_command_sender.py"
    )
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    direct = [
        ln
        for ln in src.splitlines()
        if re.search(r"send_ahk_cmd(_ex)?\(", ln)
        and "def _send" not in ln
        and "import" not in ln
        and not ln.strip().startswith("#")
        and "ok, kind = send_ahk_cmd_ex" not in ln
    ]
    assert not direct, f"发现绕开 _send 包装器的直接发送: {direct}"


# ---------------------------------------------------------------------------
# AHKInputHandler._check_send:故障路由
# ---------------------------------------------------------------------------

def _handler_state(kind, alive=True):
    state = SimpleNamespace(
        command_sender=SimpleNamespace(
            last_failure_kind=kind,
            mark_transport_unavailable=lambda value: None,
        ),
        check_ahk_alive=lambda: alive,
        _ahk_transport_failure_notified=False,
    )
    state._last_transport_failure_kind = (
        lambda: AHKInputHandler._last_transport_failure_kind(state)
    )
    return state


def test_check_send_timeout_triggers_transport_notify():
    calls = []
    state = _handler_state(SEND_TIMEOUT, alive=True)
    state._notify_transport_failed = calls.append
    assert AHKInputHandler._check_send(state, False) is False
    assert calls == [SEND_TIMEOUT], "传输超时没有触发挂死上报"


def test_check_send_live_process_without_window_remains_retryable():
    calls = []
    state = _handler_state(SEND_NO_WINDOW, alive=True)
    state._notify_transport_failed = calls.append
    assert AHKInputHandler._check_send(state, False) is False
    assert calls == []


def test_input_handler_marks_only_cleanup_commands_force():
    calls = []

    class Sender:
        def set_accepting_actions(self, enabled, *, force=False):
            calls.append(("gate", enabled, force))
            return True

        def clear_queue(self, priority, *, force=False):
            calls.append(("clear", priority, force))
            return True

        def stop_macro(self, *, force=False):
            calls.append(("stop_macro", force))
            return True

        def set_skill_hold_keys(self, keys, *, force=False):
            calls.append(("holds", list(keys), force))
            return True

        def clear_all_configurable_hooks(self, *, force=False):
            calls.append(("hooks", force))
            return True

    state = SimpleNamespace(
        command_sender=Sender(),
        dry_run_mode=False,
        debug_display_manager=None,
        _check_send=lambda ok: ok,
    )
    assert AHKInputHandler.set_accepting_actions(state, False)
    assert AHKInputHandler.set_accepting_actions(state, True)
    assert AHKInputHandler.clear_queue(state)
    assert AHKInputHandler.stop_macro(state)
    assert AHKInputHandler.set_skill_hold_keys(state, [])
    assert AHKInputHandler.set_skill_hold_keys(state, ["q"])
    assert AHKInputHandler.clear_all_configurable_hooks(state)
    assert calls == [
        ("gate", False, True),
        ("gate", True, False),
        ("clear", -1, True),
        ("stop_macro", True),
        ("holds", [], True),
        ("holds", ["q"], False),
        ("hooks", True),
    ]


def test_check_send_rejected_does_not_trigger_transport_notify():
    """业务拒绝(如闸门关闭)是正常路径,不得当成挂死。"""
    calls = []
    state = _handler_state(SEND_REJECTED, alive=True)
    state._notify_transport_failed = calls.append
    assert AHKInputHandler._check_send(state, False) is False
    assert calls == [], "业务拒绝被误报成传输挂死"


def test_check_send_dead_process_takes_precedence():
    """进程已退出时走 ahk_process_died 流程(check_ahk_alive 内部上报),不重复报挂死。"""
    calls = []
    state = _handler_state(SEND_TIMEOUT, alive=False)
    state._notify_transport_failed = calls.append
    assert AHKInputHandler._check_send(state, False) is False
    assert calls == [], "进程死亡与传输挂死被同时上报"


def test_notify_transport_failed_emits_event_once():
    emitted = []
    marked = []
    state = SimpleNamespace(
        _ahk_transport_failure_notified=False,
        command_sender=SimpleNamespace(mark_transport_unavailable=marked.append),
    )
    fake_bridge = SimpleNamespace(
        ahk_event=SimpleNamespace(emit=lambda s: emitted.append(s))
    )
    logged = []
    with mock.patch.object(handler_mod, "ahk_signal_bridge", fake_bridge), \
         mock.patch.object(handler_mod, "LOG_ERROR", new=logged.append):
        AHKInputHandler._notify_transport_failed(state, SEND_TIMEOUT)
        AHKInputHandler._notify_transport_failed(state, SEND_TIMEOUT)  # 第二次必须无动作
    assert emitted == ["ahk_transport_failed:timeout"], f"事件发布不对: {emitted}"
    assert marked == [SEND_TIMEOUT]
    assert len(logged) == 1, "挂死告警重复刷屏"
    assert "重启" in logged[0], "告警没有明确提示用户重启应用"


# ---------------------------------------------------------------------------
# MacroEngine:锁定 READY 入口 + 延迟停机
# ---------------------------------------------------------------------------

def test_engine_transport_failed_sets_lock_and_schedules_delayed_stop():
    from PySide6.QtCore import QTimer

    state = object.__new__(MacroEngine)
    state._ahk_transport_failed = False
    state.sound_manager = None
    logged = []
    with mock.patch.object(QTimer, "singleShot") as single_shot, \
         mock.patch.object(me, "LOG_ERROR", new=logged.append), \
         mock.patch.object(me.event_bus, "publish"):
        MacroEngine._on_ahk_transport_failed(state)
    assert state._ahk_transport_failed is True, "挂死后没有置锁定标志"
    assert single_shot.called, "没有走延迟 STOPPED 流程"
    assert single_shot.call_args[0][0] == 0
    callback = single_shot.call_args[0][1]
    assert callback.__self__ is state
    assert callback.__func__ is MacroEngine._stop_due_to_ahk_death
    assert any("重启" in ln for ln in logged), "没有明确提示用户重启应用"


def test_f8_ready_entry_is_gated_on_transport_failure():
    """源码级钉死:F8 的 STOPPED 分支必须先检查 _ahk_transport_failed。"""
    path = os.path.join(REPO, "torchlight_assistant", "core", "macro_engine.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    m = re.search(r"def _handle_f8_press\b(.*?)STOPPED状态启动", src, re.S)
    assert m, "未找到 F8 的 STOPPED → READY 分支"
    assert "_ahk_transport_failed" in m.group(1), (
        "F8 STOPPED 分支没有传输挂死闸门 —— 挂死后仍可进 READY(键全发不出去)"
    )
    assert re.search(
        r'event_bus\.subscribe\(\s*"ahk_transport_failed"', src
    ), "引擎没有订阅 ahk_transport_failed 事件"


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
