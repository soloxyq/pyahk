"""Configuration-generation state must not leak into a different action."""

import threading
from types import SimpleNamespace
from unittest import mock

from torchlight_assistant.core.resource_manager import ResourceManager
from torchlight_assistant.core.skill_manager import SkillManager


def _bare_skill_manager(*, running: bool = False) -> SkillManager:
    manager = object.__new__(SkillManager)
    manager._config_lock = threading.Lock()
    manager._resource_condition_history = {}
    manager._required_consecutive_checks = 2
    manager._skills_config = {}
    manager._global_config = {
        "sequence_enabled": False,
        "macro_steps": [],
    }
    manager._config_update_in_progress = False
    manager._config_generation = 0
    manager._is_running = running
    manager._is_paused = False
    manager._boss_mode_active = False
    manager.resource_manager = None
    return manager


def test_skill_condition_history_does_not_cross_config_generation():
    manager = _bare_skill_manager()
    manager._skills_config = {
        "ResourceSkill": {"Enabled": True, "ExecuteCondition": 2}
    }
    manager._resource_condition_history = {"ResourceSkill": [True]}

    assert manager._apply_config_update(
        {"ResourceSkill": {"Enabled": True, "ExecuteCondition": 2}},
        {"sequence_enabled": False, "macro_steps": []},
    ) is True

    assert manager._resource_condition_history == {}
    # The first sufficient frame in the new generation is not allowed to reuse
    # the previous generation's first True sample.
    assert manager._check_resource_continuity("ResourceSkill", True) is False


def test_failed_skill_update_and_restart_discard_late_condition_history():
    manager = _bare_skill_manager(running=True)
    manager._skills_config = {
        "ResourceSkill": {"Enabled": True, "TriggerMode": 0}
    }
    manager._resource_condition_history = {"ResourceSkill": [True]}
    manager.input_handler = SimpleNamespace(
        set_accepting_actions=lambda enabled: True
    )
    manager._sync_skill_hold_keys = lambda: False
    manager._stop_autonomous_scheduling = lambda: None

    with mock.patch(
        "torchlight_assistant.core.skill_manager.event_bus.publish"
    ):
        assert manager._apply_config_update(
            {"ResourceSkill": {"Enabled": True, "TriggerMode": 0}},
            {"sequence_enabled": False, "macro_steps": []},
        ) is False

    assert manager._resource_condition_history == {}
    assert manager._config_update_in_progress is True

    # Model a detector that outlived the scheduler's bounded join and completed
    # after the failure cleanup. A complete restart is the final generation
    # boundary and must discard that late sample as well.
    manager._resource_condition_history["ResourceSkill"] = [True]
    manager._sync_skill_hold_keys = lambda: True
    manager.border_frame_manager = SimpleNamespace(
        prepare_border=lambda *args: None
    )
    manager._start_autonomous_scheduling = lambda: None
    manager.unified_scheduler = SimpleNamespace(
        get_status=lambda: {"running": True}
    )

    assert manager.start() is True
    assert manager._resource_condition_history == {}
    assert manager._config_update_in_progress is False
    assert manager._check_resource_continuity("ResourceSkill", True) is False


def test_blocked_old_condition_callback_cannot_cross_config_transaction():
    manager = _bare_skill_manager(running=True)
    old_skill = {
        "Enabled": True,
        "TriggerMode": 0,
        "ExecuteCondition": 2,
        "ConditionCoordX": 10,
        "ConditionCoordY": 20,
        "ConditionColor": 0,
        "ColorTolerance": 12,
        "Key": "OLD",
        "AltKey": "OLD_ALT",
    }
    new_skill = dict(old_skill, Key="NEW")
    manager._skills_config = {"ResourceSkill": old_skill}
    manager._config_generation = 0
    manager._frame_usage_stats = {
        "total_frame_gets": 1,
        "cached_frame_usage": 0,
        "performance_ratio": 0.0,
    }
    manager.debug_display_manager = None
    manager.input_handler = SimpleNamespace(
        execute_skill_normal=mock.Mock(return_value=True),
        execute_skill_high=mock.Mock(return_value=True),
    )
    manager._sync_skill_hold_keys = lambda: True
    manager.unified_scheduler = SimpleNamespace(
        get_status=lambda: {"running": False}
    )

    detector_entered = threading.Event()
    release_detector = threading.Event()

    def blocking_detection(*_args, **_kwargs):
        detector_entered.set()
        assert release_detector.wait(timeout=2)
        return True

    manager.border_frame_manager = SimpleNamespace(
        get_region_from_frame=lambda *_args: object(),
        is_resource_sufficient=blocking_detection,
    )
    manager._prepare_frame_detection_cache = lambda: object()

    worker = threading.Thread(
        target=manager.execute_timed_skill,
        args=("ResourceSkill",),
    )
    worker.start()
    assert detector_entered.wait(timeout=2)

    # The scheduler's own generation does not change during a live config
    # transaction. The SkillManager generation must invalidate this callback
    # even though it resumes only after the transaction gate is open again.
    assert manager._apply_config_update(
        {"ResourceSkill": new_skill},
        {"sequence_enabled": False, "macro_steps": []},
    ) is True
    assert manager._config_update_in_progress is False
    assert manager._config_generation == 1

    release_detector.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert manager._resource_condition_history == {}
    manager.input_handler.execute_skill_normal.assert_not_called()
    manager.input_handler.execute_skill_high.assert_not_called()


def test_blocked_old_condition_result_cannot_send_key_after_transaction():
    manager = _bare_skill_manager(running=True)
    old_skill = {
        "Enabled": True,
        "TriggerMode": 0,
        "ExecuteCondition": 0,
        "Key": "OLD",
    }
    manager._skills_config = {"TimedSkill": old_skill}
    manager._frame_usage_stats = {
        "total_frame_gets": 1,
        "cached_frame_usage": 0,
        "performance_ratio": 0.0,
    }
    manager.input_handler = SimpleNamespace(
        execute_skill_normal=mock.Mock(return_value=True),
        execute_skill_high=mock.Mock(return_value=True),
    )
    manager._sync_skill_hold_keys = lambda: True
    manager.unified_scheduler = SimpleNamespace(
        get_status=lambda: {"running": False}
    )
    manager._prepare_frame_detection_cache = lambda: object()

    detector_entered = threading.Event()
    release_detector = threading.Event()

    def blocking_condition(*_args, **_kwargs):
        detector_entered.set()
        assert release_detector.wait(timeout=2)
        return True

    manager._check_execution_conditions = blocking_condition

    worker = threading.Thread(
        target=manager.execute_timed_skill,
        args=("TimedSkill",),
    )
    worker.start()
    assert detector_entered.wait(timeout=2)

    assert manager._apply_config_update(
        {"TimedSkill": dict(old_skill, Key="NEW")},
        {"sequence_enabled": False, "macro_steps": []},
    ) is True
    release_detector.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    manager.input_handler.execute_skill_normal.assert_not_called()
    manager.input_handler.execute_skill_high.assert_not_called()


def _bare_resource_manager() -> ResourceManager:
    manager = object.__new__(ResourceManager)
    manager.hp_config = {
        "enabled": True,
        "key": "1",
        "cooldown": 5000,
        "threshold": 50,
    }
    manager.mp_config = {
        "enabled": True,
        "key": "2",
        "cooldown": 5000,
        "threshold": 50,
    }
    manager.check_interval = 200
    manager._flask_cooldowns = {}
    manager._flask_cooldown_identities = {}
    manager._ocr_number_box = {}
    manager.tesseract_ocr_manager = None
    manager._tesseract_config_signature = None
    manager.input_handler = SimpleNamespace(
        execute_hp_potion=lambda key: True,
        execute_mp_potion=lambda key: True,
    )
    return manager


def test_same_potion_action_identity_preserves_existing_cooldown():
    manager = _bare_resource_manager()
    assert manager._execute_resource("hp", manager.hp_config) is True
    timestamp = manager._flask_cooldowns["hp"]
    identity = manager._flask_cooldown_identities["hp"]

    # Detection and timing parameters may change, but this is still the same
    # enabled HP action and key, so it must not gain an extra immediate use.
    manager.update_config(
        {
            "hp_config": dict(
                manager.hp_config,
                threshold=25,
                cooldown=9000,
            ),
            "mp_config": dict(manager.mp_config),
        }
    )

    assert manager._flask_cooldowns["hp"] == timestamp
    assert manager._flask_cooldown_identities["hp"] == identity


def test_potion_key_or_enabled_change_invalidates_only_its_cooldown():
    manager = _bare_resource_manager()
    assert manager._execute_resource("hp", manager.hp_config) is True
    assert manager._execute_resource("mp", manager.mp_config) is True
    mp_timestamp = manager._flask_cooldowns["mp"]

    manager.update_config(
        {
            "hp_config": dict(manager.hp_config, key="3"),
            "mp_config": dict(manager.mp_config),
        }
    )

    assert "hp" not in manager._flask_cooldowns
    assert "hp" not in manager._flask_cooldown_identities
    assert manager._flask_cooldowns["mp"] == mp_timestamp

    assert manager._execute_resource("hp", manager.hp_config) is True
    manager.update_config(
        {
            "hp_config": dict(manager.hp_config, enabled=False),
            "mp_config": dict(manager.mp_config),
        }
    )

    assert "hp" not in manager._flask_cooldowns
    assert "hp" not in manager._flask_cooldown_identities
    assert manager._flask_cooldowns["mp"] == mp_timestamp


def test_in_place_potion_key_change_cannot_reuse_old_cooldown():
    manager = _bare_resource_manager()
    assert manager._execute_resource("hp", manager.hp_config) is True

    manager.hp_config["key"] = "3"

    assert manager._check_internal_cooldown("hp") is True
    assert "hp" not in manager._flask_cooldowns
    assert "hp" not in manager._flask_cooldown_identities
