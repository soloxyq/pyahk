"""Behavior checks for one wire owner, stop priority and native-only GUI waits."""

import ctypes
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

import hold_client
from hold_client import SEND_ERROR, SEND_OK, SEND_REJECTED, SEND_TIMEOUT
from torchlight_assistant.core.command_transport import CommandTransport
from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
import torchlight_assistant.core.ahk_command_sender as sender_module


def _wait_pending(transport, ordinary=0, safety=0):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with transport._condition:
            if len(transport._ordinary) == ordinary and len(transport._safety) == safety:
                return
        time.sleep(0.001)
    raise AssertionError("command was not admitted to the mailbox")


def test_two_sender_facades_share_a_single_wire_owner(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []

    def wire(_title, cmd, _param):
        calls.append((cmd, threading.get_ident()))
        if cmd == 100:
            entered.set()
            assert release.wait(1)
        return True, SEND_OK

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", wire)
    title = "mailbox-" + uuid4().hex
    first, second = AHKCommandSender(title), AHKCommandSender(title)
    calls.clear()
    a = threading.Thread(target=lambda: results.append(first._send(100)))
    b = threading.Thread(target=lambda: results.append(second._send(101)))
    try:
        a.start()
        assert entered.wait(1)
        b.start()
        transport = sender_module.get_command_transport(title)
        _wait_pending(transport, ordinary=1)
        assert [cmd for cmd, _thread in calls] == [100]
    finally:
        release.set()
        a.join(1)
        b.join(1)
    assert results == [True, True]
    assert [cmd for cmd, _thread in calls] == [100, 101]
    assert len({thread for _cmd, thread in calls}) == 1


def test_stop_barrier_cancels_pending_old_work_and_precedes_ordinary():
    transport = CommandTransport()
    entered, release = threading.Event(), threading.Event()
    calls, results = [], {}

    def blocked():
        calls.append("inflight")
        entered.set()
        assert release.wait(1)
        return True, SEND_OK

    def operation(name):
        calls.append(name)
        return True, SEND_OK

    a = threading.Thread(target=lambda: results.setdefault("a", transport.send(blocked)))
    b = threading.Thread(target=lambda: results.setdefault("b", transport.send(lambda: operation("stale"))))
    stop = threading.Thread(target=lambda: results.setdefault(
        "stop", transport.send(lambda: operation("stop"), safety=True, barrier=True)
    ))
    try:
        a.start()
        assert entered.wait(1)
        b.start()
        _wait_pending(transport, ordinary=1)
        stop.start()
        _wait_pending(transport, safety=1)
        b.join(1)
        assert results["b"] == (False, SEND_REJECTED)
    finally:
        release.set()
        for thread in (a, b, stop):
            thread.join(1)
    assert calls == ["inflight", "stop"]
    assert results["stop"] == (True, SEND_OK)


def test_expired_queued_command_is_never_sent_later():
    transport = CommandTransport()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked():
        entered.set()
        assert release.wait(1)
        return True, SEND_OK

    thread = threading.Thread(target=lambda: transport.send(blocked))
    try:
        thread.start()
        assert entered.wait(1)
        transport.QUEUE_TIMEOUT_SECONDS = 0.02
        assert transport.send(lambda: calls.append("late")) == (False, SEND_REJECTED)
    finally:
        release.set()
        thread.join(1)
    assert transport.send(lambda: (True, SEND_OK)) == (True, SEND_OK)
    assert calls == []


def test_wire_failure_blocks_already_queued_business_commands(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []

    def wire(_title, cmd, _param):
        calls.append(cmd)
        if cmd == 100:
            entered.set()
            assert release.wait(1)
            return False, SEND_ERROR
        return True, SEND_OK

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", wire)
    sender = AHKCommandSender("circuit-" + uuid4().hex)
    calls.clear()
    a = threading.Thread(target=lambda: results.append(sender._send(100)))
    b = threading.Thread(target=lambda: results.append(sender._send(101)))
    try:
        a.start()
        assert entered.wait(1)
        b.start()
        _wait_pending(sender_module.get_command_transport(sender.window_title), ordinary=1)
    finally:
        release.set()
        a.join(1)
        b.join(1)
    assert results == [False, False]
    assert calls == [100]
    assert sender.reset_runtime() is True
    assert calls[-1] == 28


@pytest.mark.parametrize("failure_kind", [SEND_ERROR, SEND_TIMEOUT])
def test_wire_failure_is_shared_by_facades_and_blocks_queued_work(monkeypatch, failure_kind):
    entered, release = threading.Event(), threading.Event()
    calls, results = [], {}

    def wire(_title, cmd, _param):
        calls.append(cmd)
        if cmd == 100:
            entered.set()
            assert release.wait(1)
            return False, failure_kind
        return True, SEND_OK

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", wire)
    title = "shared-circuit-" + uuid4().hex
    first, second = AHKCommandSender(title), AHKCommandSender(title)
    calls.clear()
    a = threading.Thread(target=lambda: results.setdefault("first", first._send(100)))
    b = threading.Thread(target=lambda: results.setdefault("second", second._send(101)))
    try:
        a.start()
        assert entered.wait(1)
        b.start()
        _wait_pending(sender_module.get_command_transport(title), ordinary=1)
    finally:
        release.set()
        a.join(1)
        b.join(1)
    assert results == {"first": False, "second": False}
    assert calls == [100]
    assert first.transport_unavailable and second.transport_unavailable

    # Another facade can clean up, but success must not reopen the circuit.
    assert second.reset_runtime() is True
    assert calls == [100, 28]
    assert first.transport_unavailable and second.transport_unavailable
    with pytest.raises(ConnectionError):
        AHKCommandSender(title)
    assert calls == [100, 28]
    assert first._send(102) is False

    # Cooldown and a single recovery PING are shared; no business is replayed.
    assert second.recover_transport() is False
    assert calls == [100, 28]
    first._next_transport_probe_at = 0.0
    assert second.recover_transport() is True
    assert not first.transport_unavailable and not second.transport_unavailable
    assert first._send(103) is True
    assert calls == [100, 28, 1, 103]


def test_new_facade_can_recover_existing_circuit_after_cooldown(monkeypatch):
    calls = []

    def wire(_title, cmd, _param):
        calls.append(cmd)
        return True, SEND_OK

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", wire)
    title = "recreated-facade-" + uuid4().hex
    original = AHKCommandSender(title)
    original.mark_transport_unavailable(SEND_TIMEOUT)
    calls.clear()
    with pytest.raises(ConnectionError):
        AHKCommandSender(title)
    assert calls == []
    assert original.transport_unavailable

    original._next_transport_probe_at = 0.0
    replacement = AHKCommandSender(title)
    assert calls == [1]
    assert not replacement.transport_unavailable
    assert not original.transport_unavailable


def test_shared_recovery_probe_excludes_other_facades(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, result = [], []

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", lambda *_: (True, SEND_OK))
    title = "shared-probe-" + uuid4().hex
    first, second = AHKCommandSender(title), AHKCommandSender(title)
    first.mark_transport_unavailable(SEND_TIMEOUT)
    first._next_transport_probe_at = 0.0

    def probe(_title, cmd, _param):
        calls.append(cmd)
        entered.set()
        assert release.wait(1)
        return True, SEND_OK

    monkeypatch.setattr(sender_module, "send_ahk_cmd_ex", probe)
    worker = threading.Thread(target=lambda: result.append(first.recover_transport()))
    try:
        worker.start()
        assert entered.wait(1)
        # A competing facade cannot probe even if cooldown expires in flight.
        second._next_transport_probe_at = 0.0
        assert second.recover_transport() is False
        assert calls == [1]
    finally:
        release.set()
        worker.join(1)
    assert result == [True]
    assert not first.transport_unavailable and not second.transport_unavailable


def test_gui_wait_services_reverse_native_ipc_without_running_qt_tasks():
    # 其他 Qt 用例会创建 offscreen QApplication；其虚拟 winId 不是 HWND。
    # 原生 IPC 必须在独立的 Windows Qt 进程验证，不能复用 offscreen 单例。
    import os
    from pathlib import Path
    import subprocess
    import sys

    env = dict(os.environ, QT_QPA_PLATFORM="windows")
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['_assert_native_gui_wait']()",
         str(Path(__file__).resolve())],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_native_gui_wait():
    from ctypes import wintypes
    from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    window = QWidget()
    hwnd = int(window.winId())  # hidden, never shown or activated
    custom_message = 0x8000 + 321
    native_seen, queued_seen = [], []
    gui_thread = threading.get_ident()

    class Filter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, _kind, message):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == custom_message:
                native_seen.append(threading.get_ident())
                return True, 7
            return False, 0

    event_filter = Filter()
    app.installNativeEventFilter(event_filter)

    def wire():
        result = ctypes.c_size_t()
        sent = hold_client.SendMessageTimeoutW(
            hwnd, custom_message, 0, 0,
            hold_client.SMTO_ABORTIFHUNG | hold_client.SMTO_ERRORONEXIT,
            200, ctypes.byref(result),
        )
        return bool(sent and result.value == 7), SEND_OK

    try:
        QTimer.singleShot(0, lambda: queued_seen.append("qt"))
        assert CommandTransport().send(wire) == (True, SEND_OK)
        assert native_seen == [gui_thread]
        assert queued_seen == []
        app.processEvents()
        assert queued_seen == ["qt"]
    finally:
        app.removeNativeEventFilter(event_filter)
        window.close()


def test_native_window_cache_is_scoped_to_server_title(monkeypatch):
    monkeypatch.setattr(hold_client, "_ahk_hwnds", {})
    monkeypatch.setattr(hold_client, "FindWindowW", lambda _class, title: {"one": 11, "two": 22}[title])
    targets = []

    def send(hwnd, _msg, _wp, _lp, _flags, _timeout, result):
        targets.append(hwnd)
        ctypes.cast(result, ctypes.POINTER(ctypes.c_size_t)).contents.value = 1
        return 1

    monkeypatch.setattr(hold_client, "SendMessageTimeoutW", send)
    for title in ("one", "two", "one"):
        assert hold_client.send_ahk_cmd_ex(title, 1) == (True, SEND_OK)
    assert targets == [11, 22, 11]


@pytest.mark.parametrize("kind", [SEND_ERROR, SEND_TIMEOUT])
def test_unhandled_protocol_reply_is_a_transport_failure(kind):
    from torchlight_assistant.core.ahk_input_handler import AHKInputHandler

    state = SimpleNamespace(command_sender=SimpleNamespace(last_failure_kind=kind))
    assert AHKInputHandler._last_transport_failure_kind(state) == kind
