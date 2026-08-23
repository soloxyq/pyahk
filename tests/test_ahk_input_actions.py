#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHKInputHandler 动作分发与坐标点击协议回归。"""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
from torchlight_assistant.core.ahk_input_handler import AHKInputHandler


def _handler(*, dry_run=False, drop=False, sender=None, actions=None):
    state = object.__new__(AHKInputHandler)
    state.dry_run_mode = dry_run
    state._drop_non_emergency = drop
    state.ahk_process = None
    state._signal_connected = False
    state.command_sender = sender or mock.Mock()
    state.debug_display_manager = SimpleNamespace(
        add_action=(actions if actions is not None else []).append
    )
    state._check_send = lambda ok: ok
    return state


def test_dry_run_records_all_skill_and_potion_actions_without_sending():
    sender = mock.Mock()
    actions = []
    state = _handler(dry_run=True, sender=sender, actions=actions)

    assert state.execute_skill_normal("1") is True
    assert state.execute_skill_high("2,delay50,3") is True
    assert state.execute_utility("4") is True
    assert state.execute_hp_potion("q") is True
    assert state.execute_mp_potion("w") is True
    assert state.click_mouse_at(100, 200, hold_time=75) is True

    assert sender.mock_calls == [], "dry-run 期间仍调用了真实 sender"
    assert actions == [
        "SkillNormal:1",
        "SkillHigh:2,delay50,3",
        "Utility:4",
        "HPPotion:q",
        "MPPotion:w",
        "MouseAt:100,200,75",
    ]


def test_special_suppression_blocks_skills_but_not_emergency_potions():
    sender = mock.Mock()
    sender.send_emergency.return_value = True
    state = _handler(drop=True, sender=sender)

    assert state.execute_skill_normal("1") is False
    assert state.execute_utility("2") is False
    assert state.execute_hp_potion("q") is True
    sender.send_emergency.assert_called_once_with("q")
    sender.send_normal.assert_not_called()
    sender.send_low_priority.assert_not_called()


def test_mouse_click_at_serializes_one_compatible_enqueue_action():
    state = object.__new__(AHKCommandSender)
    state.enqueue = mock.Mock(return_value=True)

    assert state.send_mouse_click_at(-10, 20, 125, priority=2) is True
    state.enqueue.assert_called_once_with(
        "mouse_click_at:-10,20,125", 2
    )


def test_mouse_click_at_validates_coordinates_and_clamps_hold_duration():
    sender = mock.Mock()
    sender.send_mouse_click_at.return_value = True
    state = _handler(sender=sender)

    assert state.click_mouse_at("10", 20, hold_time=-3) is True
    assert state.click_mouse_at(30, 40, hold_time=9000) is True
    assert state.click_mouse_at("bad", 20) is False
    assert state.click_mouse_at(2 ** 40, 20) is False
    assert sender.send_mouse_click_at.call_args_list == [
        mock.call(10, 20, 0, priority=2),
        mock.call(30, 40, 5000, priority=2),
    ]


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
