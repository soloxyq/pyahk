#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资源配置 load → gather → save 往返测试。

GUI 只认识自己有控件的字段,而 JSON 里还有一批**没有控件**的用户设置
(ocr_model / ocr_device 是 wiki/04 文档化的配置项,match_threshold 会作为
识别置信度门槛传给 recognize_and_parse)。

如果构建配置时从空字典重建,这些字段每次保存或 F8 同步都会被静默抹掉 ——
手工配好 GPU / medium 模型的用户,按一次 F8 就退回 CPU + small,且没有任何提示,
表现为"我明明配了 GPU,怎么还是 CPU 在跑"。

这里用假控件跑真实的 ResourceConfigManager.build_resource_config,
断言往返之后隐藏字段原样保留。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:
    pytest = None

from torchlight_assistant.gui.resource_config_manager import ResourceConfigManager


class _FakeCheck:
    def __init__(self, v=True):
        self._v = v

    def isChecked(self):
        return self._v


class _FakeText:
    def __init__(self, t=""):
        self._t = t

    def text(self):
        return self._t


class _FakeSpin:
    def __init__(self, v=50):
        self._v = v

    def value(self):
        return self._v


class _FakeCombo:
    def __init__(self, data="paddle"):
        self._data = data

    def currentData(self):
        return self._data


def _widgets(coord_text="100,200,300,400"):
    return {
        "enabled": _FakeCheck(True),
        "key": _FakeText("1"),
        "threshold": _FakeSpin(60),
        "coord_input": _FakeText(coord_text),
        "tolerance_input": _FakeText("10,30,50"),
        "ocr_engine_combo": _FakeCombo("paddle"),
    }


def _build(existing, mode="text_ocr"):
    return ResourceConfigManager.build_resource_config(
        "hp", _widgets(), mode, {}, None, existing_config=existing
    )


# ---------------------------------------------------------------------------

def test_hidden_ocr_fields_survive_roundtrip():
    """ocr_model / ocr_device 没有对应控件,必须原样保留。"""
    existing = {
        "enabled": True,
        "key": "1",
        "detection_mode": "text_ocr",
        "ocr_engine": "paddle",
        "ocr_model": "PP-OCRv6_medium_rec",
        "ocr_device": "gpu",
    }
    out = _build(existing)
    assert out["ocr_model"] == "PP-OCRv6_medium_rec", "ocr_model 被抹掉了"
    assert out["ocr_device"] == "gpu", "ocr_device 被抹掉了(会静默退回 CPU)"


def test_match_threshold_is_not_reset_to_default():
    """match_threshold 是用户可调的识别置信度门槛,UI 上没有控件,
    写死 0.70 等于每次保存都把用户调过的阈值改回默认值。"""
    out = _build({"match_threshold": 0.85})
    assert out["match_threshold"] == 0.85, (
        f"match_threshold 被重置成了 {out['match_threshold']}"
    )
    # 没有历史值时才用默认
    assert _build({})["match_threshold"] == 0.70


def test_user_rectangle_is_preserved_as_backup():
    """text_ocr 模式会"保留矩形配置作为备份",但备份的应该是用户自己框过的矩形,
    而不是把默认值写回去。"""
    out = _build({"region_x1": 11, "region_y1": 22, "region_x2": 33, "region_y2": 44})
    assert (out["region_x1"], out["region_y1"], out["region_x2"], out["region_y2"]) == (
        11, 22, 33, 44
    ), "用户框过的矩形被默认值覆盖了"


def test_ui_owned_fields_still_win():
    """合并不能反过来让旧值盖住 UI:UI 拥有的字段必须以控件为准。"""
    out = _build({
        "enabled": False, "key": "9", "threshold": 5,
        "text_x1": 1, "text_y1": 2, "text_x2": 3, "text_y2": 4,
        "ocr_engine": "tesseract",
        "tolerance_h": 1, "tolerance_s": 2, "tolerance_v": 3,
    })
    assert out["enabled"] is True
    assert out["key"] == "1"
    assert out["threshold"] == 60
    assert (out["text_x1"], out["text_y1"], out["text_x2"], out["text_y2"]) == (
        100, 200, 300, 400
    )
    assert out["ocr_engine"] == "paddle"
    assert (out["tolerance_h"], out["tolerance_s"], out["tolerance_v"]) == (10, 30, 50)
    assert out["detection_mode"] == "text_ocr"


def test_unknown_future_fields_survive():
    """护栏:将来新增的、UI 暂时没做控件的字段也不该被吞掉。"""
    out = _build({"some_future_knob": 123})
    assert out.get("some_future_knob") == 123


def test_no_existing_config_still_builds():
    """首次配置(没有历史)时仍要能正常构建,不能抛异常。"""
    out = _build(None)
    assert out["detection_mode"] == "text_ocr"
    assert out["enabled"] is True


def test_invalid_tolerance_input_is_not_replaced_with_live_defaults():
    for invalid in ("", "True,30,50", "10,,30,50", "180,30,50", "10,30,256"):
        widgets = _widgets()
        widgets["tolerance_input"] = _FakeText(invalid)
        out = ResourceConfigManager.build_resource_config(
            "hp", widgets, "rectangle", {}, None, existing_config={}
        )
        assert tuple(out[key] for key in ("tolerance_h", "tolerance_s", "tolerance_v")) == (None, None, None)


def test_other_detection_modes_also_merge():
    """圆形/矩形模式同样从旧配置合并,不该只修 text_ocr 一条路径。"""
    for mode in ("circle", "rectangle"):
        out = ResourceConfigManager.build_resource_config(
            "hp", _widgets(), mode, {}, None,
            existing_config={"ocr_model": "PP-OCRv6_medium_rec", "ocr_device": "gpu"},
        )
        assert out.get("ocr_model") == "PP-OCRv6_medium_rec", f"{mode} 模式抹掉了 ocr_model"
        assert out.get("ocr_device") == "gpu", f"{mode} 模式抹掉了 ocr_device"


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
