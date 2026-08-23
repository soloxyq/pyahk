#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用宏:key_names 迁移/序列化/归一化的纯逻辑单测(不依赖 Windows/GUI)。

直接按文件路径加载 key_names,绕过 torchlight_assistant 包 __init__(其会 import
hold_client/WinDLL,在非 Windows 上不可用),以便核心逻辑测试可在任意平台运行。
"""

import importlib.util
import json
import os

_KN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "torchlight_assistant", "utils", "key_names.py"
)
_spec = importlib.util.spec_from_file_location("key_names_under_test", _KN_PATH)
kn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kn)


def test_migrate_basic():
    assert kn.migrate_skill_sequence_to_steps("1,delay50,RButton") == [
        {"type": "press", "key": "1"},
        {"type": "delay", "ms": 50},
        {"type": "press", "key": "RButton"},
    ]


def test_migrate_normalizes_mouse_alias():
    steps = kn.migrate_skill_sequence_to_steps("right_mouse,delay20")
    assert steps == [
        {"type": "press", "key": "RButton"},
        {"type": "delay", "ms": 20},
    ]


def test_migrate_malformed_delay_is_press():
    # 畸形 delay token(无数字)按普通键处理(与旧 _parse_sequence_delay 一致)
    assert kn.migrate_skill_sequence_to_steps("delayx,2") == [
        {"type": "press", "key": "delayx"},
        {"type": "press", "key": "2"},
    ]


def test_legacy_roundtrip_press_delay():
    steps = [
        {"type": "press", "key": "1"},
        {"type": "delay", "ms": 50},
        {"type": "press", "key": "RButton"},
    ]
    assert kn.steps_to_legacy_sequence(steps) == "1,delay50,RButton"


def test_legacy_empty_when_down_up_present():
    steps = [{"type": "down", "key": "RButton"}, {"type": "up", "key": "RButton"}]
    assert kn.steps_to_legacy_sequence(steps) == ""


def test_normalize_drops_and_clamps():
    raw = [
        {"type": "down", "key": "right_mouse"},  # 别名 → RButton
        {"type": "delay", "ms": -5},             # 负数 → 0
        {"type": "press"},                       # 缺 key → 丢弃
        {"type": "bogus", "key": "x"},           # 未知类型 → 丢弃
        {"type": "up", "key": "1"},
        "notadict",                              # 非 dict → 丢弃
    ]
    assert kn.normalize_macro_steps(raw) == [
        {"type": "down", "key": "RButton"},
        {"type": "delay", "ms": 0},
        {"type": "up", "key": "1"},
    ]


def test_config_migrate_when_absent():
    cfg = {"global": {"skill_sequence": "1,delay50,Rbutton"}}
    kn.normalize_config_keys(cfg)
    assert cfg["global"]["macro_steps"] == [
        {"type": "press", "key": "1"},
        {"type": "delay", "ms": 50},
        {"type": "press", "key": "RButton"},
    ]


def test_config_respects_explicit_empty():
    # 用户显式清空成 [] 时不得从旧 skill_sequence 回填
    cfg = {"global": {"skill_sequence": "1,2,3", "macro_steps": []}}
    kn.normalize_config_keys(cfg)
    assert cfg["global"]["macro_steps"] == []


def test_config_normalizes_boss_mode_fields():
    cfg = {
        "skills": {"Skill1": {"Key": "r", "BossOnly": 1}},
        "global": {"boss_mode_hotkey": "xbutton1"},
    }
    kn.normalize_config_keys(cfg)
    assert cfg["skills"]["Skill1"]["BossOnly"] is True
    assert cfg["global"]["boss_mode_hotkey"] == "XButton1"


def test_config_disables_boss_only_for_hold_mode():
    cfg = {"skills": {"Skill1": {"TriggerMode": 2, "BossOnly": True}}}
    kn.normalize_config_keys(cfg)
    assert cfg["skills"]["Skill1"]["BossOnly"] is False


def test_normalize_side_mouse_button_aliases():
    assert kn.normalize_key_name("x1") == "XButton1"
    assert kn.normalize_key_name("button8") == "XButton1"
    assert kn.normalize_key_name("x2") == "XButton2"
    assert kn.normalize_key_name("button9") == "XButton2"


def test_d4_druid_sequence_uses_explicit_delays_for_cadence():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "d4_druid.json"))
    with open(path, encoding="utf-8") as fp:
        global_config = json.load(fp)["global"]

    steps = global_config["macro_steps"]
    assert steps and len(steps) % 2 == 0
    for index in range(0, len(steps), 2):
        assert steps[index]["type"] == "press"
        assert steps[index + 1]["type"] == "delay"
        assert int(steps[index + 1]["ms"]) > 0
    assert kn.steps_to_legacy_sequence(steps) == global_config["skill_sequence"]


if __name__ == "__main__":
    fns = sorted(
        (n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)
    )
    for name, fn in fns:
        fn()
        print("PASS", name)
    print(f"ALL {len(fns)} PASSED")
