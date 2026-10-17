"""跨模式公共服务与迟到检测的行为回归；外部 IO 全部为替身。"""

import threading
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from torchlight_assistant.core.event_bus import event_bus
from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.resource_manager import ResourceManager
from torchlight_assistant.core.resource_scheduler import ResourceScheduler
from torchlight_assistant.core.runtime_context import ProductionContext
from torchlight_assistant.core.simple_affix_reroll_manager import (
    SimpleAffixRerollConfig, SimpleAffixRerollManager, SimpleAffixRerollStatus,
    _AffixRerollRun,
)
from torchlight_assistant.core.skill_manager import SkillManager
from torchlight_assistant.core.states import MacroState


class Input:
    def __init__(self):
        self.gate = False
        self.owner = ("none", 0)
        self.dry_run_mode = False
        self.sent = []
        self.sent_event = threading.Event()

    def __getattr__(self, name):
        return lambda *args, **kwargs: True

    def reset_runtime(self):
        self.gate = False
        self.owner = ("none", 0)
        return True

    def arm_main_mode(self):
        self.gate = False
        return True

    def set_runtime_owner(self, owner, epoch):
        self.owner = (owner, epoch)
        return True

    def set_accepting_actions(self, enabled, **kwargs):
        if enabled and self.owner != (kwargs.get("owner"), kwargs.get("epoch")):
            return False
        self.gate = enabled
        return True

    def set_dry_run_mode(self, enabled):
        self.dry_run_mode = enabled

    def execute_hp_potion(self, key):
        if not self.gate:
            return False
        self.sent.append(key)
        self.sent_event.set()
        return True

    execute_skill_normal = execute_hp_potion
    execute_skill_high = execute_hp_potion


class Border:
    def __init__(self):
        self.frame = np.zeros((20, 20, 4), dtype=np.uint8)
        self.running = False
        self.paused = False
        self.window_activation_config = {}

    def __getattr__(self, name):
        return lambda *args, **kwargs: True

    def get_current_frame(self):
        return self.frame

    def get_region_from_frame(self, frame, x, y, width, height):
        return frame[y:y + height, x:x + width]

    def capture_once_for_debug_and_cache(self, *args, **kwargs):
        return self.frame

    def start_capture_loop(self, **kwargs):
        self.running = True

    def stop(self):
        self.running = False


HP = {
    "enabled": True, "key": "1", "threshold": 60, "cooldown": 1000,
    "detection_mode": "text_ocr", "ocr_engine": "paddle",
    "text_x1": 0, "text_y1": 0, "text_x2": 5, "text_y2": 5,
}


@pytest.fixture
def runtime():
    border, inputs = Border(), Input()
    with mock.patch.object(ResourceManager, "_check_deepai_availability"):
        resources = ResourceManager(border, inputs)
    resources._update_tesseract_ocr_config = lambda config: None
    resources.update_config({"hp_config": dict(HP), "check_interval": 10})
    resources._ocr_number_box = {"hp": (0, 0, 5, 5)}
    engine = object.__new__(MacroEngine)
    engine._state = MacroState.STOPPED
    engine._state_lock = threading.RLock()
    engine._transition_lock = threading.RLock()
    engine._prepared_mode = "combat"
    engine._global_config = {"input_mode": "direct", "resource_management": {
        "hp_config": dict(HP), "check_interval": 10,
    }}
    engine._skills_config = {}
    engine._runtime_attempt_epoch = 0
    engine._runtime_start_epoch = 0
    engine._runtime_owner = "none"
    engine._runtime_owner_epoch = 0
    engine._ahk_transport_failed = False
    engine._stationary_mode_active = False
    engine._force_move_active = False
    engine.input_handler, engine.border_manager = inputs, border
    engine.resource_manager = resources
    engine.resource_scheduler = ResourceScheduler(resources)
    engine.resource_scheduler.scheduler.STOP_JOIN_TIMEOUT_SECONDS = 0.01
    engine.skill_manager = SkillManager(inputs, engine, border, resources)
    engine.skill_manager._apply_config_update({}, engine._global_config)
    engine.pathfinding_manager = SimpleNamespace(
        start=mock.Mock(return_value=True), stop=lambda: True, is_running=True,
    )
    engine.affix_reroll_manager = SimpleNamespace(status=SimpleNamespace(is_running=False))
    engine.sound_manager = SimpleNamespace(update_config=lambda config: None)
    engine._prepare_target_window_for_ready = lambda: True
    engine._register_secondary_hotkeys = lambda: True
    engine._publish_status_update = lambda: None
    engine._update_osd_visibility = lambda: None
    event_bus.subscribe("engine:config_updated", engine._on_config_updated)
    yield engine
    engine.resource_scheduler.cleanup()
    engine.skill_manager.cleanup()
    event_bus.unsubscribe("engine:config_updated", engine._on_config_updated)


def block_ocr(resources):
    entered, release = threading.Event(), threading.Event()

    def recognize(*args):
        entered.set()
        assert release.wait(3)
        return 1, 100, 1.0

    resources.paddle_ocr_manager = SimpleNamespace(recognize_and_parse=recognize)
    return entered, release


def test_old_ocr_cannot_send_disabled_new_key_after_stop_and_ready(runtime):
    engine = runtime
    resources = engine.resource_manager
    entered, release = block_ocr(resources)
    engine._state = MacroState.RUNNING
    engine.input_handler.gate = True
    assert engine.resource_scheduler.start()
    assert entered.wait(1)
    old_thread = engine.resource_scheduler.scheduler._scheduler_thread
    try:
        assert engine._set_state(MacroState.STOPPED)
        assert old_thread.is_alive()  # 本例确实越过 bounded join，不是顺序停好再测试。
        config = {"skills": {}, "global": {"input_mode": "direct", "resource_management": {
            "hp_config": dict(HP, enabled=False, key="q"), "check_interval": 10,
        }}}
        engine._handle_f8_press(config)
        assert engine._state is MacroState.READY
        assert engine.input_handler.gate is True
        assert old_thread.is_alive()
    finally:
        release.set()
        old_thread.join(1)
    assert not old_thread.is_alive()
    assert engine.input_handler.sent == []
    assert resources._flask_cooldowns == {}


@pytest.mark.parametrize("boundary", ["pause_resume", "config", "restart"])
def test_resource_detection_requires_current_production_token(runtime, boundary):
    resources = runtime.resource_manager
    entered, release = block_ocr(resources)
    runtime.input_handler.gate = True
    resources.start()
    worker = threading.Thread(target=resources.check_and_execute_resources)
    worker.start()
    assert entered.wait(1)
    try:
        if boundary == "pause_resume":
            resources.pause()
            resources.resume()
        elif boundary == "config":
            resources.update_config({"hp_config": dict(HP, key="q")})
        else:
            resources.stop()
            resources.start()
    finally:
        release.set()
        worker.join(1)
    assert not worker.is_alive()
    assert runtime.input_handler.sent == []


@pytest.mark.parametrize("mode", ["combat", "pathfinding"])
def test_resource_task_runs_in_both_main_modes(runtime, mode):
    engine = runtime
    engine._prepared_mode = mode
    engine.input_handler.gate = True
    engine.resource_manager.paddle_ocr_manager = SimpleNamespace(
        recognize_and_parse=lambda *args: (1, 100, 1.0)
    )
    engine._start_subsystems_based_on_mode()
    assert engine.input_handler.sent_event.wait(1)
    assert engine.resource_scheduler.is_running()
    assert "resource_checker" not in engine.skill_manager.unified_scheduler._tasks
    if mode == "pathfinding":
        assert engine.skill_manager._is_running is False
        engine.pathfinding_manager.start.assert_called_once()


def test_resource_scheduler_pause_resume_and_live_interval(runtime):
    service = runtime.resource_scheduler
    runtime.resource_manager.update_config({"check_interval": 50})
    assert service.start()
    service.pause()
    assert not service.is_running()
    assert runtime.resource_manager._runtime().capture() is None
    runtime.resource_manager.update_config({"check_interval": 75})
    service.update_interval()
    assert service.scheduler._tasks["resource_checker"].interval == .075
    assert service.resume()
    assert service.is_running()


def test_old_skill_condition_cannot_send_after_pause_resume(runtime):
    manager = runtime.skill_manager
    manager._skills_config = {"Skill": {
        "Enabled": True, "Key": "q", "TriggerMode": 0, "ExecuteCondition": 0,
        "Timer": 100000,
    }}
    runtime.input_handler.gate = True
    manager.start()
    entered, release = threading.Event(), threading.Event()

    def detect(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return True

    manager._check_execution_conditions = detect
    worker = threading.Thread(target=manager.execute_timed_skill, args=("Skill",))
    worker.start()
    assert entered.wait(1)
    try:
        manager.pause()
        assert manager.resume()
    finally:
        release.set()
        worker.join(1)
    assert runtime.input_handler.sent == []


def test_context_invalidates_results_for_every_boundary():
    context = ProductionContext()
    assert context.capture() is None
    context.start()
    for boundary in (context.reconfigure, context.pause, context.resume,
                     context.stop, context.start):
        old = context.capture()
        boundary()
        assert not context.accepts(old)


def test_production_tokens_cannot_cross_manager_instances():
    first, second = ProductionContext(), ProductionContext()
    first.start()
    second.start()
    assert first.accepts(first.capture())
    assert not second.accepts(first.capture())


def test_resource_service_refuses_restart_until_slow_worker_exits(runtime):
    service = runtime.resource_scheduler
    entered, release = block_ocr(runtime.resource_manager)
    runtime.input_handler.gate = True
    assert service.start()
    assert entered.wait(1)
    old_thread = service.scheduler._scheduler_thread
    try:
        service.stop()
        assert old_thread.is_alive()
        assert service.start() is False
        assert not runtime.resource_manager.is_running()
        assert runtime.resource_manager._runtime().capture() is None
    finally:
        release.set()
        old_thread.join(1)
    assert runtime.input_handler.sent == []
    assert service.start()
    assert runtime.input_handler.sent_event.wait(1)


def test_slow_resource_ocr_does_not_block_timed_skills(runtime):
    resources = runtime.resource_manager
    entered, release = block_ocr(resources)
    runtime.input_handler.gate = True
    assert runtime.resource_scheduler.start()
    assert entered.wait(1)
    manager = runtime.skill_manager
    manager._skills_config = {"Timed": {
        "Enabled": True, "Key": "q", "Timer": 10,
        "TriggerMode": 0, "ExecuteCondition": 0,
    }}
    try:
        assert manager.start()
        assert runtime.input_handler.sent_event.wait(1)
        assert "q" in runtime.input_handler.sent
        assert "1" not in runtime.input_handler.sent
    finally:
        resources.stop()
        release.set()


def test_paused_skill_results_do_not_repopulate_continuity_history(runtime):
    manager = runtime.skill_manager
    manager._skills_config = {"Conditional": {
        "Enabled": True, "Key": "q", "Timer": 100000,
        "TriggerMode": 0, "ExecuteCondition": 2, "ConditionColor": 0,
        "ConditionCoordX": 0, "ConditionCoordY": 0,
    }}
    runtime.input_handler.gate = True
    entered, release = threading.Event(), threading.Event()

    def detect(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return True

    runtime.border_manager.is_resource_sufficient = detect
    assert manager.start()
    worker = threading.Thread(target=manager.execute_timed_skill, args=("Conditional",))
    worker.start()
    assert entered.wait(1)
    try:
        manager.pause()
        assert manager.resume()
    finally:
        release.set()
        worker.join(1)
    assert manager._resource_condition_history == {}
    assert runtime.input_handler.sent == []


def test_resource_config_snapshot_is_not_mutated_by_caller(runtime):
    config = {"hp_config": dict(HP)}
    runtime.resource_manager.update_config(config)
    config["hp_config"]["key"] = "q"
    assert runtime.resource_manager.hp_config["key"] == "1"


def test_owner_stop_matches_affix_generation_and_never_restarts(runtime):
    engine = runtime
    manager = SimpleAffixRerollManager(engine.border_manager, engine.input_handler)
    engine.affix_reroll_manager = manager
    run = _AffixRerollRun(7, SimpleAffixRerollConfig(), SimpleAffixRerollStatus(is_running=True))
    manager._active_run = run
    manager.status = run.status
    manager._gate_owner_generation = 7
    try:
        with mock.patch.object(event_bus, "publish") as publish:
            engine._on_runtime_owner_stop("affix:6")
            assert manager.status.is_running
            engine._on_runtime_owner_stop("affix:7")
            assert not manager.status.is_running
            engine._on_runtime_owner_stop("affix:7")
            assert all(call.args[0] != "hotkey:f7_system_toggle" for call in publish.call_args_list)
    finally:
        manager.cleanup()


def test_owner_stop_matches_pathfinding_epoch(runtime):
    runtime._state = MacroState.RUNNING
    runtime._runtime_owner = "pathfinding"
    runtime._runtime_owner_epoch = 9
    runtime.stop_macro = mock.Mock()
    for value in ("pathfinding:8", "pathfinding:-9", "pathfinding:9:extra", "main:9"):
        runtime._on_runtime_owner_stop(value)
    runtime.stop_macro.assert_not_called()
    runtime._on_runtime_owner_stop("pathfinding:9")
    runtime.stop_macro.assert_called_once()


@pytest.mark.parametrize("boundary", ["none", "recovery", "sender_recovery", "new_run", "cleanup"])
def test_deferred_transport_failure_cannot_stop_recovered_or_new_run(runtime, boundary):
    from PySide6.QtCore import QTimer

    runtime._state = MacroState.RUNNING
    runtime._runtime_start_epoch = 3
    runtime.input_handler.command_sender = SimpleNamespace(transport_unavailable=True)
    runtime.sound_manager = None
    runtime.stop_macro = mock.Mock()
    with mock.patch.object(QTimer, "singleShot") as defer, mock.patch.object(
        event_bus, "publish"
    ), mock.patch("winsound.MessageBeep"):
        runtime._on_ahk_transport_failed("timeout")
    callback = defer.call_args.args[1]
    if boundary == "recovery":
        runtime._ahk_transport_failed = False
    elif boundary == "sender_recovery":
        runtime.input_handler.command_sender.transport_unavailable = False
    elif boundary == "new_run":
        runtime._runtime_start_epoch += 1
    elif boundary == "cleanup":
        runtime._cleanup_done = True
    callback()
    assert runtime.stop_macro.call_count == (1 if boundary == "none" else 0)


def test_transport_notices_cannot_override_current_sender_circuit(runtime):
    from PySide6.QtCore import QTimer

    sender = runtime.input_handler.command_sender = SimpleNamespace(
        transport_unavailable=False
    )
    runtime._ahk_transport_failed = False
    with mock.patch.object(QTimer, "singleShot") as defer:
        runtime._on_ahk_transport_failed("old timeout")
    assert not runtime._ahk_transport_failed
    defer.assert_not_called()

    sender.transport_unavailable = True
    runtime._ahk_transport_failed = True
    runtime._on_ahk_transport_recovered()
    assert runtime._ahk_transport_failed
    sender.transport_unavailable = False
    runtime._on_ahk_transport_recovered()
    assert not runtime._ahk_transport_failed
