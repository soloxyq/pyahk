"""Exercise the real wrapper interface, without loading DXGI or sending input."""

from unittest.mock import Mock

from native_capture.python_wrapper import CaptureManager, GameCaptureLib
from torchlight_assistant.utils.native_graphics_capture_manager import (
    CaptureConfig,
    NativeGraphicsCaptureManager,
)


def test_running_capture_configuration_roundtrips_through_real_wrapper():
    low_level = Mock(spec_set=GameCaptureLib)
    low_level.set_config.return_value = True
    native = object.__new__(CaptureManager)
    native._initialized = True
    native._capture_lib = low_level
    manager = NativeGraphicsCaptureManager(CaptureConfig())
    manager.is_running = True
    manager.session_id = 7
    manager.capture_manager = native
    try:
        assert manager.set_capture_config({"capture_interval_ms": 99}) is True
        sent_config = low_level.set_config.call_args.args[1]
        assert low_level.set_config.call_args.args[0] == 7
        assert sent_config["capture_interval_ms"] == 99
        assert manager.config.capture_interval_ms == 99
        low_level.get_config.return_value = sent_config
        assert manager.get_capture_config() == sent_config
        low_level.get_config.assert_called_once_with(7)

        low_level.set_config.return_value = False
        assert manager.set_capture_config({"capture_interval_ms": 88}) is False
        assert manager.config.capture_interval_ms == 99
    finally:
        manager.is_running = False
        manager.session_id = None
        manager.capture_manager = None
        native._initialized = False


def test_start_capture_reclaims_session_when_native_start_fails():
    native = Mock()
    native.create_window_session.return_value = 41
    native.start_capture.return_value = False
    # Reaping must continue to destroy the session even if stopping the failed
    # native session itself reports an error.
    native.stop_capture.side_effect = RuntimeError("already stopped")

    manager = NativeGraphicsCaptureManager(
        CaptureConfig(target_window_handle=1234)
    )
    manager.capture_manager = native
    manager._frame_rect = {"x": 10, "y": 20, "width": 30, "height": 40}

    assert manager.start_capture() is False

    native.stop_capture.assert_called_once_with(41)
    native.destroy_session.assert_called_once_with(41)
    assert manager.session_id is None
    assert manager._frame_rect is None
    assert manager.is_running is False


def test_stop_capture_destroys_session_when_native_stop_fails():
    native = Mock()
    native.stop_capture.side_effect = RuntimeError("already stopped")

    manager = NativeGraphicsCaptureManager(CaptureConfig())
    manager.capture_manager = native
    manager.session_id = 73
    manager.is_running = True
    manager._frame_rect = {"x": 1, "y": 2, "width": 3, "height": 4}

    manager.stop_capture()

    native.stop_capture.assert_called_once_with(73)
    native.destroy_session.assert_called_once_with(73)
    assert manager.session_id is None
    assert manager._frame_rect is None
    assert manager.is_running is False
