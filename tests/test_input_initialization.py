"""构造回滚和迟到传输通知；不启动真实 AHK，也不发送按键。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from torchlight_assistant.core import ahk_input_handler as module
from torchlight_assistant.core.ahk_input_handler import AHKInputHandler


@pytest.mark.parametrize("failure", ["connection", "signal"])
def test_partial_input_initialization_cleans_owned_process(monkeypatch, failure):
    process = Mock()
    process.poll.return_value = None
    sender = Mock()
    sender.shutdown.return_value = True

    def start(handler):
        handler.ahk_process = process
        return True

    def connect(*args):
        if failure == "signal":
            raise RuntimeError("signal connect failed")

    def make_sender(*args):
        if failure == "connection":
            raise ConnectionError("handshake failed")
        return sender

    monkeypatch.setattr(module.AHKConfig, "validate", lambda: True)
    monkeypatch.setattr(AHKInputHandler, "_start_ahk_server", start)
    monkeypatch.setattr(module, "AHKCommandSender", make_sender)
    monkeypatch.setattr(module, "ahk_signal_bridge", SimpleNamespace(
        ahk_event=SimpleNamespace(connect=connect),
    ))
    with pytest.raises(RuntimeError):
        AHKInputHandler()
    process.wait.assert_called_once()
    if failure == "connection":
        process.terminate.assert_called_once()
    else:
        sender.shutdown.assert_called_once()
        process.terminate.assert_not_called()


def test_late_failure_notification_does_not_reopen_recovered_circuit(monkeypatch):
    emitted = Mock()
    monkeypatch.setattr(module, "ahk_signal_bridge", SimpleNamespace(
        ahk_event=SimpleNamespace(emit=emitted),
    ))
    sender = SimpleNamespace(transport_unavailable=False,
                             mark_transport_unavailable=Mock())
    handler = SimpleNamespace(command_sender=sender,
                              _ahk_transport_failure_notified=False)
    AHKInputHandler._notify_transport_failed(handler, "timeout")
    assert not handler._ahk_transport_failure_notified
    sender.mark_transport_unavailable.assert_not_called()
    emitted.assert_not_called()


@pytest.mark.parametrize("event,unavailable,expected", [
    ("ahk_transport_failed:timeout", False, False),
    ("ahk_transport_failed:timeout", True, True),
    ("ahk_transport_recovered:", True, False),
    ("ahk_transport_recovered:", False, True),
])
def test_queued_transport_notice_checks_current_channel(event, unavailable, expected):
    handler = object.__new__(AHKInputHandler)
    handler.ahk_process = None
    handler.command_sender = SimpleNamespace(transport_unavailable=unavailable)
    handler.event_bus = SimpleNamespace(publish=Mock())
    handler._on_ahk_event(event)
    assert handler.event_bus.publish.called is expected
