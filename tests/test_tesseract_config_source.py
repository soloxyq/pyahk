from types import SimpleNamespace

import numpy as np


def test_tesseract_psm_mode_is_bounded_and_non_finite_safe():
    from torchlight_assistant.utils.tesseract_ocr_manager import (
        normalize_tesseract_config,
    )

    assert normalize_tesseract_config({"psm_mode": 13})["psm_mode"] == 13
    assert normalize_tesseract_config({"psm_mode": 14})["psm_mode"] == 7
    assert normalize_tesseract_config({"psm_mode": float("inf")})["psm_mode"] == 7


def test_tesseract_factory_reuses_same_signature_and_rebuilds_on_change():
    from torchlight_assistant.utils import tesseract_ocr_manager as module

    module.reset_tesseract_ocr_manager()
    try:
        first = module.get_tesseract_ocr_manager({"lang": "eng", "psm_mode": 7})
        same = module.get_tesseract_ocr_manager({"lang": "eng", "psm_mode": "7"})
        changed = module.get_tesseract_ocr_manager(
            {"lang": "chi_sim", "psm_mode": 7}
        )

        assert same is first
        assert changed is not first
        assert changed.lang == "chi_sim"
    finally:
        module.reset_tesseract_ocr_manager()


def test_resource_manager_uses_current_tesseract_config_and_skips_same_signature(monkeypatch):
    from torchlight_assistant.core.resource_manager import ResourceManager
    from torchlight_assistant.utils import tesseract_ocr_manager as module

    created = []

    def fake_get(config):
        instance = SimpleNamespace(config=dict(config))
        created.append(instance)
        return instance

    monkeypatch.setattr(module, "get_tesseract_ocr_manager", fake_get)
    manager = object.__new__(ResourceManager)
    manager.hp_config = {}
    manager.mp_config = {}
    manager.check_interval = 200
    manager._ocr_number_box = {}
    manager.tesseract_ocr_manager = None
    manager._tesseract_config_signature = None

    current = {"lang": "eng", "psm_mode": 6}
    manager.update_config({}, current)
    first = manager.tesseract_ocr_manager
    manager.update_config({}, dict(current))
    manager.update_config({}, {"lang": "chi_sim", "psm_mode": 6})

    assert len(created) == 2
    assert first.config == current
    assert manager.tesseract_ocr_manager.config["lang"] == "chi_sim"


def test_gui_tesseract_test_config_comes_from_current_main_window():
    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    widget_like = SimpleNamespace(
        main_window=SimpleNamespace(
            _global_config={"tesseract_ocr": {"lang": "deu", "psm_mode": 8}}
        )
    )

    assert ResourceManagementWidget._get_current_tesseract_config(widget_like) == {
        "lang": "deu",
        "psm_mode": 8,
    }


def test_runtime_tesseract_consumes_origin_converted_roi():
    """绝对桌面坐标不能直接用于切 output/window 局部帧。"""
    from torchlight_assistant.core.resource_manager import ResourceManager

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[5:9, 6:10] = 123
    calls = []

    class Border:
        @staticmethod
        def get_region_from_frame(image, x, y, width, height):
            # 模拟位于左侧副屏、原点为 (-1920, 0) 的局部帧。
            local_x = x + 1920
            local_y = y
            if (
                local_x < 0
                or local_y < 0
                or local_x + width > image.shape[1]
                or local_y + height > image.shape[0]
            ):
                return None
            return image[local_y : local_y + height, local_x : local_x + width]

    class Tesseract:
        @staticmethod
        def recognize_and_parse(image, region):
            calls.append((image.copy(), region))
            return "40/100", 40.0

    manager = object.__new__(ResourceManager)
    manager.border_frame_manager = Border()
    manager.debug_display_manager = None
    manager.tesseract_ocr_manager = Tesseract()
    manager._flask_cooldowns = {}
    manager.hp_config = {
        "enabled": True,
        "cooldown": 0,
        "threshold": 50,
        "detection_mode": "text_ocr",
        "ocr_engine": "tesseract",
        "text_x1": -1914,
        "text_y1": 5,
        "text_x2": -1910,
        "text_y2": 9,
    }
    manager.mp_config = {}

    assert manager._is_resource_low("hp", frame) is True
    assert len(calls) == 1
    image, region = calls[0]
    assert image.shape == (4, 4, 3)
    assert np.all(image == 123)
    assert region == (0, 0, 4, 4)
