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

    def set_macro_steps(self, steps):
        self.calls.append(("set_macro_steps", steps))
        return True

    def start_macro(self):
        self.calls.append(("start_macro",))
        return True

    def stop_macro(self):
        self.calls.append(("stop_macro",))
        return True

    def hold_key(self, key):
        self.calls.append(("hold", key))
        return True

    def release_key(self, key):
        self.calls.append(("release", key))
        return True

    def execute_skill_normal(self, key):
        self.calls.append(("normal", key))
        return True

    def execute_skill_high(self, key):
        self.calls.append(("high", key))
        return True


class FakeScheduler:
    def __init__(self, running=False):
        self.running = running
        self.paused = False
        self.stopped = False
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
    sm.unified_scheduler = FakeScheduler(running=scheduler_running)
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
    assert not _calls(sm, "hold")  # 技能按住配置不属于宏模式
    assert all(task[0] != "sequence_scheduler" for task in sm.unified_scheduler.added_tasks)
    assert any(task[0] == "resource_checker" for task in sm.unified_scheduler.added_tasks)


def test_pause_and_resume_stop_and_restart_ahk_macro():
    sm = _make_sm(sequence_enabled=True)
    sm._is_running = True

    sm.pause()
    sm.resume()

    assert ("stop_macro",) in sm.input_handler.calls
    assert _calls(sm, "start_macro")


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
