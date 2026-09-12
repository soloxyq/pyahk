#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GUI 配置往返与技能检测的失败安全回归测试。"""

import os
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qt_app():
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    return QApplication.instance() or QApplication([])


def test_cooldown_detection_fails_closed_without_frame_or_valid_size():
    from torchlight_assistant.core.skill_manager import SkillManager

    manager = object.__new__(SkillManager)
    manager.debug_display_manager = None
    manager.border_frame_manager = SimpleNamespace(
        compare_cooldown_image=lambda *_args, **_kwargs: 100.0
    )
    base = {
        "TriggerMode": 1,
        "CooldownCoordX": 20,
        "CooldownCoordY": 30,
        "CooldownSize": 12,
    }

    assert manager._check_cooldown_ready("skill", base, None) is False
    assert manager._check_cooldown_ready(
        "skill", {**base, "CooldownSize": 0}, np.zeros((4, 4, 4), dtype=np.uint8)
    ) is False
    assert manager._check_cooldown_ready(
        "skill", {**base, "CooldownCoordX": "bad"}, np.zeros((4, 4, 4), dtype=np.uint8)
    ) is False
    assert manager._check_cooldown_ready(
        "skill", {**base, "CooldownCoordX": float("inf")}, np.zeros((4, 4, 4), dtype=np.uint8)
    ) is False


def test_cooldown_detection_preserves_negative_virtual_desktop_coordinates():
    from torchlight_assistant.core.skill_manager import SkillManager

    calls = []
    manager = object.__new__(SkillManager)
    manager.debug_display_manager = None
    manager.border_frame_manager = SimpleNamespace(
        compare_cooldown_image=lambda *args, **kwargs: calls.append((args, kwargs)) or 100.0
    )
    frame = np.zeros((12, 12, 4), dtype=np.uint8)

    assert manager._check_cooldown_ready(
        "skill",
        {
            "TriggerMode": 1,
            "CooldownCoordX": -1920,
            "CooldownCoordY": -40,
            "CooldownSize": 12,
        },
        frame,
    ) is True
    assert calls[0][0][1:5] == (-1920, -40, "skill", 12)


def test_condition_detection_accepts_origin_and_fails_closed_on_unknown_frame():
    from torchlight_assistant.core.skill_manager import SkillManager

    calls = []
    manager = object.__new__(SkillManager)
    manager.border_frame_manager = SimpleNamespace(
        get_region_from_frame=lambda *_args: np.zeros((1, 1, 4), dtype=np.uint8)
    )
    manager._evaluate_condition = (
        lambda *args: calls.append(args) or False
    )
    config = {
        "ExecuteCondition": 2,
        "ConditionCoordX": 0,
        "ConditionCoordY": -30,
    }
    frame = np.zeros((1, 1, 4), dtype=np.uint8)

    assert manager._check_execution_conditions("skill", config, frame) is False
    assert calls[0][2]["ConditionCoordX"] == 0
    assert calls[0][2]["ConditionCoordY"] == -30
    assert manager._check_execution_conditions("skill", config, None) is None


def test_condition_detection_fails_closed_when_coordinate_is_outside_frame():
    from torchlight_assistant.core.skill_manager import SkillManager

    manager = object.__new__(SkillManager)
    manager.border_frame_manager = SimpleNamespace(
        get_region_from_frame=lambda *_args: None
    )
    manager._evaluate_condition = lambda *_args: (_ for _ in ()).throw(
        AssertionError("越界坐标不应进入业务条件判断")
    )

    assert manager._check_execution_conditions(
        "skill",
        {
            "ExecuteCondition": 1,
            "ConditionCoordX": -99999,
            "ConditionCoordY": 0,
        },
        np.zeros((1, 1, 4), dtype=np.uint8),
    ) is None


def test_window_activation_switch_to_class_only_clears_executable():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import WindowActivationWidget

    widget = WindowActivationWidget()
    try:
        widget.update_from_config(
            {
                "window_activation": {
                    "enabled": True,
                    "ahk_class": "OldClass",
                    "ahk_exe": "old-game.exe",
                }
            }
        )
        assert widget.widgets["exe"].currentText() == "old-game.exe"

        widget.update_from_config(
            {
                "window_activation": {
                    "enabled": True,
                    "ahk_class": "NewClass",
                    "ahk_exe": "",
                }
            }
        )
        saved = widget.get_config()["window_activation"]
        assert saved["ahk_class"] == "NewClass"
        assert saved["ahk_exe"] == ""
    finally:
        widget.close()


def test_loading_window_config_does_not_reinfer_or_clear_configured_class(monkeypatch):
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import WindowActivationWidget
    from torchlight_assistant.gui.main_window import GameSkillConfigUI
    from torchlight_assistant.utils.window_utils import WindowUtils

    # 配置中的进程允许当前没有运行；加载配置不是一次用户选进程操作，不能因此
    # 自动探测并清掉用户明确填写的 class。
    monkeypatch.setattr(WindowUtils, "get_window_class_by_process", lambda _name: "")
    widget = WindowActivationWidget()
    fake_main = SimpleNamespace(window_activation=widget, _updating_ui=True)
    widget.widgets["exe"].currentTextChanged.connect(
        lambda text: GameSkillConfigUI._on_process_selection_changed(fake_main, text)
    )
    try:
        widget.update_from_config(
            {
                "window_activation": {
                    "enabled": True,
                    "ahk_class": "ConfiguredClass",
                    "ahk_exe": "offline-game.exe",
                }
            }
        )
        assert widget.widgets["class"].text() == "ConfiguredClass"
        assert widget.widgets["exe"].currentText() == "offline-game.exe"
    finally:
        widget.close()


def test_missing_resource_sections_reset_both_flasks_to_disabled():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    widget = ResourceManagementWidget()
    try:
        widget.update_from_config(
            {
                "resource_management": {
                    "hp_config": {
                        "enabled": True,
                        "detection_mode": "rectangle",
                        "region_x1": 1,
                        "region_y1": 2,
                        "region_x2": 3,
                        "region_y2": 4,
                    },
                    "mp_config": {
                        "enabled": True,
                        "detection_mode": "rectangle",
                        "region_x1": 5,
                        "region_y1": 6,
                        "region_x2": 7,
                        "region_y2": 8,
                    },
                }
            }
        )
        assert widget.hp_widgets["enabled"].isChecked()
        assert widget.mp_widgets["enabled"].isChecked()

        widget.update_from_config({"resource_management": {}})
        assert not widget.hp_widgets["enabled"].isChecked()
        assert not widget.mp_widgets["enabled"].isChecked()
        assert widget.hp_widgets["coord_input"].text() == "136,910,213,1004"
        assert widget.mp_widgets["coord_input"].text() == "1552,910,1560,1004"
        assert widget.hp_circle_config == {}
        assert widget.mp_circle_config == {}

        widget.update_from_config(
            {"resource_management": {"hp_config": {"enabled": True}}}
        )
        assert widget.hp_widgets["enabled"].isChecked()
        assert not widget.mp_widgets["enabled"].isChecked()
        saved = widget.get_config()["resource_management"]["hp_config"]
        assert [saved[key] for key in ("region_x1", "region_y1", "region_x2", "region_y2")] == [0, 0, 0, 0]
    finally:
        widget.close()


def test_resource_ocr_engine_does_not_leak_across_profiles():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    widget = ResourceManagementWidget()
    try:
        widget.update_from_config(
            {
                "resource_management": {
                    "hp_config": {
                        "enabled": True,
                        "detection_mode": "text_ocr",
                        "ocr_engine": "tesseract",
                    }
                }
            }
        )
        assert widget.hp_widgets["ocr_engine_combo"].currentData() == "tesseract"

        # B profile 暂时使用 rectangle，OCR 下拉框虽隐藏也必须预先同步；否则用户
        # 之后切换到 text_ocr 会继承 A profile 的 tesseract。
        widget.update_from_config(
            {
                "resource_management": {
                    "hp_config": {
                        "enabled": True,
                        "detection_mode": "rectangle",
                        "ocr_engine": "template",
                    }
                }
            }
        )
        assert widget.hp_widgets["ocr_engine_combo"].currentData() == "template"
        widget.hp_widgets["mode_combo"].setCurrentIndex(2)
        assert widget.hp_widgets["ocr_engine_combo"].currentData() == "template"
    finally:
        widget.close()


def test_resource_roundtrip_removes_legacy_unused_color_profiles():
    from torchlight_assistant.gui.resource_config_manager import ResourceConfigManager

    class _Check:
        def isChecked(self):
            return True

    class _Text:
        def __init__(self, value):
            self._value = value

        def text(self):
            return self._value

    class _Spin:
        def value(self):
            return 50

    colors = [
        {
            "name": "custom",
            "target_h": 7,
            "target_s": 8,
            "target_v": 9,
            "tolerance_h": 40,
            "tolerance_s": 50,
            "tolerance_v": 60,
        }
    ]
    result = ResourceConfigManager.build_resource_config(
        "hp",
        {
            "enabled": _Check(),
            "key": _Text("1"),
            "threshold": _Spin(),
            "coord_input": _Text("1,2,3,4"),
            "tolerance_input": _Text("10,30,50"),
        },
        "rectangle",
        {},
        existing_config={"colors": colors},
    )

    assert "colors" not in result


def test_resource_template_capture_uses_absolute_coordinate_conversion():
    from torchlight_assistant.core.resource_manager import ResourceManager

    calls = []
    caches = {}

    class _Border:
        def get_region_from_frame(self, frame, x, y, width, height):
            calls.append((x, y, width, height))
            return frame[0:height, 0:width]

        def set_template_cache(self, name, value):
            caches[name] = value

    manager = object.__new__(ResourceManager)
    manager.border_frame_manager = _Border()
    manager.hp_config = {
        "enabled": True,
        "detection_mode": "rectangle",
        "region_x1": -1600,
        "region_y1": -200,
        "region_x2": -1590,
        "region_y2": -190,
    }
    manager.mp_config = {}

    manager.capture_template_hsv(np.zeros((10, 10, 4), dtype=np.uint8))

    assert calls == [(-1600, -200, 10, 10)]
    assert "hp_region" in caches


def _skill_config(key: str, **extra):
    config = {
        "Enabled": True,
        "Key": key,
        "Priority": False,
        "Timer": 100,
        "TriggerMode": 0,
        "BossOnly": False,
        "CooldownCoordX": 0,
        "CooldownCoordY": 0,
        "CooldownSize": 12,
        "ExecuteCondition": 0,
        "ConditionCoordX": 0,
        "ConditionCoordY": 0,
        "ConditionColor": 0,
        "AltKey": "",
    }
    config.update(extra)
    return config


def test_simplified_skill_preserves_unrendered_and_nested_fields():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.skill_config_widget import SimplifiedSkillWidget

    original = _skill_config(
        "q",
        ColorTolerance=37,
        FutureNested={"mode": "custom", "values": [1, 2, 3]},
    )
    widget = SimplifiedSkillWidget(None, "skill", original, None)
    try:
        widget._ui_widgets["Key"].setText("w")
        saved = widget.get_current_config()
        assert saved["Key"] == "w"
        assert saved["ColorTolerance"] == 37
        assert saved["FutureNested"] == original["FutureNested"]

        saved["FutureNested"]["values"].append(4)
        assert original["FutureNested"]["values"] == [1, 2, 3]
    finally:
        widget.close()


def test_skill_config_preserves_skills_beyond_visible_limit():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.feature_widgets import SkillConfigWidget

    original = {
        f"skill_{index}": _skill_config(
            str(index), FutureField={"index": index}
        )
        for index in range(10)
    }
    widget = SkillConfigWidget()
    try:
        widget.update_from_config(original)
        assert len(widget.skill_widgets) == 8
        saved = widget.get_config()
        assert list(saved) == list(original)
        assert saved["skill_8"] == original["skill_8"]
        assert saved["skill_9"] == original["skill_9"]
    finally:
        widget.close()


def test_affix_reroll_roundtrip_keeps_delay_and_unknown_fields():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.feature_widgets import AffixRerollWidget

    original = {
        "affix_reroll": {
            "enabled": True,
            "target_affixes": ["生命"],
            "max_attempts": 31,
            "click_delay": 987,
            "future_ocr_option": {"backend": "custom"},
        }
    }
    widget = AffixRerollWidget()
    try:
        widget.update_from_config(original)
        assert widget.click_delay_spinbox.value() == 987
        saved = widget.get_config()["affix_reroll"]
        assert saved["click_delay"] == 987
        assert saved["future_ocr_option"] == {"backend": "custom"}
    finally:
        widget.close()


def test_affix_config_rejects_non_string_targets_and_incomplete_coordinates():
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
    )

    parsed = SimpleAffixRerollConfig.from_dict(
        {
            "enabled": True,
            "target_affixes": [0, False, None, {"x": 1}, " 生命 ", ""],
            "enchant_button_coord": [10, 20],
            "first_affix_button_coord": [11, 21],
            "replace_button_coord": [12, 22],
            "close_button_coord": [13, 23],
        }
    )
    assert parsed.target_affixes == ["生命"]

    manager = object.__new__(SimpleAffixRerollManager)
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["生命"],
        enchant_button_coord=(10, 20),
    )
    manager.status = SimpleAffixRerollStatus()
    manager._run_lock = threading.RLock()
    manager._active_run = None

    assert manager.start_reroll() is False


def test_affix_widget_does_not_turn_non_string_targets_into_ocr_matches():
    app = _qt_app()
    if app is None:
        return
    from torchlight_assistant.gui.feature_widgets import AffixRerollWidget

    widget = AffixRerollWidget()
    try:
        widget.update_from_config({"affix_reroll": {
            "target_affixes": [0, False, None, {"x": 1}, " 生命 "]
        }})
        assert widget.get_config()["affix_reroll"]["target_affixes"] == ["生命"]
    finally:
        widget.close()


def test_malformed_macro_steps_do_not_abort_ui_refresh():
    app = _qt_app()
    if app is None:
        return
    from torchlight_assistant.gui.feature_widgets import SkillConfigWidget

    widget = SkillConfigWidget()
    try:
        widget.update_from_config({}, {"macro_steps": 123})
        assert widget.get_macro_steps() == []
    finally:
        widget.close()


@pytest.mark.parametrize("threshold", [True, "invalid", -1, 101])
def test_invalid_resource_threshold_does_not_become_executable_default(threshold):
    app = _qt_app()
    if app is None:
        return
    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    widget = ResourceManagementWidget()
    try:
        widget.update_from_config({"resource_management": {"hp_config": {
            "enabled": True,
            "threshold": threshold,
            "region_x1": 0, "region_y1": 0, "region_x2": 10, "region_y2": 10,
        }}})
        assert widget.get_config()["resource_management"]["hp_config"]["threshold"] == 0
    finally:
        widget.close()


def test_pathfinding_roundtrip_accepts_negative_virtual_desktop_origin():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import PathfindingWidget

    widget = PathfindingWidget()
    try:
        assert widget.get_config()["pathfinding_config"]["minimap_area"] == [0, 0, 0, 0]
        widget.update_from_config(
            {
                "pathfinding_config": {
                    "hotkey": "legacy-unused",
                    "minimap_area": [-1600, -200, 420, 360],
                    "future_option": 17,
                }
            }
        )
        saved = widget.get_config()["pathfinding_config"]
        assert saved["minimap_area"] == [-1600, -200, 420, 360]
        assert saved["future_option"] == 17
        assert "hotkey" not in saved
    finally:
        widget.close()


def test_pathfinding_missing_or_malformed_section_resets_previous_coordinates():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import PathfindingWidget

    widget = PathfindingWidget()
    try:
        widget.update_from_config(
            {"pathfinding_config": {"minimap_area": [-100, -50, 300, 200]}}
        )
        widget.update_from_config({"pathfinding_config": None})
        assert widget.get_config()["pathfinding_config"]["minimap_area"] == [0, 0, 0, 0]

        widget.update_from_config(
            {"pathfinding_config": {"minimap_area": [-100, -50, 300, 200]}}
        )
        widget.update_from_config(
            {"pathfinding_config": {"minimap_area": [123]}}
        )
        assert widget.get_config()["pathfinding_config"]["minimap_area"] == [0, 0, 0, 0]
    finally:
        widget.close()


def test_nullable_nested_sections_reset_widgets_without_losing_future_fields():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import (
        StationaryModeWidget,
        WindowActivationWidget,
    )
    from torchlight_assistant.gui.feature_widgets import AffixRerollWidget
    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    window = WindowActivationWidget()
    stationary = StationaryModeWidget()
    affix = AffixRerollWidget()
    resources = ResourceManagementWidget()
    try:
        window.update_from_config({"window_activation": None})
        stationary.update_from_config({"stationary_mode_config": None})
        affix.update_from_config({"affix_reroll": None})
        resources.update_from_config({"resource_management": None})

        assert not window.get_config()["window_activation"]["enabled"]
        assert stationary.get_config()["stationary_mode_config"]["hotkey"] == ""
        assert not affix.get_config()["affix_reroll"]["enabled"]
        saved_resources = resources.get_config()["resource_management"]
        assert not saved_resources["hp_config"]["enabled"]
        assert not saved_resources["mp_config"]["enabled"]

        window.update_from_config(
            {"window_activation": {"future": {"exact_hwnd": True}}}
        )
        resources.update_from_config(
            {"resource_management": {"future": {"sampling": "adaptive"}}}
        )
        assert window.get_config()["window_activation"]["future"] == {
            "exact_hwnd": True
        }
        assert resources.get_config()["resource_management"]["future"] == {
            "sampling": "adaptive"
        }
    finally:
        window.close()
        stationary.close()
        affix.close()
        resources.close()


def test_resource_manager_bad_nested_config_and_threshold_fail_closed():
    from torchlight_assistant.core.resource_manager import ResourceManager

    manager = object.__new__(ResourceManager)
    manager.hp_config = {}
    manager.mp_config = {}
    manager.check_interval = 200
    manager._ocr_number_box = {}
    manager._tesseract_config_signature = None
    manager.tesseract_ocr_manager = None
    manager._flask_cooldowns = {}
    manager.border_frame_manager = SimpleNamespace(
        get_current_frame=lambda: np.zeros((2, 2, 4), dtype=np.uint8)
    )
    manager.debug_display_manager = None

    manager.update_config(
        {"hp_config": "invalid", "mp_config": None, "check_interval": "bad"}
    )
    assert manager.hp_config == {}
    assert manager.mp_config == {}
    assert manager.check_interval == 200

    manager.hp_config = {
        "enabled": True,
        "threshold": "invalid",
        "cooldown": 0,
    }
    assert manager._is_resource_low("hp", np.zeros((2, 2, 4), dtype=np.uint8)) is False

    manager.hp_config = {
        "enabled": True,
        "threshold": 50,
        "cooldown": 0,
        "detection_mode": "future-mode",
    }
    assert manager._is_resource_low("hp", np.zeros((2, 2, 4), dtype=np.uint8)) is False


def test_resource_widget_rejects_bad_subsection_types_and_preserves_unknown_mode():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    widget = ResourceManagementWidget()
    try:
        widget.update_from_config(
            {
                "resource_management": {
                    "hp_config": ["invalid"],
                    "mp_config": None,
                }
            }
        )
        assert not widget.hp_widgets["enabled"].isChecked()
        assert not widget.mp_widgets["enabled"].isChecked()

        future = {
            "enabled": True,
            "detection_mode": "future-detector",
            "future_options": {"levels": [1, 2]},
        }
        widget.update_from_config(
            {"resource_management": {"hp_config": future}}
        )
        assert widget.hp_widgets["mode_combo"].currentIndex() == -1
        assert "不受支持" in widget.hp_mode_label.text()
        saved = widget.get_config()["resource_management"]["hp_config"]
        assert saved["detection_mode"] == "future-detector"
        assert saved["future_options"] == {"levels": [1, 2]}

        saved["future_options"]["levels"].append(3)
        assert future["future_options"]["levels"] == [1, 2]
    finally:
        widget.close()


def test_pathfinding_roundtrip_deep_copies_future_fields():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.config_widgets import PathfindingWidget

    original = {
        "pathfinding_config": {
            "minimap_area": [-10, -20, 100, 80],
            "future": {"weights": [1, 2]},
        }
    }
    widget = PathfindingWidget()
    try:
        widget.update_from_config(original)
        saved = widget.get_config()["pathfinding_config"]
        saved["future"]["weights"].append(3)
        assert original["pathfinding_config"]["future"]["weights"] == [1, 2]
    finally:
        widget.close()


def test_empty_skill_config_resets_visibility_and_values():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.skill_config_widget import SimplifiedSkillWidget

    widget = SimplifiedSkillWidget(None, "skill", _skill_config("q", TriggerMode=1), None)
    try:
        assert widget.cooldown_frame.isVisibleTo(widget)
        widget.refresh({})
        assert not widget._ui_widgets["Enabled"].isChecked()
        assert widget._ui_widgets["Key"].text() == ""
        assert widget.timer_frame.isVisibleTo(widget)
        assert not widget.cooldown_frame.isVisibleTo(widget)
    finally:
        widget.close()


def test_unknown_input_and_skill_modes_remain_fail_closed_after_ui_roundtrip():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.basic_widgets import TopControlsWidget
    from torchlight_assistant.gui.skill_config_widget import SimplifiedSkillWidget

    controls = TopControlsWidget()
    skill = SimplifiedSkillWidget(
        None,
        "skill",
        _skill_config(
            "q",
            TriggerMode="future-trigger",
            ExecuteCondition=99,
        ),
        None,
    )
    try:
        controls.update_from_config({"input_mode": "future-input"})
        assert controls.input_mode_combo.currentIndex() == -1
        assert controls.get_config()["input_mode"] == "future-input"

        assert skill._ui_widgets["TriggerModeCombo"].currentIndex() == -1
        assert skill._ui_widgets["ExecuteCondition"].currentIndex() == -1
        saved = skill.get_current_config()
        assert saved["TriggerMode"] == "future-trigger"
        assert saved["ExecuteCondition"] == 99
        assert not skill.timer_frame.isVisibleTo(skill)
        assert not skill.cooldown_frame.isVisibleTo(skill)
        assert not skill.condition_frame.isVisibleTo(skill)

        controls.update_from_config(
            {"debug_mode": {"enabled": True, "future": {"trace": [1]}}}
        )
        debug_saved = controls.get_config()["debug_mode"]
        assert debug_saved["future"] == {"trace": [1]}
    finally:
        controls.close()
        skill.close()


def test_priority_keys_roundtrip_preserves_outer_and_entry_extensions():
    app = _qt_app()
    if app is None:
        return

    from torchlight_assistant.gui.priority_keys_widget import PriorityKeysWidget

    widget = PriorityKeysWidget()
    try:
        widget.set_config(
            {
                "enabled": True,
                "special_keys": ["space"],
                "managed_keys": {
                    "e": {
                        "target": "e",
                        "delay": 25,
                        "future": {"policy": "strict"},
                    }
                },
                "future_outer": {"version": 2},
            }
        )
        saved = widget.get_config()
        assert saved["future_outer"] == {"version": 2}
        assert saved["managed_keys"]["e"]["future"] == {
            "policy": "strict"
        }
    finally:
        widget.close()


def test_string_false_does_not_enable_runtime_producers():
    from torchlight_assistant.core.resource_manager import ResourceManager
    from torchlight_assistant.core.skill_manager import SkillManager
    from torchlight_assistant.utils.border_frame_manager import BorderFrameManager

    assert SkillManager._timed_skill_intervals(
        {
            "bad": {
                "Enabled": "false",
                "TriggerMode": 0,
                "Timer": 10,
            }
        }
    ) == {}

    skill_manager = object.__new__(SkillManager)
    skill_manager._global_config = {"sequence_enabled": "false"}
    assert skill_manager._is_macro_mode() is False

    resource_manager = object.__new__(ResourceManager)
    resource_manager._is_running = True
    resource_manager._is_paused = False
    resource_manager.hp_config = {"enabled": "false"}
    resource_manager.mp_config = {}
    resource_manager._is_resource_low = lambda *_args: (_ for _ in ()).throw(
        AssertionError("字符串 false 不得启动资源检测")
    )
    assert resource_manager.check_and_execute_resources(None) is False

    border = object.__new__(BorderFrameManager)
    border.skill_coords = []
    border.set_skill_coordinates(
        {
            "bad": {
                "Enabled": "false",
                "TriggerMode": 1,
                "CooldownCoordX": 1,
                "CooldownCoordY": 2,
            }
        },
        {},
    )
    assert border.skill_coords == []


def test_non_finite_detection_thresholds_fail_closed():
    from torchlight_assistant.core.resource_manager import ResourceManager
    from torchlight_assistant.utils.border_frame_manager import BorderFrameManager

    with pytest.raises(ValueError):
        ResourceManager._match_threshold({"match_threshold": float("nan")})
    with pytest.raises(ValueError):
        ResourceManager._match_threshold({"match_threshold": 1.1})

    with pytest.raises(ValueError):
        BorderFrameManager._finite_config_number(
            float("nan"), "s_min", 0.0, 100.0
        )
    assert BorderFrameManager._should_detect_hp_barrier(
        object.__new__(BorderFrameManager), {"detect_barrier": "false"}
    ) is True
    assert BorderFrameManager._should_detect_hp_barrier(
        object.__new__(BorderFrameManager), {"detect_barrier": False}
    ) is False


def test_json_booleans_are_not_numeric_configuration_values():
    from torchlight_assistant.core.resource_manager import ResourceManager
    from torchlight_assistant.core.skill_manager import SkillManager
    from torchlight_assistant.utils.border_frame_manager import BorderFrameManager

    assert SkillManager._milliseconds_to_seconds(True) is None
    with pytest.raises(ValueError):
        ResourceManager._match_threshold({"match_threshold": True})
    with pytest.raises(ValueError):
        BorderFrameManager._finite_config_number(True, "s_min", 0.0, 100.0)


@pytest.mark.parametrize("origin", [(0, 0), (1920, 120), (-1920, -200)])
@pytest.mark.parametrize("ocr_succeeds", [True, False])
def test_ocr_selection_uses_monitor_local_crop_and_saves_desktop_box(
    monkeypatch, origin, ocr_succeeds
):
    app = _qt_app()
    if app is None:
        pytest.skip("PySide6 不可用")

    from PySide6.QtCore import QObject, QTimer, Signal
    from torchlight_assistant.gui import region_selection_dialog
    from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget

    screenshot = np.zeros((120, 240, 3), dtype=np.uint8)
    rough_box = (20, 30, 140, 90)
    number_box = (30, 40, 120, 80)

    def desktop_box(box):
        x1, y1, x2, y2 = box
        return (x1 + origin[0], y1 + origin[1], x2 + origin[0], y2 + origin[1])

    class FakeDialog(QObject):
        region_selected = Signal(int, int, int, int)

        def __init__(self, analyze_colors):
            super().__init__()
            assert analyze_colors is False
            self._desktop_origin = origin

        def get_screenshot_array(self):
            return screenshot

        def exec(self):
            self.region_selected.emit(*desktop_box(rough_box))

    calls = []

    def locate_number_box(frame, box, **kwargs):
        calls.append((frame, box))
        if ocr_succeeds:
            return {
                "box": number_box, "current": 50, "maximum": 100,
                "percentage": 50.0, "score": 0.99, "text": "50/100",
            }
        return None

    monkeypatch.setattr(region_selection_dialog, "RegionSelectionDialog", FakeDialog)
    monkeypatch.setitem(
        sys.modules,
        "torchlight_assistant.utils.paddle_ocr_manager",
        SimpleNamespace(get_paddle_ocr_manager=lambda: SimpleNamespace(
            locate_number_box=locate_number_box
        )),
    )
    widget = ResourceManagementWidget()
    try:
        widget.main_window = SimpleNamespace(
            _global_config={}, hide=lambda: None, show=lambda: None,
            raise_=lambda: None, activateWindow=lambda: None,
        )
        mode_combo = widget.hp_widgets["mode_combo"]
        mode_combo.setCurrentIndex(mode_combo.findData("text_ocr"))
        ocr_combo = widget.hp_widgets["ocr_engine_combo"]
        ocr_combo.setCurrentIndex(ocr_combo.findData("paddle"))
        pending = []
        monkeypatch.setattr(QTimer, "singleShot", lambda _ms, callback: pending.append(callback))

        widget._start_region_selection_for_coords("hp")
        while pending:
            pending.pop(0)()

        assert len(calls) == 1
        assert calls[0][0] is screenshot
        assert calls[0][1] == rough_box
        expected = desktop_box(number_box if ocr_succeeds else rough_box)
        assert widget.hp_widgets["coord_input"].text() == ",".join(map(str, expected))
    finally:
        widget.close()
