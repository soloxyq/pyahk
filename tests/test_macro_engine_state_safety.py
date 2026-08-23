"""MacroEngine 状态事务、STOPPED 安全清理与事件订阅生命周期测试。"""

import os
import sys
import threading
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as macro_mod
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.states import MacroState


def _raise(message):
    raise RuntimeError(message)


def _bare_engine(state):
    engine = object.__new__(MacroEngine)
    engine._state = state
    engine._state_lock = threading.RLock()
    engine._transition_lock = threading.RLock()
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
    engine._register_secondary_hotkeys = lambda: order.append("hooks") or True
    engine.skill_manager = SimpleNamespace(
        prepare_border_only=lambda: order.append("prepare_border")
    )
    engine.border_manager = SimpleNamespace(
        enable_debug_save=lambda: order.append("debug_save"),
        capture_once_for_debug_and_cache=lambda *args: order.append("capture") or None,
    )
    engine.resource_manager = None
    engine._collect_resource_regions = lambda: {}
    engine._capture_interval_ms = lambda: 40
    engine._open_runtime_gate = lambda context: order.append(("gate", context)) or True

    assert engine._on_state_enter(MacroState.READY) is True
    assert order[0] == ("arm", "进入 READY")
    assert order.index(("arm", "进入 READY")) < order.index("hooks") < order.index("capture")
    assert order[-1] == ("gate", "进入 READY")


def test_main_mode_arm_failure_schedules_stopped_barrier():
    from PySide6.QtCore import QTimer

    engine = object.__new__(MacroEngine)
    engine.input_handler = SimpleNamespace(arm_main_mode=lambda: False)

    with mock.patch.object(QTimer, "singleShot") as single_shot:
        assert engine._arm_main_mode("进入 READY") is False

    single_shot.assert_called_once_with(0, engine._force_stopped_after_gate_failure)


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


def test_stopped_cleanup_continues_after_component_exception():
    order = []

    def step(name, result=None):
        def run(*args, **kwargs):
            order.append(name)
            return result

        return run

    def failing_skill_stop():
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
        set_accepting_actions=step("gate", True),
        set_stationary_mode=lambda active, mode: order.append(
            ("stationary", active, mode)
        ) or True,
        set_drop_non_emergency=step("drop_off"),
        clear_queue=step("clear", True),
        clear_all_configurable_hooks=step("clear_hooks", True),
        dry_run_mode=False,
        set_dry_run_mode=step("dry_run"),
    )
    engine.skill_manager = SimpleNamespace(stop=failing_skill_stop)
    engine.pathfinding_manager = SimpleNamespace(stop=step("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=step("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=step("border_stop"))
    engine._set_boss_mode_active = step("boss_off")

    assert engine._on_state_enter(MacroState.STOPPED) is True

    assert order[0] == "gate", "AHK 原子停止屏障必须是第一条安全动作"
    assert "path_stop" in order and "resource_stop" in order
    assert "clear_hooks" in order
    assert order.count("clear") == 2
    assert order.index("clear_hooks") < max(i for i, item in enumerate(order) if item == "clear")
    assert engine._force_move_active is False
    assert engine._stationary_mode_active is False
    assert ("stationary", False, "block_mouse") in order
    assert engine._prepared_mode == "none"


def test_running_entry_exception_rolls_back_to_ready():
    order = []
    engine = _bare_engine(MacroState.READY)
    engine._prepared_mode = "combat"
    engine._force_move_active = False
    engine._open_runtime_gate = lambda context: order.append(("open", context)) or True
    engine.input_handler = SimpleNamespace(
        set_force_move_state=lambda active: True,
        set_accepting_actions=lambda enabled: order.append(("gate", enabled)) or True,
        clear_queue=lambda: order.append("clear") or True,
    )
    engine._start_subsystems_based_on_mode = lambda: _raise("resource start failed")
    engine.skill_manager = SimpleNamespace(stop=lambda: order.append("skill_stop"))
    engine.pathfinding_manager = SimpleNamespace(stop=lambda: order.append("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=lambda: order.append("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=lambda: order.append("border_stop"))

    with mock.patch.object(macro_mod.event_bus, "publish") as publish:
        assert engine._set_state(MacroState.RUNNING) is False

    assert engine._state == MacroState.READY
    publish.assert_not_called()
    assert ("gate", False) in order
    assert "skill_stop" in order and "clear" in order
    assert ("open", "RUNNING 入口失败后恢复 READY") in order


def test_running_rollback_cannot_reopen_gate_after_physical_f8_latch():
    from PySide6.QtCore import QTimer

    order = []
    engine = _bare_engine(MacroState.READY)
    engine._prepared_mode = "combat"
    engine.input_handler = SimpleNamespace(
        set_accepting_actions=lambda enabled: order.append(("gate", enabled))
        or (not enabled),
        clear_queue=lambda: order.append("clear") or True,
    )
    engine.skill_manager = SimpleNamespace(stop=lambda: order.append("skill_stop"))
    engine.pathfinding_manager = SimpleNamespace(stop=lambda: order.append("path_stop"))
    engine.resource_manager = SimpleNamespace(stop=lambda: order.append("resource_stop"))
    engine.border_manager = SimpleNamespace(stop=lambda: order.append("border_stop"))

    with mock.patch.object(QTimer, "singleShot") as single_shot:
        engine._rollback_failed_state_entry(MacroState.RUNNING, MacroState.READY)

    assert order[0] == ("gate", False)
    assert order[-1] == ("gate", True)
    single_shot.assert_called_once_with(0, engine._force_stopped_after_gate_failure)
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
        set_accepting_actions=lambda enabled: True,
        clear_queue=lambda: True,
    )
    engine.skill_manager = SimpleNamespace(stop=lambda: order.append("skill_stop"))
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

    def set_accepting_actions(enabled):
        calls.append(f"gate:{enabled}")
        gate["open"] = enabled
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
        set_accepting_actions=set_accepting_actions,
        click_mouse_at=click_mouse_at,
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
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
        assert calls[:4] == ["recover", "gate:True", "thread:create", "thread:start"]
        run = manager._active_run
        assert run is not None
        assert manager._click_at(run, (10, 20), "附魔按钮") is True
        assert calls[-1] == "click:10,20"

        manager.stop_reroll("测试停止")

    assert "gate:False" in calls
    assert calls.index("gate:False") > calls.index("click:10,20")
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
        set_accepting_actions=lambda enabled: False,
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
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

    def set_accepting_actions(enabled):
        gate_calls.append(enabled)
        gate["open"] = enabled
        return True

    def click_mouse_at(x, y):
        clicks.append((x, y))
        return gate["open"]

    manager = object.__new__(SimpleAffixRerollManager)
    manager.input_handler = SimpleNamespace(
        recover_transport=lambda: True,
        set_accepting_actions=set_accepting_actions,
        click_mouse_at=click_mouse_at,
    )
    manager.border_manager = SimpleNamespace(
        capture_screen_for_reroll=lambda region: object()
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        max_attempts=100,
        click_delay=5000,
        enchant_button_coord=(10, 20),
    )
    manager.status = SimpleAffixRerollStatus()
    manager.ocr_manager = BlockingOCR()
    manager._run_lock = threading.RLock()
    manager._run_generation = 0
    manager._active_run = None
    manager._gate_owner_generation = None
    manager._reroll_thread = None
    manager._screen_region = (0, 0, 10, 10)
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
        set_accepting_actions=lambda enabled: True,
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
        set_accepting_actions=lambda enabled: True,
    )
    manager.config = SimpleAffixRerollConfig(
        enabled=True,
        target_affixes=["测试"],
        enchant_button_coord=(10, 20),
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
