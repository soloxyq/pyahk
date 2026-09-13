"""Foreground confirmation without real window activation or wall-clock waits."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from torchlight_assistant.utils import window_utils
from torchlight_assistant.utils.window_utils import WindowUtils


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


@pytest.fixture
def foreground(monkeypatch):
    clock = FakeClock()
    gui = SimpleNamespace(
        IsWindow=Mock(return_value=True),
        GetForegroundWindow=Mock(return_value=200),
    )
    monkeypatch.setattr(window_utils, "time", clock)
    monkeypatch.setattr(window_utils, "win32gui", gui)
    return clock, gui


def test_already_foreground_returns_without_waiting(foreground):
    clock, gui = foreground
    gui.GetForegroundWindow.return_value = 100

    assert WindowUtils.wait_for_foreground(100) is True
    gui.IsWindow.assert_called_once_with(100)
    assert clock.sleeps == []


def test_waits_until_requested_window_becomes_foreground(foreground):
    clock, gui = foreground
    gui.GetForegroundWindow.side_effect = lambda: 100 if clock.now >= 0.02 else 200

    assert WindowUtils.wait_for_foreground(100) is True
    assert clock.now == pytest.approx(0.02)
    assert clock.sleeps == [0.01, 0.01]


def test_timeout_bounds_total_wait_and_final_sleep(foreground):
    clock, gui = foreground

    assert WindowUtils.wait_for_foreground(100, timeout=0.025) is False
    assert clock.now == pytest.approx(0.025)
    assert clock.sleeps == pytest.approx([0.01, 0.01, 0.005])
    assert all(0 < duration <= 0.01 for duration in clock.sleeps)
    assert gui.GetForegroundWindow.call_count == 4


def test_window_destroyed_while_waiting_fails_immediately(foreground):
    clock, gui = foreground
    gui.IsWindow.side_effect = lambda _hwnd: clock.now < 0.01

    assert WindowUtils.wait_for_foreground(100) is False
    assert clock.now == pytest.approx(0.01)
    assert gui.GetForegroundWindow.call_count == 1


@pytest.mark.parametrize("operation", ["IsWindow", "GetForegroundWindow"])
def test_window_api_exception_fails_without_waiting(foreground, operation):
    clock, gui = foreground
    getattr(gui, operation).side_effect = RuntimeError("window query failed")

    assert WindowUtils.wait_for_foreground(100) is False
    assert clock.sleeps == []


@pytest.mark.parametrize("active, expected", [(100, True), (200, False)])
def test_zero_timeout_queries_once_without_sleeping(foreground, active, expected):
    clock, gui = foreground
    gui.GetForegroundWindow.return_value = active

    assert WindowUtils.wait_for_foreground(100, timeout=0) is expected
    gui.GetForegroundWindow.assert_called_once_with()
    assert clock.sleeps == []


def test_invalid_window_handle_is_not_queried(foreground):
    clock, gui = foreground

    assert WindowUtils.wait_for_foreground(0) is False
    gui.IsWindow.assert_not_called()
    assert clock.sleeps == []


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), "invalid"])
def test_invalid_timeout_fails_without_waiting(foreground, timeout):
    clock, gui = foreground

    assert WindowUtils.wait_for_foreground(100, timeout=timeout) is False
    gui.IsWindow.assert_not_called()
    assert clock.sleeps == []


def test_unavailable_window_api_fails_closed(monkeypatch):
    monkeypatch.setattr(window_utils, "win32gui", None)

    assert WindowUtils.wait_for_foreground(100) is False
