"""Cross-component transaction and lifecycle regression tests."""

import threading
import sys
from types import SimpleNamespace
from unittest import mock

import numpy as np

from torchlight_assistant.core.ahk_input_handler import AHKInputHandler
from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.resource_manager import ResourceManager
from torchlight_assistant.core.simple_affix_reroll_manager import (
    SimpleAffixRerollConfig,
    SimpleAffixRerollManager,
    SimpleAffixRerollStatus,
    _AffixRerollRun,
)
from torchlight_assistant.core.skill_manager import SkillManager
from torchlight_assistant.core.states import MacroState
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager


class _Input:
    def __init__(self):
        self.calls = []
        self.hold_result = True

    def set_skill_hold_keys(self, keys):
        self.calls.append(("holds", list(keys)))
        return self.hold_result

    def set_macro_steps(self, steps):
        self.calls.append(("steps", list(steps)))
        return True

    def start_macro(self):
        self.calls.append(("macro_start",))
        return True

    def stop_macro(self):
        self.calls.append(("macro_stop",))
        return True

    def set_accepting_actions(self, enabled):
        self.calls.append(("gate", bool(enabled)))
        return True


class _Scheduler:
    def __init__(self, calls):
        self.running = True
        self.calls = calls

    def get_status(self):
        return {"running": self.running}

    def stop(self):
        self.calls.append(("scheduler_stop",))
        self.running = False
        return True

    def clear_all_tasks(self):
        self.calls.append(("tasks_clear",))

    def add_task(self, *args, **kwargs):
        self.calls.append(("task_add", args[0]))
        return True

    def remove_task(self, task_id):
        self.calls.append(("task_remove", task_id))
        return True

    def update_task_interval(self, task_id, interval):
        self.calls.append(("task_interval", task_id, interval))
        return True


def _skill_manager(sequence_enabled=False):
    input_handler = _Input()
    manager = SkillManager(
        input_handler,
        None,
        SimpleNamespace(prepare_border=lambda *args: None, stop=lambda: None),
        resource_manager=None,
    )
    manager.unified_scheduler = _Scheduler(input_handler.calls)
    manager._global_config = {
        "sequence_enabled": sequence_enabled,
        "macro_steps": [],
    }
    manager._is_running = True
    return manager


def test_combined_skill_to_macro_update_never_pulses_new_hold_key():
    manager = _skill_manager(sequence_enabled=False)
    manager._skills_config = {
        "old": {"Enabled": True, "TriggerMode": 2, "Key": "A"}
    }
    new_skills = {
        "new": {"Enabled": True, "TriggerMode": 2, "Key": "B"}
    }
    new_global = {
        "sequence_enabled": True,
        "macro_steps": [{"type": "press", "key": "1"}],
    }

    assert manager._apply_config_update(new_skills, new_global) is True

    hold_calls = [call for call in manager.input_handler.calls if call[0] == "holds"]
    assert hold_calls == [("holds", [])]
    assert manager.input_handler.calls.index(("holds", [])) < manager.input_handler.calls.index(
        ("macro_start",)
    )


def test_config_sync_failure_closes_gate_before_blocking_scheduler_stop():
    manager = _skill_manager(sequence_enabled=False)
    manager._skills_config = {
        "old": {"Enabled": True, "TriggerMode": 2, "Key": "A"}
    }
    manager.input_handler.hold_result = False

    with mock.patch(
        "torchlight_assistant.core.skill_manager.event_bus.publish"
    ) as publish:
        assert manager._apply_config_update(
            {"new": {"Enabled": True, "TriggerMode": 2, "Key": "B"}},
            {"sequence_enabled": False, "macro_steps": []},
        ) is False

    calls = manager.input_handler.calls
    assert calls.index(("gate", False)) < calls.index(("scheduler_stop",))
    assert manager._is_running is False
    assert manager._config_update_in_progress is True
    publish.assert_called_once_with(
        "skill_manager:input_sync_failed", reason="技能持键热更新失败"
    )


def test_disposed_ahk_handler_drops_already_queued_signal_delivery():
    published = []
    handler = object.__new__(AHKInputHandler)
    handler._disposed = True
    handler.event_bus = SimpleNamespace(
        publish=lambda name, **data: published.append((name, data))
    )

    handler._on_ahk_event("intercept_key_down:F8")

    assert published == []


def test_border_manager_cleanup_unsubscribes_once():
    manager = object.__new__(BorderFrameManager)
    manager._cleanup_done = False
    manager.stop = mock.Mock()

    with mock.patch(
        "torchlight_assistant.core.event_bus.event_bus.unsubscribe"
    ) as unsubscribe:
        manager.cleanup()
        manager.cleanup()

    manager.stop.assert_called_once_with()
    unsubscribe.assert_called_once_with(
        "engine:config_updated", manager._on_config_updated
    )


def test_text_ocr_rejects_region_outside_current_frame_before_ocr():
    manager = object.__new__(ResourceManager)
    manager.hp_config = {
        "enabled": True,
        "detection_mode": "text_ocr",
        "ocr_engine": "tesseract",
        "text_x1": 0,
        "text_y1": 0,
        "text_x2": 11,
        "text_y2": 10,
        "threshold": 50,
    }
    manager.mp_config = {}
    manager._flask_cooldowns = {}
    manager.debug_display_manager = None
    manager.border_frame_manager = SimpleNamespace()
    manager.tesseract_ocr_manager = mock.Mock()

    assert manager._is_resource_low("hp", np.zeros((10, 10, 4), dtype=np.uint8)) is False
    manager.tesseract_ocr_manager.recognize_and_parse.assert_not_called()


def test_paddle_ocr_lock_uses_same_zero_origin_and_frame_bounds_rules():
    manager = object.__new__(ResourceManager)
    manager.hp_config = {
        "enabled": True,
        "detection_mode": "text_ocr",
        "ocr_engine": "paddle",
        "text_x1": 0,
        "text_y1": 0,
        "text_x2": 4,
        "text_y2": 5,
    }
    manager.mp_config = {}
    manager._ocr_number_box = {}
    manager.paddle_ocr_manager = SimpleNamespace(
        recognize_and_parse=lambda *args: (1, 2, 50.0)
    )

    manager.lock_ocr_number_position(np.zeros((5, 4, 4), dtype=np.uint8))
    assert manager._ocr_number_box["hp"] == (0, 0, 4, 5)

    manager.hp_config["text_x2"] = 5
    manager.lock_ocr_number_position(np.zeros((5, 4, 4), dtype=np.uint8))
    assert "hp" not in manager._ocr_number_box


def test_resource_config_change_invalidates_stale_paddle_lock():
    manager = object.__new__(ResourceManager)
    manager.hp_config = {
        "enabled": True,
        "detection_mode": "text_ocr",
        "ocr_engine": "paddle",
        "text_x1": 0,
        "text_y1": 0,
        "text_x2": 4,
        "text_y2": 5,
    }
    manager.mp_config = {}
    manager._ocr_number_box = {"hp": (0, 0, 4, 5)}

    changed = dict(manager.hp_config, text_x2=6)
    manager.update_config({"hp_config": changed, "mp_config": {}})

    assert "hp" not in manager._ocr_number_box


def test_missing_deepai_does_not_disable_independent_tesseract_engine():
    manager = object.__new__(ResourceManager)
    tesseract = object()
    manager.tesseract_ocr_manager = tesseract

    with mock.patch.dict(sys.modules, {"deepai": None}):
        manager._check_deepai_availability()

    assert manager.deepai_available is False
    assert manager._deepai_get_recognizer is None
    assert manager.tesseract_ocr_manager is tesseract


def test_failed_potion_send_does_not_arm_internal_cooldown():
    manager = object.__new__(ResourceManager)
    manager.input_handler = SimpleNamespace(execute_hp_potion=lambda key: False)
    manager._flask_cooldowns = {}

    assert manager._execute_resource("hp", {"key": "1"}) is False
    assert "hp" not in manager._flask_cooldowns


def test_disabled_resources_explicitly_clear_ahk_emergency_key_cache():
    batches = []
    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(batch_update_config=batches.append)

    engine._update_ahk_emergency_keys_cache(
        {
            "resource_management": {
                "hp_config": {"enabled": False, "key": "1"},
                "mp_config": {"enabled": False, "key": "2"},
            }
        }
    )

    assert batches[-1]["hp_key"] == ""
    assert batches[-1]["mp_key"] == ""


def test_emergency_cache_protocol_preserves_explicit_empty_keys():
    sent = []
    sender = SimpleNamespace(
        _send=lambda command, param: sent.append((command, param)) or True
    )

    assert AHKCommandSender.batch_update_config(
        sender, {"hp_key": "", "mp_key": "", "unrelated": None}
    ) is True
    assert sent[0][1] == "hp_key:,mp_key:"


def test_stationary_sender_validates_and_commits_transactionally():
    sender = object.__new__(AHKCommandSender)
    sender._stationary_mode_active = False
    sender._stationary_mode_type = ""
    sender._send = mock.Mock(return_value=False)

    assert sender.set_stationary_mode(True, "BLOCK_MOUSE") is False
    assert sender._stationary_mode_active is False
    assert sender._stationary_mode_type == ""
    sender._send.assert_called_once()

    sender._send.reset_mock()
    assert sender.set_stationary_mode(True, "future_mode") is False
    sender._send.assert_not_called()

    sender._send.return_value = True
    assert sender.set_stationary_mode(True, "BLOCK_MOUSE") is True
    assert sender._stationary_mode_active is True
    assert sender._stationary_mode_type == "block_mouse"

    assert sender.set_stationary_mode(False, "future_mode") is True
    assert sender._stationary_mode_active is False
    assert sender._stationary_mode_type == ""


def test_stationary_batch_field_supports_explicit_clear():
    sent = []
    sender = SimpleNamespace(
        _send=lambda command, param: sent.append((command, param)) or True
    )

    assert AHKCommandSender.batch_update_config(
        sender, {"stationary_type": ""}
    ) is True
    assert sent[0][1] == "stationary_type:"


def test_force_move_passthrough_rejects_scalar_string():
    sender = object.__new__(AHKCommandSender)
    sender._send = mock.Mock(return_value=True)

    assert sender.set_force_move_passthrough_keys("RButton") is False
    sender._send.assert_not_called()

    assert sender.set_force_move_passthrough_keys(["RButton", " space "]) is True
    sender._send.assert_called_once()
    assert sender._send.call_args.args[1] == "RButton,space"


def test_malformed_force_move_passthrough_is_cleared_not_split_into_characters():
    batches = []
    sent_passthrough = []
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._is_debug_mode_active = False
    engine._stationary_mode_active = False
    engine.input_handler = SimpleNamespace(
        dry_run_mode=False,
        set_dry_run_mode=lambda *_args: True,
        set_target_window=lambda *_args: True,
        set_force_move_key=lambda *_args: True,
        set_force_move_replacement_key=lambda *_args: True,
        set_force_move_passthrough_keys=lambda keys: sent_passthrough.append(keys) or True,
        batch_update_config=lambda config: batches.append(config) or True,
    )
    engine.resource_manager = SimpleNamespace(update_config=lambda *_args: None)
    engine._update_osd_visibility = lambda: None

    engine._on_config_updated(
        {},
        {
            "stationary_mode_config": {
                "force_move_passthrough_keys": "RButton"
            }
        },
    )

    assert sent_passthrough == [[]]


class _SwapRunOnExit:
    """Deterministically exposes publish-after-unlock generation races."""

    def __init__(self, manager, replacement):
        self._lock = threading.RLock()
        self._manager = manager
        self._replacement = replacement

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._lock.release()
        self._manager._active_run = self._replacement


def test_affix_click_error_status_is_published_before_generation_unlock():
    manager = object.__new__(SimpleAffixRerollManager)
    old_run = _AffixRerollRun(
        generation=1,
        config=SimpleAffixRerollConfig(),
        status=SimpleAffixRerollStatus(is_running=True),
    )
    new_run = _AffixRerollRun(
        generation=2,
        config=SimpleAffixRerollConfig(),
        status=SimpleAffixRerollStatus(is_running=True),
    )
    manager._active_run = old_run
    manager.status = old_run.status
    manager.input_handler = SimpleNamespace(click_mouse_at=lambda *args: False)
    manager._run_lock = _SwapRunOnExit(manager, new_run)
    published_while_current = []
    manager._publish_status_update = lambda status: published_while_current.append(
        manager._active_run is old_run
    )

    assert manager._click_at(old_run, (10, 20), "测试") is False
    assert published_while_current == [True]
    assert manager._active_run is new_run
