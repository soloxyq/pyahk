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
