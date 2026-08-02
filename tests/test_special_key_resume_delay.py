#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""特殊键松开保护的 Python→AHK 配置接线回归。"""

import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as me
from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
from torchlight_assistant.core.macro_engine import MacroEngine


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class _InputRecorder:
    def __init__(self):
        self.batches = []

    def batch_update_config(self, config):
        self.batches.append(dict(config))
        return True


def _update(value_present, value=None):
    cfg = {"resource_management": {}}
    if value_present:
        cfg["special_key_resume_delay_ms"] = value
    recorder = _InputRecorder()
    state = SimpleNamespace(input_handler=recorder)
    lines = []
    with mock.patch.object(me, "LOG_ERROR", new=lines.append):
        MacroEngine._update_ahk_emergency_keys_cache(state, cfg)
    assert len(recorder.batches) == 1
    return recorder.batches[0]["special_key_resume_delay_ms"], lines


def test_resume_delay_is_always_sent_and_clamped():
    assert _update(False) == (0, [])
    assert _update(True, 50) == (50, [])

    value, lines = _update(True, -1)
    assert value == 0
    assert lines and "钳制" in lines[0]

    value, lines = _update(True, 5000)
    assert value == 1000
    assert lines and "钳制" in lines[0]

    value, lines = _update(True, "bad")
    assert value == 0
    assert lines and "非法" in lines[0]


def test_batch_sender_preserves_numeric_zero():
    calls = []

    def send(cmd, param):
        calls.append((cmd, param))
        return True

    state = SimpleNamespace(_send=send)
    assert AHKCommandSender.batch_update_config(
        state,
        {
            "special_key_resume_delay_ms": 0,
            "empty": "",
            "missing": None,
        },
    )
    assert len(calls) == 1
    assert calls[0][1] == "special_key_resume_delay_ms:0"


def test_ahk_parser_and_gui_range_are_wired():
    with open(os.path.join(REPO, "hold_server_extended.ahk"), encoding="utf-8") as fp:
        ahk = fp.read()
    assert 'case "special_key_resume_delay_ms":' in ahk
    assert "SpecialKeyResumeDelayMs := Min(Max(Integer(value), 0), 1000)" in ahk

    with open(
        os.path.join(REPO, "torchlight_assistant", "gui", "basic_widgets.py"),
        encoding="utf-8",
    ) as fp:
        gui = fp.read()
    assert (
        'timing_spinboxes["special_key_resume_delay"].setRange(0, 1000)' in gui
    )


def test_d4_druid_profile_enables_a_valid_guard():
    with open(os.path.join(REPO, "d4_druid.json"), encoding="utf-8") as fp:
        cfg = json.load(fp)
    delay_ms = cfg.get("global", {}).get("special_key_resume_delay_ms", 0)
    assert isinstance(delay_ms, int)
    assert 0 <= delay_ms <= 1000


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
