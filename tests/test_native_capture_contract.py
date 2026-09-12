"""Regression checks for the native capture ABI and coordinate contract.

These are deliberately hardware-free: DXGI output selection is exercised on a
Windows desktop in integration testing, while this suite pins the source/API
contract so future refactors cannot silently return to adapter-only selection
or output-local Python coordinates.
"""

import ctypes
import os
from pathlib import Path

import numpy as np

from native_capture.python_wrapper import CaptureFrame, GameCaptureLib
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager
from torchlight_assistant.utils.native_graphics_capture_manager import (
    CaptureConfig,
    NativeGraphicsCaptureManager,
)


REPO = Path(__file__).resolve().parents[1]


def test_capture_frame_matches_64bit_size_t_abi():
    """C++ CaptureFrame.data_size is size_t, never a 32-bit c_int."""
    assert CaptureFrame.data_size.offset == 32
    assert CaptureFrame._fields_[5][1] is ctypes.c_size_t
    assert CaptureFrame.format.offset == 40
    assert ctypes.sizeof(CaptureFrame) == 48


def test_checked_in_windows_dll_exports_the_current_wrapper_api():
    """The deployed DLL must be rebuilt whenever the native ABI changes."""
    if os.name != "nt":
        return

    dll_path = REPO / "native_capture" / "capture_lib.dll"
    wrapper = GameCaptureLib(str(dll_path))
    assert wrapper._lib.capture_get_frame_rect is not None


def test_native_source_uses_adapter_and_output_and_refcounted_cleanup():
    source = (REPO / "native_capture" / "capture_lib.cpp").read_text(encoding="utf-8")
    header = (REPO / "native_capture" / "capture_lib.h").read_text(encoding="utf-8")

    assert "int output_index" in source
    assert "EnumOutputs(session->output_selection.output_index" in source
    assert "GetOutputSelectionByGlobalIndex" in source
    assert "g_init_refcount" in source
    assert "std::recursive_mutex g_api_mutex" in source
    assert "capture_get_frame_rect" in header


def test_absolute_desktop_coordinates_are_converted_using_frame_origin():
    manager = object.__new__(BorderFrameManager)
    manager._frame_origin = (-1920, 0)
    frame = np.zeros((100, 200, 4), dtype=np.uint8)

    region = manager.get_region_from_frame(frame, -1900, 10, 20, 15)

    assert region is not None
    assert region.shape == (15, 20, 4)


def test_runtime_capture_config_rejects_string_boolean_without_mutating_state():
    manager = NativeGraphicsCaptureManager(CaptureConfig(enable_region=False))
    manager.is_running = True
    manager.session_id = 1
    manager._frame_rect = {"x": 0, "y": 0, "width": 10, "height": 10}
    manager.capture_manager = type(
        "CaptureStub",
        (),
        {"set_capture_config": lambda *_args: True},
    )()

    assert manager.set_capture_config({"enable_region": "false"}) is False
    assert manager.config.enable_region is False
    assert manager._frame_rect == {"x": 0, "y": 0, "width": 10, "height": 10}
    manager.is_running = False
    manager.session_id = None
    manager.capture_manager = None


def test_native_wrapper_rejects_bool_fraction_and_c_int_overflow():
    wrapper = object.__new__(GameCaptureLib)
    wrapper._initialized = False
    wrapper._sessions = {}

    for bad_config in (
        {"capture_interval_ms": True},
        {"capture_interval_ms": 1.5},
        {"capture_interval_ms": 2**31},
        {"enable_region": 1},
        {"enable_region": True, "region": {"width": True, "height": 10}},
        {"enable_region": True, "region": {"width": 10, "height": 0}},
    ):
        try:
            wrapper._dict_to_capture_config(bad_config)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid native config was accepted: {bad_config!r}")

    parsed = wrapper._dict_to_capture_config(
        {
            "capture_interval_ms": 0,
            "enable_region": True,
            "region": {"x": -1920, "y": 0, "width": 640, "height": 480},
        }
    )
    assert parsed.capture_interval_ms == 0
    assert parsed.enable_region == 1
    assert (parsed.region.x, parsed.region.y) == (-1920, 0)
