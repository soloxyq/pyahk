#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SkillManager 与 AHK 端宏解释器的集成边界单测。

Python 不再逐步执行 macro_steps;它只负责把步骤下发给 AHK,并在状态变化时
start/stop AHK 端宏循环。AHK 端负责顺序、循环、delay 与 held-key 释放。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:  # 允许无 pytest 独立运行
    pytest = None

try:
    from torchlight_assistant.core.skill_manager import SkillManager
    from torchlight_assistant.core.ahk_command_sender import AHKCommandSender

    _IMPORT_ERR = None
except Exception as e:  # 非 Windows / 缺依赖
    SkillManager = None
    AHKCommandSender = None
    _IMPORT_ERR = e

if pytest is not None:
    pytestmark = pytest.mark.skipif(
        SkillManager is None, reason=f"导入链不可用: {_IMPORT_ERR}"
    )


class FakeInput:
    """记录 SkillManager 发给 AHK 输入层的动作。"""

    def __init__(self):
        self.calls = []
        self.set_macro_steps_result = True
        self.start_macro_result = True
        self.stop_macro_result = True
        self.skill_hold_result = True

    def set_macro_steps(self, steps):
        self.calls.append(("set_macro_steps", steps))
        return self.set_macro_steps_result

    def start_macro(self):
        self.calls.append(("start_macro",))
        return self.start_macro_result

    def stop_macro(self):
        self.calls.append(("stop_macro",))
        return self.stop_macro_result

    def set_skill_hold_keys(self, keys):
        # 声明式:记录每次下发的**完整期望集合**(空列表=释放全部)
        self.calls.append(("skill_hold", list(keys)))
        return self.skill_hold_result

    def execute_skill_normal(self, key):
        self.calls.append(("normal", key))
        return True

    def execute_skill_high(self, key):
        self.calls.append(("high", key))
        return True


class FakeScheduler:
    def __init__(self, running=False, log=None):
        self.running = running
        self.paused = False
        self.stopped = False
        # 与 FakeInput 共享同一个有序事件表,才能断言"停调度线程"与"释放持键"的先后
        self.log = log if log is not None else []
        self.added_tasks = []
        self.removed_tasks = []
        self.updated = []

    def get_status(self):
        return {"running": self.running}

    def start(self):
        self.running = True
        return True

    def stop(self):
        self.running = False
        self.stopped = True
        self.log.append(("scheduler_stop",))
        return True

    def pause(self):
        self.paused = True
        return True

    def resume(self):
        self.paused = False
        self.running = True
        return True

    def clear_all_tasks(self):
        self.added_tasks.clear()

    def add_task(self, task_id, interval, callback, args=(), kwargs=None, start_immediately=False):
        self.added_tasks.append((task_id, interval, callback))
        return True

    def remove_task(self, task_id):
        self.removed_tasks.append(task_id)
        return True

    def update_task_interval(self, task_id, interval):
        self.updated.append((task_id, interval))
        return True


class FakeBorderManager:
    def __init__(self):
        self.prepared = False
        self.stopped = False

    def prepare_border(self, skills_config, resource_config):
        self.prepared = True

    def stop(self):
        self.stopped = True


class FakeResourceManager:
    def check_resources(self):
        return None


_STEPS = [
    {"type": "down", "key": "RButton"},
    {"type": "down", "key": "1"},
    {"type": "up", "key": "1"},
    {"type": "delay", "ms": 50},
    {"type": "up", "key": "RButton"},
]


def _make_sm(sequence_enabled=True, steps=None, scheduler_running=False):
    sm = SkillManager(
        FakeInput(),
        None,
        FakeBorderManager(),
        resource_manager=FakeResourceManager(),
    )
    sm.unified_scheduler = FakeScheduler(
        running=scheduler_running, log=sm.input_handler.calls
    )
    sm._global_config = {
        "sequence_enabled": sequence_enabled,
        "macro_steps": steps if steps is not None else list(_STEPS),
        "resource_management": {"check_interval": 200},
    }
    return sm


def _calls(sm, name):
    return [c for c in sm.input_handler.calls if c[0] == name]


def test_start_macro_mode_delegates_steps_to_ahk():
    sm = _make_sm(sequence_enabled=True)
    sm.start()

    assert _calls(sm, "set_macro_steps")[-1][1] == _STEPS
    assert _calls(sm, "start_macro")
    assert not _calls(sm, "skill_hold")  # 技能按住配置不属于宏模式
    assert all(task[0] != "sequence_scheduler" for task in sm.unified_scheduler.added_tasks)
    assert any(task[0] == "resource_checker" for task in sm.unified_scheduler.added_tasks)


def test_macro_start_failure_does_not_publish_local_running_state():
    sm = _make_sm(sequence_enabled=True)
    sm.input_handler.start_macro_result = False

    assert sm.start() is False
    assert sm._is_running is False
    assert sm.unified_scheduler.running is False


def test_hold_sync_failure_does_not_start_python_producers():
    sm = _make_sm(sequence_enabled=False)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm.input_handler.skill_hold_result = False

    assert sm.start() is False
    assert sm._is_running is False
    assert sm.unified_scheduler.running is False


def test_pause_and_resume_stop_and_restart_ahk_macro():
    sm = _make_sm(sequence_enabled=True)
    sm._is_running = True

    sm.pause()
    sm.resume()

    assert ("stop_macro",) in sm.input_handler.calls
    assert _calls(sm, "start_macro")


def test_resume_keeps_scheduler_paused_when_ahk_restore_fails():
    sm = _make_sm(sequence_enabled=True)
    assert sm.start() is True
    sm.pause()
    sm.input_handler.start_macro_result = False

    assert sm.resume() is False
    assert sm._is_paused is True
    assert sm.unified_scheduler.paused is True


def test_resume_compensates_ahk_macro_when_scheduler_resume_fails():
    sm = _make_sm(sequence_enabled=True)
    assert sm.start() is True
    sm.pause()
    sm.input_handler.calls.clear()
    sm.unified_scheduler.resume = lambda: False

    assert sm.resume() is False
    assert _calls(sm, "start_macro")
    assert _calls(sm, "stop_macro")
    assert sm.input_handler.calls.index(("start_macro",)) < sm.input_handler.calls.index(
        ("stop_macro",)
    )
    assert sm._is_paused is True


def test_stop_macro_mode_stops_ahk_macro():
    sm = _make_sm(sequence_enabled=True)
    sm._is_running = True

    sm.stop()

    assert ("stop_macro",) in sm.input_handler.calls
    assert sm.border_frame_manager.stopped is True


def test_update_macro_steps_restarts_running_ahk_macro():
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._is_running = True
    new_steps = [{"type": "press", "key": "2"}]

    sm.update_global_config(
        {
            "sequence_enabled": True,
            "macro_steps": new_steps,
            "resource_management": {"check_interval": 200},
        }
    )

    assert ("stop_macro",) in sm.input_handler.calls
    assert ("set_macro_steps", new_steps) in sm.input_handler.calls
    assert ("start_macro",) in sm.input_handler.calls


def test_live_macro_step_sync_failure_stops_python_producers():
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._is_running = True
    sm.input_handler.set_macro_steps_result = False

    result = sm.update_global_config(
        {
            "sequence_enabled": True,
            "macro_steps": [{"type": "press", "key": "2"}],
            "resource_management": {"check_interval": 200},
        }
    )

    assert result is False
    assert sm._is_running is False
    assert sm.unified_scheduler.stopped is True
    assert not _calls(sm, "start_macro")


def test_live_macro_start_failure_stops_python_producers():
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._is_running = True
    sm.input_handler.start_macro_result = False

    result = sm.update_global_config(
        {
            "sequence_enabled": True,
            "macro_steps": [{"type": "press", "key": "2"}],
            "resource_management": {"check_interval": 200},
        }
    )

    assert result is False
    assert sm._is_running is False
    assert sm.unified_scheduler.stopped is True


def test_live_skill_to_macro_release_failure_does_not_start_macro():
    sm = _make_sm(sequence_enabled=False, scheduler_running=True)
    sm._is_running = True
    sm.input_handler.skill_hold_result = False

    result = sm.update_global_config(
        {
            "sequence_enabled": True,
            "macro_steps": [{"type": "press", "key": "2"}],
            "resource_management": {"check_interval": 200},
        }
    )

    assert result is False
    assert sm._is_running is False
    assert sm.unified_scheduler.stopped is True
    assert not _calls(sm, "set_macro_steps")
    assert not _calls(sm, "start_macro")


def test_live_macro_to_skill_hold_failure_does_not_rebuild_tasks():
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True
    sm.input_handler.skill_hold_result = False

    result = sm.update_global_config(
        {
            "sequence_enabled": False,
            "macro_steps": [],
            "resource_management": {"check_interval": 200},
        }
    )

    assert result is False
    assert sm._is_running is False
    assert sm.unified_scheduler.stopped is True
    assert sm.unified_scheduler.added_tasks == []


def test_switch_macro_to_skill_stops_ahk_macro_and_rebuilds_scheduler():
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._is_running = True

    sm.update_global_config(
        {
            "sequence_enabled": False,
            "macro_steps": [],
            "resource_management": {"check_interval": 200},
        }
    )

    assert ("stop_macro",) in sm.input_handler.calls
    assert all(task[0] != "sequence_scheduler" for task in sm.unified_scheduler.added_tasks)
    assert any(task[0] == "cooldown_checker" for task in sm.unified_scheduler.added_tasks)


def test_boss_only_skips_until_boss_mode_enabled():
    sm = _make_sm(sequence_enabled=False)
    skill = {
        "Enabled": True,
        "TriggerMode": 0,
        "Key": "R",
        "BossOnly": True,
        "Priority": False,
        "ExecuteCondition": 0,
    }

    sm._try_execute_skill("Ultimate", skill, object())
    assert not _calls(sm, "normal")

    sm.set_boss_mode_active(True)
    sm._try_execute_skill("Ultimate", skill, object())

    assert ("normal", "R") in sm.input_handler.calls


def test_string_false_priority_does_not_escalate_skill_queue():
    sm = _make_sm(sequence_enabled=False)
    skill = {
        "Enabled": True,
        "TriggerMode": 0,
        "Key": "R",
        "Priority": "false",
        "ExecuteCondition": 0,
    }

    sm._try_execute_skill("Normal", skill, object())

    assert ("normal", "R") in sm.input_handler.calls
    assert ("high", "R") not in sm.input_handler.calls


_HOLD_SKILLS = {
    # 键盘持键在配置里排在鼠标键之前,验证下发时被重排为"鼠标键优先"
    "Skill1": {"Enabled": True, "TriggerMode": 2, "Key": "q"},
    "Skill2": {"Enabled": True, "TriggerMode": 2, "Key": "RButton"},
    "Skill3": {"Enabled": False, "TriggerMode": 2, "Key": "e"},  # 未启用不应下发
    "Skill4": {"Enabled": True, "TriggerMode": 0, "Key": "1"},   # 非按住模式不应下发
}


def _hold_sets(sm):
    return [c[1] for c in sm.input_handler.calls if c[0] == "skill_hold"]


def test_skill_mode_start_declares_full_hold_set_mouse_first():
    """技能模式 start:声明完整期望集合,鼠标键排在键盘键之前。"""
    sm = _make_sm(sequence_enabled=False)
    sm._skills_config = dict(_HOLD_SKILLS)

    sm.start()

    assert _hold_sets(sm)[-1] == ["RButton", "q"]


def test_pause_declares_empty_set_and_resume_redeclares_full():
    """pause 声明空集合(释放全部);resume 重新声明完整集合。"""
    sm = _make_sm(sequence_enabled=False)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.pause()
    assert _hold_sets(sm)[-1] == []

    sm.resume()
    assert _hold_sets(sm)[-1] == ["RButton", "q"]


def test_stop_declares_empty_set_after_scheduler_stopped():
    """stop 必须先停调度线程再释放持键(否则在飞回调会在释放后又入队)。"""
    sm = _make_sm(sequence_enabled=False, scheduler_running=True)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.stop()

    assert _hold_sets(sm)[-1] == []
    assert sm.unified_scheduler.stopped is True

    # 真正断言顺序:调度线程必须在"声明空持键集合"之前就停掉
    names = [c[0] for c in sm.input_handler.calls]
    stop_idx = names.index("scheduler_stop")
    release_idx = max(
        i
        for i, c in enumerate(sm.input_handler.calls)
        if c[0] == "skill_hold" and c[1] == []
    )
    assert stop_idx < release_idx, sm.input_handler.calls


def test_stop_can_skip_redundant_ahk_cleanup_after_atomic_runtime_reset():
    """上层原子 reset 后只停 Python 生产者，不再叠加跨进程清理超时。"""
    sm = _make_sm(sequence_enabled=False, scheduler_running=True)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.stop(cleanup_input=False)

    assert sm.unified_scheduler.stopped is True
    assert _hold_sets(sm) == []
    assert not _calls(sm, "stop_macro")


def test_switch_skill_to_macro_releases_hold_keys():
    """技能→宏:必须先释放技能持键,否则持键在宏模式下永久卡住。"""
    sm = _make_sm(sequence_enabled=False, scheduler_running=True)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.update_global_config(
        {
            "sequence_enabled": True,
            "macro_steps": [{"type": "press", "key": "2"}],
            "resource_management": {"check_interval": 200},
        }
    )

    assert [] in _hold_sets(sm)
    assert ("start_macro",) in sm.input_handler.calls


def test_switch_macro_to_skill_redeclares_hold_keys():
    """宏→技能:补声明完整期望集合(宏模式期间从未持有技能持键)。"""
    sm = _make_sm(sequence_enabled=True, scheduler_running=True)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.update_global_config(
        {
            "sequence_enabled": False,
            "macro_steps": [],
            "resource_management": {"check_interval": 200},
        }
    )

    assert _hold_sets(sm)[-1] == ["RButton", "q"]


def test_emergency_stop_releases_hold_keys():
    """紧急停止也必须释放技能持键,否则键悬空。"""
    sm = _make_sm(sequence_enabled=False)
    sm._skills_config = dict(_HOLD_SKILLS)
    sm._is_running = True

    sm.emergency_stop()

    assert _hold_sets(sm)[-1] == []


def test_hold_set_serialized_as_line_protocol():
    """期望持键用换行分隔的行协议下发(顺序即按下顺序)。"""
    assert AHKCommandSender.serialize_skill_hold_keys(["RButton", "q"]) == "RButton\nq"
    assert AHKCommandSender.serialize_skill_hold_keys([]) == ""
    assert AHKCommandSender.serialize_skill_hold_keys(None) == ""


def test_serializer_uses_ahk_line_protocol():
    assert AHKCommandSender.serialize_macro_steps(_STEPS) == (
        "down:RButton\n"
        "down:1\n"
        "up:1\n"
        "delay:50\n"
        "up:RButton"
    )


if __name__ == "__main__":
    if SkillManager is None:
        print("SKIP: 导入链不可用:", _IMPORT_ERR)
        sys.exit(0)
    fns = sorted(
        (n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)
    )
    for name, fn in fns:
        fn()
        print("PASS", name)
    print(f"ALL {len(fns)} PASSED")
