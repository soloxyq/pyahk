#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""F8 UI 配置同步与配置保存提交边界回归测试。"""

import os
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as macro_engine_module
import torchlight_assistant.gui.main_window as main_window_module
from torchlight_assistant.core.config_manager import ConfigManager
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.states import MacroState
from torchlight_assistant.gui.basic_widgets import TimingSettingsWidget
from torchlight_assistant.gui.main_window import GameSkillConfigUI


def _new_engine(config_manager):
    engine = MacroEngine.__new__(MacroEngine)
    engine.config_manager = config_manager
    engine.sound_manager = SimpleNamespace(update_config=lambda config: None)
    engine._skills_config = {"OldSkill": {"Enabled": True}}
    engine._global_config = {"old": True}
    engine._remember_config_file = lambda path: None
    return engine


def _valid_config():
    return {
        "skills": {"Skill1": {"Enabled": True, "Key": "q"}},
        "global": {
            "sequence_enabled": False,
            "macro_steps": [],
            "skill_sequence": "",
        },
    }


def test_non_object_app_state_is_ignored(tmp_path):
    engine = MacroEngine.__new__(MacroEngine)
    state_file = tmp_path / ".pyahk_state.json"
    engine.APP_STATE_FILE = state_file

    for value in ([], None, "default.json"):
        state_file.write_text(json.dumps(value), encoding="utf-8")
        assert engine._load_last_config_file() == ""


def test_physical_f8_requests_ui_config_sync():
    """物理 F8 不能直接切状态,必须先请求 MainWindow 采集当前控件值。"""
    engine = MacroEngine.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    published = []

    with patch.object(
        macro_engine_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        engine._handle_ahk_intercept_key("F8")

    assert published == [("hotkey:f8_system_toggle", (), {})]


def test_physical_f8_stop_does_not_depend_on_ui():
    """运行中的 F8 是安全停机路径,GUI 失效也不能阻断。"""
    engine = MacroEngine.__new__(MacroEngine)
    engine._state = MacroState.RUNNING
    calls = []
    engine._handle_f8_press = lambda config=None: calls.append(config)

    with patch.object(macro_engine_module.event_bus, "publish") as publish:
        engine._handle_ahk_intercept_key("F8")

    publish.assert_not_called()
    assert calls == [None]


def test_physical_f7_f9_start_request_current_ui_snapshot():
    engine = MacroEngine.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine.affix_reroll_manager = SimpleNamespace(
        status=SimpleNamespace(is_running=False)
    )
    engine._on_f7_key_press = lambda *_args: (_ for _ in ()).throw(
        AssertionError("F7 启动不得绕过 GUI")
    )
    engine._on_f9_key_press = lambda *_args: (_ for _ in ()).throw(
        AssertionError("F9 启动不得绕过 GUI")
    )
    published = []

    with patch.object(
        macro_engine_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(name),
    ):
        engine._handle_ahk_intercept_key("F7")
        engine._handle_ahk_intercept_key("F9")

    assert published == ["hotkey:f7_system_toggle", "hotkey:f9_system_toggle"]


def test_physical_f7_stop_does_not_gather_ui_config():
    engine = MacroEngine.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine.affix_reroll_manager = SimpleNamespace(
        status=SimpleNamespace(is_running=True)
    )
    engine._on_f7_key_press = Mock()
    with patch.object(macro_engine_module.event_bus, "publish") as publish:
        engine._handle_ahk_intercept_key("F7")
    publish.assert_not_called()
    engine._on_f7_key_press.assert_called_once_with()


def test_ui_f8_bridge_publishes_current_widget_config():
    full_config = _valid_config()
    ui = SimpleNamespace(
        macro_engine=SimpleNamespace(get_current_state=lambda: MacroState.STOPPED),
        _gather_current_config_from_ui=lambda: full_config,
    )
    published = []

    with patch.object(
        main_window_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        GameSkillConfigUI._toggle_visibility_and_macro(ui)

    assert published == [
        ("ui:sync_and_toggle_state_requested", (full_config,), {})
    ]


def test_ui_f7_f9_bridges_publish_current_widget_config():
    full_config = _valid_config()
    ui = SimpleNamespace(_gather_current_config_from_ui=lambda: full_config)
    published = []

    with patch.object(
        main_window_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        GameSkillConfigUI._publish_synced_mode_request(
            ui, "ui:sync_and_toggle_affix_requested", "F7"
        )
        GameSkillConfigUI._publish_synced_mode_request(
            ui, "ui:sync_and_toggle_pathfinding_requested", "F9"
        )

    assert published == [
        ("ui:sync_and_toggle_affix_requested", (full_config,), {}),
        ("ui:sync_and_toggle_pathfinding_requested", (full_config,), {}),
    ]


def test_ui_stop_does_not_gather_config():
    def fail_if_gathered():
        raise AssertionError("停止路径不应读取 GUI 配置")

    ui = SimpleNamespace(
        macro_engine=SimpleNamespace(get_current_state=lambda: MacroState.PAUSED),
        _gather_current_config_from_ui=fail_if_gathered,
    )
    published = []

    with patch.object(
        main_window_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        GameSkillConfigUI._toggle_visibility_and_macro(ui)

    assert published == [
        ("ui:sync_and_toggle_state_requested", (None,), {})
    ]


def test_ui_gather_preserves_unknown_global_fields_and_wires_resource_interval():
    timing = SimpleNamespace(
        get_config=lambda: {
            "key_press_duration": 10,
            "hp_cooldown": 1111,
            "mp_cooldown": 2222,
            "resource_check_interval": 333,
        }
    )
    ui = SimpleNamespace(
        _global_config={
            "future_extension": {"keep": True},
            "process_history": {"items": ["game.exe"]},
            "tesseract_ocr": {"future": {"models": ["eng"]}},
            "queue_processor_interval": 50,
            "mouse_click_duration": 5,
        },
        top_controls=None,
        timing_settings=timing,
        window_activation=None,
        stationary_mode=None,
        affix_reroll=None,
        pathfinding_settings=None,
        resource_management=SimpleNamespace(
            get_config=lambda: {
                "resource_management": {
                    "hp_config": {"cooldown": 1111},
                    "mp_config": {"cooldown": 2222},
                    "check_interval": 200,
                }
            }
        ),
        priority_keys_widget=None,
        skill_config=None,
    )

    result = GameSkillConfigUI._gather_current_config_from_ui(ui)

    assert result["global"]["future_extension"] == {"keep": True}
    assert result["global"]["resource_management"]["check_interval"] == 333
    assert result["global"]["resource_management"]["hp_config"]["cooldown"] == 1111
    assert result["global"]["resource_management"]["mp_config"]["cooldown"] == 2222
    assert "queue_processor_interval" not in result["global"]
    assert "mouse_click_duration" not in result["global"]
    assert "hp_cooldown" not in result["global"]
    assert "mp_cooldown" not in result["global"]
    assert "resource_check_interval" not in result["global"]
    result["global"]["process_history"]["items"].append("other.exe")
    result["global"]["tesseract_ocr"]["future"]["models"].append("chi_sim")
    assert ui._global_config["process_history"]["items"] == ["game.exe"]
    assert ui._global_config["tesseract_ocr"]["future"]["models"] == ["eng"]


def test_timing_widget_loads_canonical_nested_resource_timings():
    class Box:
        def __init__(self):
            self.value = None

        def setValue(self, value):
            self.value = value

    widget = SimpleNamespace(
        timing_spinboxes={
            name: Box()
            for name in (
                "key_press",
                "cooldown_checker",
                "capture_interval",
                "special_key_resume_delay",
                "hp_cooldown",
                "mp_cooldown",
                "resource_check_interval",
            )
        },
        sound_feedback_checkbox=None,
    )

    TimingSettingsWidget.update_from_config(
        widget,
        {
            "resource_management": {
                "check_interval": 333,
                "hp_config": {"cooldown": 1111},
                "mp_config": {"cooldown": 2222},
            }
        }
    )

    assert widget.timing_spinboxes["hp_cooldown"].value == 1111
    assert widget.timing_spinboxes["mp_cooldown"].value == 2222
    assert widget.timing_spinboxes["resource_check_interval"].value == 333


def test_timing_widget_prefers_canonical_nested_values_and_sanitizes_bad_input():
    from torchlight_assistant.gui.basic_widgets import TimingSettingsWidget

    class Box:
        def setValue(self, value):
            self.value = value

    checkbox = SimpleNamespace(setChecked=lambda value: setattr(checkbox, "value", value))
    widget = SimpleNamespace(
        timing_spinboxes={
            name: Box()
            for name in (
                "key_press",
                "cooldown_checker",
                "capture_interval",
                "special_key_resume_delay",
                "hp_cooldown",
                "mp_cooldown",
                "resource_check_interval",
            )
        },
        sound_feedback_checkbox=checkbox,
    )

    TimingSettingsWidget.update_from_config(
        widget,
        {
            "key_press_duration": float("nan"),
            "capture_interval": None,
            "sound_feedback_enabled": "false",
            "hp_cooldown": 9999,
            "resource_check_interval": 9999,
            "resource_management": {
                "check_interval": 321,
                "hp_config": {"cooldown": 1234},
            },
        },
    )

    assert widget.timing_spinboxes["key_press"].value == 10
    assert widget.timing_spinboxes["capture_interval"].value == 40
    assert widget.timing_spinboxes["hp_cooldown"].value == 1234
    assert widget.timing_spinboxes["resource_check_interval"].value == 321
    assert checkbox.value is False


def test_ui_f8_gather_failure_defers_dialog_and_dedupes():
    """采集失败:不发布事件;弹窗必须经 QTimer 延后 —— 同步模态框会在
    intercept_key_down 的 publish 链内开嵌套事件循环,期间物理 F8/F7/F9
    全部被 EventBus 同名递归保护静默吞掉。pending 标志防重复堆叠。"""

    def boom():
        raise RuntimeError("widget broken")

    ui = SimpleNamespace(
        macro_engine=SimpleNamespace(get_current_state=lambda: MacroState.STOPPED),
        _gather_current_config_from_ui=boom,
    )
    published = []
    deferred = []

    with patch.object(
        main_window_module.event_bus,
        "publish",
        side_effect=lambda name, *a, **k: published.append(name),
    ), patch.object(
        main_window_module.QMessageBox, "critical"
    ) as critical, patch.object(
        main_window_module.QTimer,
        "singleShot",
        side_effect=lambda ms, fn: deferred.append(fn),
    ):
        GameSkillConfigUI._toggle_visibility_and_macro(ui)
        # 弹窗尚未展示时再按一次 F8:不得排队第二个弹窗
        GameSkillConfigUI._toggle_visibility_and_macro(ui)

        assert published == []          # 未发布任何状态切换事件(未启动)
        critical.assert_not_called()    # publish 链内没有同步弹窗
        assert len(deferred) == 1       # 去重:只排队一个弹窗

        deferred[0]()                   # 模拟事件循环轮到延后回调
        critical.assert_called_once()
        assert ui._config_error_dialog_pending is False  # 弹完复位

        # 复位后再失败可以再次弹(不是永久熔断)
        GameSkillConfigUI._toggle_visibility_and_macro(ui)
        assert len(deferred) == 2


def test_save_commits_runtime_config_only_after_disk_success():
    order = []
    engine = None

    class RecordingConfigManager:
        def save_config(self, data, file_path):
            assert engine._global_config == {"old": True}
            order.append(("disk", file_path))

    engine = _new_engine(RecordingConfigManager())
    engine.sound_manager = SimpleNamespace(
        update_config=lambda config: order.append(("sound", config))
    )
    engine._remember_config_file = lambda path: order.append(("remember", path))
    published = []
    full_config = _valid_config()

    with patch.object(
        macro_engine_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: (
            order.append(("event", name)),
            published.append((name, args, kwargs)),
        ),
    ):
        assert engine.save_full_config("new.json", full_config) is True

    assert order[0] == ("disk", "new.json")
    assert engine._skills_config is full_config["skills"]
    assert engine._global_config is full_config["global"]
    assert ("remember", "new.json") in order
    assert published[0][0] == "engine:config_updated"
    assert published[-1] == (
        "engine:config_save_result",
        ("new.json", True, ""),
        {},
    )


def test_save_failure_keeps_runtime_config_and_reports_failure():
    class FailingConfigManager:
        def save_config(self, data, file_path):
            raise OSError("disk full")

    engine = _new_engine(FailingConfigManager())
    old_skills = engine._skills_config
    old_global = engine._global_config
    remembered = []
    sound_updates = []
    engine._remember_config_file = remembered.append
    engine.sound_manager = SimpleNamespace(update_config=sound_updates.append)
    published = []

    with patch.object(
        macro_engine_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        assert engine.save_full_config("broken.json", _valid_config()) is False

    assert engine._skills_config is old_skills
    assert engine._global_config is old_global
    assert remembered == []
    assert sound_updates == []
    assert published == [
        (
            "engine:config_save_result",
            ("broken.json", False, "disk full"),
            {},
        )
    ]


def test_save_rejects_invalid_sections_before_disk_write():
    writes = []
    engine = _new_engine(
        SimpleNamespace(save_config=lambda data, path: writes.append(path))
    )
    old_skills = engine._skills_config
    old_global = engine._global_config
    published = []

    with patch.object(
        macro_engine_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        assert engine.save_full_config(
            "invalid.json", {"skills": [], "global": {}}
        ) is False

    assert writes == []
    assert engine._skills_config is old_skills
    assert engine._global_config is old_global
    assert published[0][0] == "engine:config_save_result"
    assert published[0][1][1] is False


def test_ui_updates_current_file_only_after_save_success():
    labels = []
    ui = SimpleNamespace(
        top_controls=SimpleNamespace(set_current_config=labels.append)
    )

    GameSkillConfigUI._perform_config_save_result(ui, "configs/ok.json", True)
    assert labels == ["ok.json"]

    with patch.object(main_window_module.QMessageBox, "critical") as critical:
        GameSkillConfigUI._perform_config_save_result(
            ui, "configs/bad.json", False, "disk full"
        )

    assert labels == ["ok.json"]
    critical.assert_called_once()


def test_config_manager_rejects_broken_and_non_object_json():
    manager = ConfigManager()
    with tempfile.TemporaryDirectory(prefix="pyahk_load_") as tmpdir:
        malformed = Path(tmpdir) / "malformed.json"
        malformed.write_text("{broken", encoding="utf-8")
        try:
            manager.load_config(str(malformed))
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("损坏 JSON 不得静默返回空配置")

        non_object = Path(tmpdir) / "list.json"
        non_object.write_text("[]", encoding="utf-8")
        try:
            manager.load_config(str(non_object))
        except ValueError as e:
            assert "顶层必须是 JSON 对象" in str(e)
        else:
            raise AssertionError("顶层非对象 JSON 必须拒绝")


def test_config_manager_rejects_non_finite_numbers_on_load_and_save():
    manager = ConfigManager()
    with tempfile.TemporaryDirectory(prefix="pyahk_finite_") as tmpdir:
        root = Path(tmpdir)
        for index, text in enumerate(("{\"x\": NaN}", "{\"x\": 1e309}")):
            source = root / f"bad_{index}.json"
            source.write_text(text, encoding="utf-8")
            try:
                manager.load_config(str(source))
            except ValueError as exc:
                assert "非有限" in str(exc)
            else:
                raise AssertionError("非有限 JSON 数值必须拒绝")

        destination = root / "saved.json"
        destination.write_text('{"old": true}', encoding="utf-8")
        try:
            manager.save_config({"global": {"interval": float("inf")}}, destination)
        except ValueError:
            pass
        else:
            raise AssertionError("保存不得输出 Infinity")
        assert destination.read_text(encoding="utf-8") == '{"old": true}'
        assert not destination.with_name("saved.json.tmp").exists()


def test_load_commits_runtime_config_only_after_validation_success():
    order = []
    engine = None

    class RecordingConfigManager:
        def load_config(self, file_path):
            assert engine._global_config == {"old": True}
            order.append(("disk", file_path))
            return _valid_config()

    engine = _new_engine(RecordingConfigManager())
    engine.sound_manager = SimpleNamespace(
        update_config=lambda config: order.append(("sound", config))
    )
    engine._remember_config_file = lambda path: order.append(("remember", path))
    published = []

    with tempfile.TemporaryDirectory(prefix="pyahk_load_") as tmpdir:
        path = Path(tmpdir) / "valid.json"
        path.write_text("{}", encoding="utf-8")
        with patch.object(
            macro_engine_module.event_bus,
            "publish",
            side_effect=lambda name, *args, **kwargs: published.append(
                (name, args, kwargs)
            ),
        ):
            assert engine.load_config(str(path)) is True

    assert order[0][0] == "disk"
    assert engine._skills_config == _valid_config()["skills"]
    assert engine._global_config == _valid_config()["global"]
    assert any(item[0] == "remember" for item in order)
    assert published[0][0] == "engine:config_updated"
    assert published[-1][0] == "engine:config_load_result"
    assert published[-1][1][1:] == (True, "")


def test_load_failure_keeps_runtime_config_and_reports_failure():
    class FailingConfigManager:
        def load_config(self, file_path):
            raise ValueError("invalid top level")

    engine = _new_engine(FailingConfigManager())
    old_skills = engine._skills_config
    old_global = engine._global_config
    remembered = []
    sound_updates = []
    engine._remember_config_file = remembered.append
    engine.sound_manager = SimpleNamespace(update_config=sound_updates.append)
    published = []

    with tempfile.TemporaryDirectory(prefix="pyahk_load_") as tmpdir:
        path = Path(tmpdir) / "invalid.json"
        path.write_text("{}", encoding="utf-8")
        with patch.object(
            macro_engine_module.event_bus,
            "publish",
            side_effect=lambda name, *args, **kwargs: published.append(
                (name, args, kwargs)
            ),
        ):
            assert engine.load_config(str(path)) is False

    assert engine._skills_config is old_skills
    assert engine._global_config is old_global
    assert remembered == []
    assert sound_updates == []
    assert len(published) == 1
    assert published[0][0] == "engine:config_load_result"
    assert published[0][1][1:] == (False, "invalid top level")


def test_ui_load_label_changes_only_after_success_result():
    labels = []
    ui = SimpleNamespace(
        top_controls=SimpleNamespace(set_current_config=labels.append)
    )
    published = []

    with patch.object(
        main_window_module.QFileDialog,
        "getOpenFileName",
        return_value=("configs/candidate.json", "JSON Files (*.json)"),
    ), patch.object(
        main_window_module.event_bus,
        "publish",
        side_effect=lambda name, *args, **kwargs: published.append(
            (name, args, kwargs)
        ),
    ):
        GameSkillConfigUI._load_config_file(ui)

    assert labels == []
    assert published == [
        ("ui:load_config_requested", ("configs/candidate.json",), {})
    ]

    GameSkillConfigUI._perform_config_load_result(
        ui, "configs/loaded.json", True
    )
    assert labels == ["loaded.json"]

    with patch.object(main_window_module.QMessageBox, "critical") as critical:
        GameSkillConfigUI._perform_config_load_result(
            ui, "configs/bad.json", False, "invalid JSON"
        )

    assert labels == ["loaded.json"]
    critical.assert_called_once()


def test_startup_load_failure_replayed_once_via_request_current_config():
    """启动期加载失败的 config_load_result 发布时 GUI 尚未订阅而被丢弃;
    引擎必须暂存失败,在 ui:request_current_config 握手时一次性重放。"""

    class FailingConfigManager:
        def load_config(self, file_path):
            raise ValueError("boom at startup")

    engine = _new_engine(FailingConfigManager())
    engine._pending_load_error = None

    with tempfile.TemporaryDirectory(prefix="pyahk_load_") as tmpdir:
        path = Path(tmpdir) / "broken.json"
        path.write_text("{}", encoding="utf-8")
        # 模拟启动期:此次 publish 无人订阅(patch 掉,等价于丢弃)
        with patch.object(macro_engine_module.event_bus, "publish"):
            assert engine.load_config(str(path)) is False

        assert engine.get_pending_load_error() == (str(path), "boom at startup")

        # GUI 构造完成后握手:补发一次失败结果
        published = []
        with patch.object(
            macro_engine_module.event_bus,
            "publish",
            side_effect=lambda name, *args, **kwargs: published.append(
                (name, args, kwargs)
            ),
        ):
            engine._handle_ui_request_current_config()
            assert published[0][0] == "engine:config_updated"
            assert published[-1] == (
                "engine:config_load_result",
                (str(path), False, "boom at startup"),
                {},
            )
            assert engine.get_pending_load_error() is None

            # 一次性:重复请求不得二次弹窗
            count_before = len(published)
            engine._handle_ui_request_current_config()
            new_events = [p[0] for p in published[count_before:]]
            assert "engine:config_load_result" not in new_events


def test_pending_load_error_cleared_by_later_success():
    """之后一次成功的 load/save 必须清掉暂存失败,不得复活过期弹窗。"""

    class OkConfigManager:
        def load_config(self, file_path):
            return _valid_config()

        def save_config(self, data, file_path):
            pass

    engine = _new_engine(OkConfigManager())
    engine._pending_load_error = ("old_broken.json", "stale error")

    with tempfile.TemporaryDirectory(prefix="pyahk_load_") as tmpdir:
        path = Path(tmpdir) / "good.json"
        path.write_text("{}", encoding="utf-8")
        with patch.object(macro_engine_module.event_bus, "publish"):
            assert engine.load_config(str(path)) is True
    assert engine.get_pending_load_error() is None

    engine2 = _new_engine(OkConfigManager())
    engine2._pending_load_error = ("old_broken.json", "stale error")
    with patch.object(macro_engine_module.event_bus, "publish"):
        assert engine2.save_full_config("out.json", _valid_config()) is True
    assert engine2.get_pending_load_error() is None


def test_initial_label_marks_failed_load():
    """启动期加载失败时,文件名标签必须带"(加载失败)",不得谎报已加载。"""
    labels = []
    ui = SimpleNamespace(
        top_controls=SimpleNamespace(set_current_config=labels.append),
        macro_engine=SimpleNamespace(
            current_config_file="configs/broken.json",
            get_pending_load_error=lambda: ("configs/broken.json", "bad json"),
        ),
    )
    with patch.object(main_window_module.event_bus, "publish"):
        GameSkillConfigUI._load_initial_config_to_ui(ui)
    assert labels == ["broken.json (加载失败)"]

    labels.clear()
    ui.macro_engine.get_pending_load_error = lambda: None
    with patch.object(main_window_module.event_bus, "publish"):
        GameSkillConfigUI._load_initial_config_to_ui(ui)
    assert labels == ["broken.json"]


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
