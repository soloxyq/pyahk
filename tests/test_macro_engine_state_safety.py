"""MacroEngine 状态事务、STOPPED 安全清理与事件订阅生命周期测试。"""

import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as macro_mod
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.states import MacroState
from torchlight_assistant.utils.window_utils import WindowUtils
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager


def _raise(message):
    raise RuntimeError(message)


def _bare_engine(state):
    engine = object.__new__(MacroEngine)
    engine._state = state
    engine._state_lock = threading.RLock()
    engine._transition_lock = threading.RLock()
    engine._runtime_owner = "main"
    engine._runtime_owner_epoch = 1
    engine._publish_status_update = mock.Mock()
    engine._update_osd_visibility = mock.Mock()
    return engine


def test_state_is_committed_only_after_entry_succeeds():
    engine = _bare_engine(MacroState.STOPPED)
    observed = []
    engine._on_state_enter = lambda new_state, from_state=None: observed.append(
        ("entry", engine._state)
    ) or True
    engine._rollback_failed_state_entry = mock.Mock()

    def record_publish(event_name, *args, **kwargs):
        observed.append((event_name, engine._state))

    with mock.patch.object(macro_mod.event_bus, "publish", record_publish):
        assert engine._set_state(MacroState.READY) is True

    assert observed[0] == ("entry", MacroState.STOPPED)
    assert engine._state == MacroState.READY
    assert ("engine:state_changed", MacroState.READY) in observed
    assert ("engine:macro_ready", MacroState.READY) in observed
    engine._rollback_failed_state_entry.assert_not_called()


def test_entry_exception_keeps_old_state_and_publishes_nothing():
    engine = _bare_engine(MacroState.STOPPED)
    engine._on_state_enter = lambda new_state, from_state=None: _raise("ready failed")
    engine._rollback_failed_state_entry = mock.Mock()

    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._set_state(MacroState.READY) is False

    assert engine._state == MacroState.STOPPED
    publish.assert_not_called()
    engine._publish_status_update.assert_not_called()
    engine._update_osd_visibility.assert_not_called()
    engine._rollback_failed_state_entry.assert_called_once_with(
        MacroState.READY, MacroState.STOPPED
    )


def test_stopped_precommits_before_running_safety_cleanup():
    engine = _bare_engine(MacroState.RUNNING)
    observed = []
    engine._on_state_enter = lambda new_state, from_state=None: observed.append(
        engine._state
    ) or True

    with mock.patch.object(macro_mod.event_bus, "publish"):
        assert engine._set_state(MacroState.STOPPED) is True

    assert observed == [MacroState.STOPPED]
    assert engine._state == MacroState.STOPPED


def test_explicit_entry_rejection_preserves_async_gate_failure_path():
    engine = _bare_engine(MacroState.READY)
    engine._on_state_enter = lambda new_state, from_state=None: False
    engine._rollback_failed_state_entry = mock.Mock()

    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._set_state(MacroState.RUNNING) is False

    assert engine._state == MacroState.READY
    publish.assert_not_called()
    # _open_runtime_gate(False) 已自行 singleShot 回退；这里不得同步补偿或重入转换。
    engine._rollback_failed_state_entry.assert_not_called()


def test_gate_failure_reapplies_stopped_barrier_even_if_state_never_committed():
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._transition_lock = mock.MagicMock()
    engine._enter_stopped_state = mock.Mock()
    engine._set_state = mock.Mock()

    engine._force_stopped_after_gate_failure()

    engine._transition_lock.__enter__.assert_called_once_with()
    engine._enter_stopped_state.assert_called_once_with()
    engine._set_state.assert_not_called()


def test_constructor_transition_lock_is_reentrant_for_sync_fallback():
    engine = object.__new__(MacroEngine)
    with mock.patch.object(
        MacroEngine,
        "_resolve_initial_config_file",
        side_effect=RuntimeError("stop after lock initialization"),
    ):
        try:
            MacroEngine.__init__(engine)
        except RuntimeError as exc:
            assert str(exc) == "stop after lock initialization"
        else:
            raise AssertionError("constructor sentinel was not raised")

    first = engine._transition_lock.acquire(blocking=False)
    second = engine._transition_lock.acquire(blocking=False)
    try:
        assert first is True
        assert second is True
    finally:
        if second:
            engine._transition_lock.release()
        if first:
            engine._transition_lock.release()


def test_gate_failure_sync_fallback_reenters_transition_barrier_without_deadlock():
    from PySide6.QtCore import QTimer

    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._transition_lock = threading.RLock()
    engine._enter_stopped_state = mock.Mock()
    engine._set_state = mock.Mock()

    with engine._transition_lock, mock.patch.object(
        QTimer, "singleShot", side_effect=RuntimeError("timer unavailable")
    ):
        engine._schedule_stopped_after_gate_failure()

    engine._enter_stopped_state.assert_called_once_with()
    engine._set_state.assert_not_called()


def test_ready_arms_main_mode_before_hooks_and_opens_gate_after_preparation():
    order = []
    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(
        arm_main_mode=lambda: order.append("arm_command") or True,
        activate_target_window=lambda: order.append("activate") or True,
        start=lambda: order.append("input_start"),
    )
    engine._arm_main_mode = lambda context: order.append(("arm", context)) or True
    engine._sync_ahk_send_mode = lambda: order.append("send_mode") or True
    engine._prepare_target_window_for_ready = lambda: order.append("target") or True
    engine._register_secondary_hotkeys = lambda: order.append("hooks") or True
    engine.skill_manager = SimpleNamespace(
        prepare_border_only=lambda: order.append("prepare_border")
    )
    engine.border_manager = SimpleNamespace(
        enable_debug_save=lambda: order.append("debug_save"),
        capture_once_for_debug_and_cache=lambda *args: order.append("capture") or object(),
    )
    engine.resource_manager = None
    engine._collect_resource_regions = lambda: {}
    engine._capture_interval_ms = lambda: 40
    engine._open_runtime_gate = lambda context: order.append(("gate", context)) or True

    assert engine._on_state_enter(MacroState.READY) is True
    assert order[0] == ("arm", "进入 READY")
    assert (
        order.index(("arm", "进入 READY"))
        < order.index("send_mode")
        < order.index("target")
        < order.index("hooks")
        < order.index("capture")
    )
    assert order[-1] == ("gate", "进入 READY")


def test_ready_capture_failure_never_opens_runtime_gate():
    engine = object.__new__(MacroEngine)
    engine._arm_main_mode = mock.Mock(return_value=True)
    engine._sync_ahk_send_mode = mock.Mock(return_value=True)
    engine._prepare_target_window_for_ready = mock.Mock(return_value=True)
    engine._register_secondary_hotkeys = mock.Mock(return_value=True)
    engine.input_handler = SimpleNamespace(start=lambda: None)
    engine.skill_manager = SimpleNamespace(prepare_border_only=lambda: None)
    engine.border_manager = SimpleNamespace(
        enable_debug_save=lambda: None,
        capture_once_for_debug_and_cache=lambda *args: None,
    )
    engine.resource_manager = None
    engine._collect_resource_regions = lambda: {}
    engine._capture_interval_ms = lambda: 40
    engine._open_runtime_gate = mock.Mock(return_value=True)

    with pytest.raises(RuntimeError, match="READY 初始化捕获失败"):
        engine._on_state_enter(MacroState.READY)

    engine._open_runtime_gate.assert_not_called()


def test_ready_rejects_invalid_or_unsynchronized_send_mode_before_target():
    engine = object.__new__(MacroEngine)
    engine._arm_main_mode = mock.Mock(return_value=True)
    engine._prepare_target_window_for_ready = mock.Mock(return_value=True)
    engine._register_secondary_hotkeys = mock.Mock(return_value=True)
    engine._sync_ahk_send_mode = mock.Mock(return_value=False)

    with pytest.raises(RuntimeError, match="输入模式"):
        engine._on_state_enter(MacroState.READY)

    engine._prepare_target_window_for_ready.assert_not_called()
    engine._register_secondary_hotkeys.assert_not_called()


def test_send_mode_validation_is_strict_and_propagates_ahk_rejection():
    engine = object.__new__(MacroEngine)
    engine._global_config = {"input_mode": "invalid"}
    engine.input_handler = SimpleNamespace(set_send_mode=mock.Mock(return_value=True))
    assert engine._sync_ahk_send_mode() is False
    engine.input_handler.set_send_mode.assert_not_called()

    engine._global_config = {"input_mode": "control"}
    engine.input_handler.set_send_mode.return_value = False
    assert engine._sync_ahk_send_mode() is False
    engine.input_handler.set_send_mode.assert_called_once_with("control")


def test_main_mode_arm_failure_schedules_stopped_barrier():
    from PySide6.QtCore import QTimer

    engine = object.__new__(MacroEngine)
    engine._prepared_mode = "combat"
    engine.input_handler = SimpleNamespace(
        arm_main_mode=lambda: False,
        set_runtime_owner=lambda owner, epoch: True,
    )

    with mock.patch.object(QTimer, "singleShot") as single_shot:
        assert engine._arm_main_mode("进入 READY") is False

    single_shot.assert_called_once()
    assert single_shot.call_args.args[0] == 0
    assert callable(single_shot.call_args.args[1])
    assert engine._runtime_attempt_epoch == 1


def test_old_gate_failure_callback_cannot_stop_a_new_runtime_owner():
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._transition_lock = threading.RLock()
    engine._runtime_attempt_epoch = 2
    engine._enter_stopped_state = mock.Mock()
    engine._set_state = mock.Mock()

    engine._force_stopped_after_gate_failure(1)

    engine._enter_stopped_state.assert_not_called()
    engine._set_state.assert_not_called()


def test_window_target_combines_class_and_executable_without_losing_either():
    assert WindowUtils.build_ahk_target(
        {"ahk_class": "UnrealWindow", "ahk_exe": "Game.exe"}
    ) == "ahk_class UnrealWindow ahk_exe Game.exe"
    assert WindowUtils.build_ahk_target(
        {"ahk_class": "", "ahk_exe": "PathOfExile.exe"}
    ) == "ahk_exe PathOfExile.exe"


def test_malformed_explicit_window_target_never_becomes_foreground_fallback():
    assert not WindowUtils.is_target_config_valid(
        {"ahk_class": "", "ahk_exe": ["Game.exe"]}
    )
    assert not WindowUtils.is_target_config_valid(
        {"ahk_class": "", "ahk_exe": "Game\n.exe"}
    )
    assert not WindowUtils.is_target_config_valid({"ahk_exe": "\nGame.exe"})

    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(
        set_target_window=mock.Mock(return_value=True),
        activate_target_window=mock.Mock(return_value=True),
    )
    engine._prepared_mode = "combat"
    engine._global_config = {
        "input_mode": "direct",
        "window_activation": {
            "enabled": False,
            "ahk_class": "",
            "ahk_exe": "Game\n.exe",
        },
    }

    assert engine._prepare_target_window_for_ready() is False
    engine.input_handler.set_target_window.assert_not_called()

    manager = object.__new__(BorderFrameManager)
    manager.set_window_activation_config(engine._global_config["window_activation"])
    with mock.patch.object(WindowUtils, "find_target_window") as find:
        assert manager._get_target_window_handle() is None
        find.assert_not_called()


def test_target_lookup_fallback_applies_only_to_unconfigured_target():
    from torchlight_assistant.utils import window_utils as window_mod
    fake_gui = SimpleNamespace(GetForegroundWindow=mock.Mock(return_value=4321))
    with mock.patch.object(window_mod, "win32gui", fake_gui), mock.patch.object(
        WindowUtils, "find_window_by_process_name", return_value=None
    ):
        assert WindowUtils.find_target_window({"ahk_exe": "absent.exe"}, fallback_to_foreground=True) is None
        assert WindowUtils.find_target_window({"ahk_exe": False}, fallback_to_foreground=True) is None
        fake_gui.GetForegroundWindow.assert_not_called()
        assert WindowUtils.find_target_window({}, fallback_to_foreground=True) == 4321


def test_complete_config_validation_rejects_malformed_window_target():
    with pytest.raises(ValueError, match="window_activation"):
        MacroEngine._validated_config_sections(
            {
                "skills": {},
                "global": {
                    "window_activation": {
                        "enabled": False,
                        "ahk_class": "",
                        "ahk_exe": {"name": "Game.exe"},
                    }
                },
            }
        )


def test_ready_target_syncs_exe_but_only_activates_when_enabled():
    calls = []
    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(
        set_target_window=lambda target: calls.append(("target", target)) or True,
        activate_target_window=lambda: calls.append(("activate",)) or True,
    )
    engine._global_config = {
        "input_mode": "direct",
        "window_activation": {
            "enabled": False,
            "ahk_class": "",
            "ahk_exe": "PathOfExile.exe",
        },
    }

    assert engine._prepare_target_window_for_ready() is True
    assert calls == [("target", "ahk_exe PathOfExile.exe")]

    engine._global_config["window_activation"]["enabled"] = True
    assert engine._prepare_target_window_for_ready() is True
    assert calls[-2:] == [
        ("target", "ahk_exe PathOfExile.exe"),
        ("activate",),
    ]


def test_control_mode_without_target_fails_closed_before_ready():
    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(
        set_target_window=mock.Mock(return_value=True),
        activate_target_window=mock.Mock(return_value=True),
    )
    engine._global_config = {
        "input_mode": "control",
        "window_activation": {"enabled": False, "ahk_class": "", "ahk_exe": ""},
    }

    assert engine._prepare_target_window_for_ready() is False
    engine.input_handler.set_target_window.assert_not_called()
    engine.input_handler.activate_target_window.assert_not_called()


def test_explicit_capture_target_miss_never_falls_back_to_foreground():
    manager = object.__new__(BorderFrameManager)
    manager.window_activation_config = {
        "enabled": False,
        "ahk_class": "",
        "ahk_exe": "missing-game.exe",
    }

    with mock.patch.object(WindowUtils, "find_target_window", return_value=None) as find:
        assert manager._get_target_window_handle() is None

    find.assert_called_once_with(manager.window_activation_config)


def test_empty_capture_target_retains_foreground_compatibility():
    manager = object.__new__(BorderFrameManager)
    manager.window_activation_config = {}

    with mock.patch.object(
        WindowUtils, "find_target_window", side_effect=[None, 4321]
    ) as find:
        assert manager._get_target_window_handle() == 4321

    assert find.call_args_list == [
        mock.call({}),
        mock.call({}, fallback_to_foreground=True),
    ]


def test_root_hook_failure_aborts_initialization_and_stops_partial_ahk():
    calls = []
    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(
        register_root_hook=lambda key: calls.append(key) or key != "F7",
    )
    engine._subscribe_event = mock.Mock()
    engine.cleanup = mock.Mock()

    try:
        engine._setup_primary_hotkey()
    except RuntimeError as exc:
        assert "永久根热键初始化失败" in str(exc)
    else:
        raise AssertionError("根热键注册失败未阻止引擎初始化")

    assert calls == ["F8", "F7"]
    engine.cleanup.assert_called_once_with()
    engine._subscribe_event.assert_not_called()


def test_stopped_ignores_dynamic_intercept_delivered_after_f8():
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._current_stationary_key = "x"
    engine._current_boss_mode_key = "b"
    engine._on_stationary_key_press = mock.Mock()
    engine._toggle_boss_mode = mock.Mock()
    engine._on_z_key_press = mock.Mock()

    # 模拟 GUI 卡顿期间 FIFO 先收到 F8，在停机完成后才补发后续 X。
    engine._handle_ahk_intercept_key("X")
    engine._handle_ahk_intercept_key("B")
    engine._handle_ahk_intercept_key("Z")

    engine._on_stationary_key_press.assert_not_called()
    engine._toggle_boss_mode.assert_not_called()
    engine._on_z_key_press.assert_not_called()


def test_pathfinding_ready_does_not_register_or_swallow_boss_hotkey():
    engine = object.__new__(MacroEngine)
    engine._prepared_mode = "pathfinding"
    engine._global_config = {
        "sequence_enabled": False,
        "boss_mode_hotkey": "XButton2",
        "stationary_mode_config": {},
        "priority_keys": {},
    }
    engine.input_handler = SimpleNamespace(register_hook=mock.Mock(return_value=True))

    assert engine._register_config_based_hotkeys() is True
    engine.input_handler.register_hook.assert_not_called()
    assert engine._current_boss_mode_key == ""


def test_start_snapshot_is_validated_before_engine_commit():
    engine = object.__new__(MacroEngine)
    engine._skills_config = {"old": {}}
    engine._global_config = {"old": True}
    engine.sound_manager = SimpleNamespace(update_config=mock.Mock())

    assert engine._apply_start_config_snapshot({"skills": [], "global": {}}, "F9") is False
    assert engine._skills_config == {"old": {}}
    assert engine._global_config == {"old": True}

    snapshot = {
        "skills": {"Skill1": {"Enabled": True, "Key": "right_mouse"}},
        "global": {"input_mode": "direct"},
    }
    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._apply_start_config_snapshot(snapshot, "F9") is True

    assert engine._skills_config["Skill1"]["Key"] == "RButton"
    assert engine._global_config == {"input_mode": "direct"}
    engine.sound_manager.update_config.assert_called_once_with(engine._global_config)
    publish.assert_called_once_with(
        "engine:config_updated", engine._skills_config, engine._global_config
    )


def test_f9_start_recovers_transport_before_preparing():
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._transition_lock = threading.RLock()
    engine.affix_reroll_manager = SimpleNamespace(
        status=SimpleNamespace(is_running=False)
    )
    engine._recover_ahk_transport_for_start = mock.Mock(return_value=False)
    engine._apply_start_config_snapshot = mock.Mock(return_value=True)
    engine.prepare_border_only = mock.Mock(return_value=True)

    engine._on_f9_key_press({"skills": {}, "global": {}})

    engine._apply_start_config_snapshot.assert_not_called()
    engine.prepare_border_only.assert_not_called()

    engine._recover_ahk_transport_for_start.return_value = True
    snapshot = {"skills": {}, "global": {}}
    engine._on_f9_key_press(snapshot)
    engine._apply_start_config_snapshot.assert_called_once_with(snapshot, "F9")
    engine.prepare_border_only.assert_called_once_with()
    assert engine._prepared_mode == "pathfinding"


def test_stationary_toggle_commits_only_after_ahk_accepts():
    engine = object.__new__(MacroEngine)
    engine._global_config = {
        "stationary_mode_config": {"mode_type": "block_mouse"}
    }
    engine._stationary_mode_active = False
    engine._publish_status_update = mock.Mock()
    engine.input_handler = SimpleNamespace(set_stationary_mode=mock.Mock(return_value=False))

    engine._on_stationary_key_press()
    assert engine._stationary_mode_active is False
    engine._publish_status_update.assert_not_called()

    engine.input_handler.set_stationary_mode.return_value = True
    engine._on_stationary_key_press()
    assert engine._stationary_mode_active is True
    engine._publish_status_update.assert_called_once_with()


def test_unknown_stationary_mode_fails_closed_before_send():
    engine = object.__new__(MacroEngine)
    engine._global_config = {
        "stationary_mode_config": {"mode_type": "future_mode"}
    }
    engine._stationary_mode_active = False
    engine._publish_status_update = mock.Mock()
    engine.input_handler = SimpleNamespace(set_stationary_mode=mock.Mock(return_value=True))

    engine._on_stationary_key_press()

    engine.input_handler.set_stationary_mode.assert_not_called()
    engine._publish_status_update.assert_not_called()
    assert engine._stationary_mode_active is False


def test_stopped_cleanup_continues_after_component_exception():
    order = []

    def step(name, result=None):
        def run(*args, **kwargs):
            order.append(name)
            return result

        return run

    def failing_skill_stop(**kwargs):
        order.append("skill_stop")
        raise RuntimeError("scheduler stuck")

    engine = object.__new__(MacroEngine)
    engine._force_move_active = True
    engine._stationary_mode_active = True
    engine._prepared_mode = "combat"
    engine._global_config = {
        "debug_mode": {"enabled": False},
        "stationary_mode_config": {"mode_type": "block_mouse"},
    }
    engine.input_handler = SimpleNamespace(
        reset_runtime=step("reset", True),
        set_drop_non_emergency=step("drop_off"),
        dry_run_mode=False,
        set_dry_run_mode=step("dry_run"),
    )
    engine.skill_manager = SimpleNamespace(stop=failing_skill_stop)
    engine.pathfinding_manager = SimpleNamespace(stop=step("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=step("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=step("border_stop"))
    engine._set_boss_mode_active = step("boss_off")

    assert engine._on_state_enter(MacroState.STOPPED) is True

    assert order[0] == "reset", "AHK 原子 STOPPED 事务必须是第一条安全动作"
    assert "path_stop" in order and "resource_stop" in order
    assert order.count("reset") == 1
    assert "clear" not in order and "clear_hooks" not in order
    assert engine._force_move_active is False
    assert engine._stationary_mode_active is False
    assert engine._prepared_mode == "none"


def test_running_entry_exception_rolls_back_to_ready():
    order = []
    engine = _bare_engine(MacroState.READY)
    engine._prepared_mode = "combat"
    engine._force_move_active = False
    engine._open_runtime_gate = lambda context: order.append(("open", context)) or True
    engine.input_handler = SimpleNamespace(
        set_force_move_state=lambda active: True,
        set_accepting_actions=lambda enabled, **kwargs: order.append(("gate", enabled)) or True,
    )
    engine._start_subsystems_based_on_mode = lambda: _raise("resource start failed")
    engine.skill_manager = SimpleNamespace(stop=lambda **kwargs: order.append("skill_stop"))
    engine.pathfinding_manager = SimpleNamespace(stop=lambda: order.append("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=lambda: order.append("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=lambda: order.append("border_stop"))

    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._set_state(MacroState.RUNNING) is False

    assert engine._state == MacroState.READY
    publish.assert_not_called()
    assert ("gate", False) in order
    assert "skill_stop" in order and "clear" not in order
    assert ("open", "RUNNING 入口失败后恢复 READY") in order


def test_running_rollback_cannot_reopen_gate_after_physical_f8_latch():
    from PySide6.QtCore import QTimer

    order = []
    engine = _bare_engine(MacroState.READY)
    engine._prepared_mode = "combat"
    engine.input_handler = SimpleNamespace(
        set_accepting_actions=lambda enabled, **kwargs: order.append(("gate", enabled))
        or (not enabled),
    )
    engine.skill_manager = SimpleNamespace(stop=lambda **kwargs: order.append("skill_stop"))
    engine.pathfinding_manager = SimpleNamespace(stop=lambda: order.append("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=lambda: order.append("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=lambda: order.append("border_stop"))

    with mock.patch.object(QTimer, "singleShot") as single_shot:
        engine._rollback_failed_state_entry(MacroState.RUNNING, MacroState.READY)

    assert order[0] == ("gate", False)
    assert order[-1] == ("gate", True)
    single_shot.assert_called_once()
    assert single_shot.call_args.args[0] == 0
    assert callable(single_shot.call_args.args[1])
    assert engine._state == MacroState.READY


def test_silent_pathfinding_start_failure_does_not_commit_running():
    order = []
    engine = _bare_engine(MacroState.READY)
    engine._prepared_mode = "pathfinding"
    engine._force_move_active = False
    engine._global_config = {"capture_interval": 40}
    engine._open_runtime_gate = lambda context: True
    engine.input_handler = SimpleNamespace(
        set_force_move_state=lambda active: True,
        set_accepting_actions=lambda enabled, **kwargs: True,
    )
    engine.skill_manager = SimpleNamespace(
        stop=lambda **kwargs: order.append("skill_stop")
    )
    engine.pathfinding_manager = SimpleNamespace(
        is_running=False,
        start=lambda: order.append("path_start"),
        stop=lambda: order.append("path_stop"),
    )
    engine.resource_manager = SimpleNamespace(
        start=lambda: order.append("resource_start"),
        stop=lambda: order.append("resource_stop"),
    )
    engine.border_manager = SimpleNamespace(
        running=True,
        start_capture_loop=lambda **kwargs: order.append("capture_start"),
        resume_capture=lambda: None,
        stop=lambda: order.append("capture_stop"),
    )

    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._set_state(MacroState.RUNNING) is False

    assert engine._state == MacroState.READY
    assert "path_start" in order
    assert "resource_start" not in order
    assert "path_stop" in order and "capture_stop" in order
    publish.assert_not_called()


def test_event_subscription_registry_is_idempotent():
    engine = object.__new__(MacroEngine)
    engine._event_subscriptions = []
    handler = lambda: None

    with mock.patch.object(macro_mod.event_bus, "subscribe") as subscribe, mock.patch.object(
        macro_mod.event_bus, "unsubscribe"
    ) as unsubscribe:
        engine._subscribe_event("test:event", handler)
        engine._subscribe_event("test:event", handler)
        engine._unsubscribe_event_handlers()
        engine._unsubscribe_event_handlers()

    subscribe.assert_called_once_with("test:event", handler)
    unsubscribe.assert_called_once_with("test:event", handler)
    assert engine._event_subscriptions == []


def test_child_managers_unsubscribe_global_events_on_cleanup_once():
    from torchlight_assistant.core.pathfinding_manager import PathfindingManager
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollManager,
    )

    pathfinding = object.__new__(PathfindingManager)
    pathfinding._cleanup_done = False
    pathfinding.stop = mock.Mock()

    affix = object.__new__(SimpleAffixRerollManager)
    affix._cleanup_done = False
    affix.stop_reroll = mock.Mock()

    with mock.patch.object(macro_mod.event_bus, "unsubscribe") as unsubscribe:
        pathfinding.cleanup()
        pathfinding.cleanup()
        affix.cleanup()
        affix.cleanup()

    pathfinding.stop.assert_called_once_with()
    affix.stop_reroll.assert_called_once_with("应用清理")
    assert unsubscribe.call_args_list == [
        mock.call("engine:config_updated", pathfinding._on_config_updated),
        mock.call("hotkey:affix_reroll_start", affix._on_f7_pressed),
        mock.call("engine:config_updated", affix._on_config_updated),
    ]


def test_affix_mode_owns_runtime_gate_across_start_click_and_stop():
    from torchlight_assistant.core import simple_affix_reroll_manager as affix_mod
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
    )

    calls = []
    gate = {"open": False}

    def recover_transport():
        calls.append("recover")
        return True

    def set_accepting_actions(enabled, **kwargs):
        calls.append(f"gate:{enabled}")
        gate["open"] = enabled
        return True

    def reset_runtime():
        calls.append("reset")
        gate["open"] = False
        return True

    def click_mouse_at(x, y):
        calls.append(f"click:{x},{y}")
        return gate["open"]

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            calls.append("thread:create")

        def start(self):
            calls.append("thread:start")

        def is_alive(self):
            return False

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        recover_transport=recover_transport,
        set_target_window=lambda target: calls.append(f"target:{target}") or True,
        set_runtime_owner=lambda owner, epoch: calls.append(
            f"owner:{owner}:{epoch}"
        ) or True,
        set_accepting_actions=set_accepting_actions,
        reset_runtime=reset_runtime,
        click_mouse_at=click_mouse_at,
    )
    manager.border_manager = SimpleNamespace(
        window_activation_config={"ahk_exe": "game.exe"}
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
        first_affix_button_coord=(11, 21),
        replace_button_coord=(12, 22),
        close_button_coord=(13, 23),
    )
    manager.status = SimpleAffixRerollStatus()
    manager.ocr_manager = SimpleNamespace(is_ready=lambda: True)
    manager._run_lock = threading.RLock()
    manager._run_generation = 0
    manager._active_run = None
    manager._gate_owner_generation = None
    manager._reroll_thread = None

    with mock.patch.object(affix_mod.threading, "Thread", FakeThread), mock.patch.object(
        affix_mod.event_bus, "publish"
    ):
        assert manager.start_reroll() is True
        assert calls[:6] == [
            "target:ahk_exe game.exe",
            "recover",
            "owner:affix:1",
            "gate:True",
            "thread:create",
            "thread:start",
        ]
        run = manager._active_run
        assert run is not None
        assert manager._click_at(run, (10, 20), "附魔按钮") is True
        assert calls[-1] == "click:10,20"

        manager.stop_reroll("测试停止")

    assert "reset" in calls
    assert calls.index("reset") > calls.index("click:10,20")
    assert gate["open"] is False
    assert manager.status.is_running is False
    assert manager._active_run is None
    assert manager._gate_owner_generation is None


def test_affix_mode_refuses_start_when_runtime_gate_cannot_open():
    from torchlight_assistant.core import simple_affix_reroll_manager as affix_mod
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
    )

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        recover_transport=lambda: True,
        set_target_window=lambda target: True,
        set_runtime_owner=lambda owner, epoch: True,
        set_accepting_actions=lambda enabled, **kwargs: False,
        reset_runtime=lambda: True,
    )
    manager.border_manager = SimpleNamespace(
        window_activation_config={"ahk_exe": "game.exe"}
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
        first_affix_button_coord=(11, 21),
        replace_button_coord=(12, 22),
        close_button_coord=(13, 23),
    )
    manager.status = SimpleAffixRerollStatus()
    manager.ocr_manager = SimpleNamespace(is_ready=lambda: True)
    manager._run_lock = threading.RLock()
    manager._run_generation = 0
    manager._active_run = None
    manager._gate_owner_generation = None
    manager._reroll_thread = None

    with mock.patch.object(affix_mod.threading, "Thread") as thread, mock.patch.object(
        affix_mod.event_bus, "publish"
    ):
        assert manager.start_reroll() is False

    thread.assert_not_called()
    assert manager.status.is_running is False
    assert manager._active_run is None
    assert manager._gate_owner_generation is None


def test_stale_affix_worker_cannot_click_or_close_new_generation_gate():
    from torchlight_assistant.core import simple_affix_reroll_manager as affix_mod
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
    )

    old_ocr_entered = threading.Event()
    release_old_ocr = threading.Event()
    new_ocr_entered = threading.Event()
    ocr_lock = threading.Lock()
    ocr_calls = 0
    gate_calls = []
    clicks = []
    gate = {"open": False}

    class BlockingOCR:
        @staticmethod
        def is_ready():
            return True

        @staticmethod
        def get_text_from_image(frame):
            nonlocal ocr_calls
            with ocr_lock:
                ocr_calls += 1
                call = ocr_calls
            if call == 1:
                old_ocr_entered.set()
                assert release_old_ocr.wait(2.0)
                return ["随机词缀"]
            new_ocr_entered.set()
            return []

    def set_accepting_actions(enabled, **kwargs):
        gate_calls.append(enabled)
        gate["open"] = enabled
        return True

    def reset_runtime():
        gate_calls.append(False)
        gate["open"] = False
        return True

    def click_mouse_at(x, y):
        clicks.append((x, y))
        return gate["open"]

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        recover_transport=lambda: True,
        set_target_window=lambda target: True,
        set_runtime_owner=lambda owner, epoch: True,
        set_accepting_actions=set_accepting_actions,
        reset_runtime=reset_runtime,
        click_mouse_at=click_mouse_at,
    )
    manager.border_manager = SimpleNamespace(
        window_activation_config={"ahk_exe": "game.exe"},
        capture_screen_for_reroll=lambda region: object()
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        max_attempts=100,
        click_delay=5000,
        enchant_button_coord=(10, 20),
        first_affix_button_coord=(11, 21),
        replace_button_coord=(12, 22),
        close_button_coord=(13, 23),
    )
    manager.status = SimpleAffixRerollStatus()
    manager.ocr_manager = BlockingOCR()
    manager._run_lock = threading.RLock()
    manager._run_generation = 0
    manager._active_run = None
    manager._gate_owner_generation = None
    manager._reroll_thread = None
    manager._resolve_ocr_region = lambda: (0, 0, 10, 10)
    manager.WORKER_JOIN_TIMEOUT_SECONDS = 0.01

    with mock.patch.object(affix_mod.event_bus, "publish"):
        assert manager.start_reroll() is True
        run1 = manager._active_run
        assert run1 is not None and old_ocr_entered.wait(1.0)

        # join 预算到期时 run1 仍阻塞在 OCR，但它已被立即失效并关闸。
        manager.stop_reroll("stop run1")
        assert run1.thread is not None and run1.thread.is_alive()
        assert gate_calls == [True, False]

        assert manager.start_reroll() is True
        run2 = manager._active_run
        assert run2 is not None and run2 is not run1
        assert new_ocr_entered.wait(1.0)
        assert gate_calls == [True, False, True]

        release_old_ocr.set()
        run1.thread.join(timeout=1.0)
        assert not run1.thread.is_alive()

        # run1 返回的 OCR 结果本会点击附魔；generation 失效后必须
        # 在任何状态/输入副作前退出，finally 也不得消费 run2 的 owner。
        assert clicks == []
        assert gate_calls == [True, False, True]
        assert manager._active_run is run2
        assert manager.status is run2.status
        assert run2.status.is_running is True
        assert not run2.stop_event.is_set()

        manager.stop_reroll("cleanup run2")

    assert gate_calls == [True, False, True, False]


def test_affix_stop_notifications_complete_before_next_generation_can_start():
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
        _AffixRerollRun,
    )

    publish_entered = threading.Event()
    release_publish = threading.Event()
    competing_lock_acquired = threading.Event()
    published = []

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        reset_runtime=lambda: True,
    )
    manager.config = SimpleAffixRerollConfig()
    manager.status = SimpleAffixRerollStatus(is_running=True)
    manager._run_lock = threading.RLock()
    manager._run_generation = 1
    run = _AffixRerollRun(
        generation=1,
        config=manager.config,
        status=manager.status,
    )
    manager._active_run = run
    manager._gate_owner_generation = run.generation
    manager.WORKER_JOIN_TIMEOUT_SECONDS = 0.01

    def blocking_status_publish(status=None):
        published.append("status")
        publish_entered.set()
        assert release_publish.wait(1.0)

    manager._publish_status_update = blocking_status_publish

    def acquire_generation_lock():
        with manager._run_lock:
            competing_lock_acquired.set()

    with mock.patch(
        "torchlight_assistant.core.simple_affix_reroll_manager.event_bus.publish",
        side_effect=lambda event, *args, **kwargs: published.append(event),
    ):
        stop_thread = threading.Thread(
            target=manager._stop_run,
            args=(run, "test stop"),
        )
        stop_thread.start()
        assert publish_entered.wait(1.0)

        contender = threading.Thread(target=acquire_generation_lock)
        contender.start()
        assert not competing_lock_acquired.wait(0.05)

        release_publish.set()
        stop_thread.join(timeout=1.0)
        contender.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert not contender.is_alive()
    assert competing_lock_acquired.is_set()
    assert published == ["status", "affix_reroll:show_ui"]


def test_affix_start_does_not_hide_ui_after_synchronous_stop_subscriber():
    from torchlight_assistant.core import simple_affix_reroll_manager as affix_mod
    from torchlight_assistant.core.simple_affix_reroll_manager import (
        SimpleAffixRerollConfig,
        SimpleAffixRerollManager,
        SimpleAffixRerollStatus,
    )

    class FakeThread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args

        def start(self):
            pass

        def is_alive(self):
            return False

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        recover_transport=lambda: True,
        set_target_window=lambda target: True,
        set_runtime_owner=lambda owner, epoch: True,
        set_accepting_actions=lambda enabled, **kwargs: True,
        reset_runtime=lambda: True,
    )
    manager.border_manager = SimpleNamespace(
        window_activation_config={"ahk_exe": "game.exe"}
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
        first_affix_button_coord=(11, 21),
        replace_button_coord=(12, 22),
        close_button_coord=(13, 23),
    )
    manager.status = SimpleAffixRerollStatus()
    manager.ocr_manager = SimpleNamespace(is_ready=lambda: True)
    manager._run_lock = threading.RLock()
    manager._run_generation = 0
    manager._active_run = None
    manager._gate_owner_generation = None
    manager._reroll_thread = None
    manager.WORKER_JOIN_TIMEOUT_SECONDS = 0.01

    events = []
    stop_requested = False

    def publish_status(status=None):
        nonlocal stop_requested
        events.append("status")
        if not stop_requested and manager._active_run is not None:
            stop_requested = True
            manager.stop_reroll("同步订阅者停止")

    manager._publish_status_update = publish_status

    with mock.patch.object(affix_mod.threading, "Thread", FakeThread), mock.patch.object(
        affix_mod.event_bus,
        "publish",
        side_effect=lambda event, *args, **kwargs: events.append(event),
    ):
        assert manager.start_reroll() is True

    assert manager._active_run is None
    assert manager.status.is_running is False
    assert events == ["status", "status", "affix_reroll:show_ui"]


def test_cleanup_unsubscribes_before_components_and_is_idempotent():
    engine = object.__new__(MacroEngine)
    engine._cleanup_lock = threading.Lock()
    engine._cleanup_done = False
    engine.skill_manager = object()
    engine.pathfinding_manager = object()
    engine.affix_reroll_manager = object()
    engine.resource_manager = object()
    engine.border_manager = object()
    engine.input_handler = object()
    engine._unsubscribe_event_handlers = mock.Mock()
    engine._cleanup_layer = mock.Mock()

    engine.cleanup()
    engine.cleanup()

    engine._unsubscribe_event_handlers.assert_called_once_with()
    assert engine._cleanup_layer.call_count == 2


def test_transport_fuse_only_clears_after_successful_ping_probe():
    engine = object.__new__(MacroEngine)
    engine._ahk_transport_failed = True
    engine.input_handler = SimpleNamespace(recover_transport=lambda: False)

    assert engine._recover_ahk_transport_for_start() is False
    assert engine._ahk_transport_failed is True

    engine.input_handler.recover_transport = lambda: True
    assert engine._recover_ahk_transport_for_start() is True
    assert engine._ahk_transport_failed is False


def test_transport_recovered_event_is_idempotent():
    engine = object.__new__(MacroEngine)
    engine._ahk_transport_failed = True
    engine._on_ahk_transport_recovered()
    engine._on_ahk_transport_recovered()
    assert engine._ahk_transport_failed is False


def test_skill_input_sync_failure_schedules_exact_stopped_callback():
    from PySide6.QtCore import QTimer

    engine = object.__new__(MacroEngine)
    with mock.patch.object(QTimer, "singleShot") as single_shot:
        engine._on_skill_input_sync_failed("macro steps rejected")

    single_shot.assert_called_once()
    assert single_shot.call_args[0][0] == 0
    callback = single_shot.call_args[0][1]
    assert callback.__self__ is engine
    assert callback.__func__ is MacroEngine._stop_due_to_skill_input_failure


def test_skill_input_sync_failure_callback_enters_stopped():
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.RUNNING
    engine.stop_macro = mock.Mock(return_value=True)

    engine._stop_due_to_skill_input_failure()

    engine.stop_macro.assert_called_once_with()


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
