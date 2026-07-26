#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Python 与 AHK 两侧命令表必须严格一致。

两侧各维护一份命令 ID(torchlight_assistant/config/ahk_commands.py 与
ahk_commands.ahk),任何一侧漏改都会让命令被静默丢给错误的 case,
症状极难定位(按键不发/持键不释放/热键不注册,却没有任何报错)。
这里把"人工比对"变成自动断言。
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from torchlight_assistant.config import ahk_commands as PY

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AHK_TABLE = os.path.join(REPO, "ahk_commands.ahk")
AHK_SERVER = os.path.join(REPO, "hold_server_extended.ahk")


def _python_table():
    return {
        name: value
        for name, value in vars(PY).items()
        if name.startswith("CMD_") and isinstance(value, int)
    }


def _ahk_table():
    with open(AHK_TABLE, encoding="utf-8") as fp:
        text = fp.read()
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^global\s+(CMD_\w+)\s*:=\s*(\d+)", text, re.M)
    }


def test_command_ids_match_on_both_sides():
    py, ahk = _python_table(), _ahk_table()
    assert py == ahk, (
        "命令表不一致:\n"
        f"  仅 Python 有: {sorted(set(py) - set(ahk))}\n"
        f"  仅 AHK 有:    {sorted(set(ahk) - set(py))}\n"
        f"  ID 不同:      "
        f"{ {k: (py[k], ahk[k]) for k in set(py) & set(ahk) if py[k] != ahk[k]} }"
    )


def test_command_ids_are_unique():
    for label, table in (("Python", _python_table()), ("AHK", _ahk_table())):
        seen = {}
        for name, value in table.items():
            assert value not in seen, f"{label} 侧 ID {value} 重复: {seen[value]} / {name}"
            seen[value] = name


def test_every_command_name_is_registered_in_cmd_names():
    py = _python_table()
    missing = [n for n, v in py.items() if v not in PY.CMD_NAMES]
    assert not missing, f"CMD_NAMES 缺少条目(调试日志会显示 UNKNOWN): {missing}"


def test_server_handles_every_declared_command():
    """每个命令 ID 都应在服务端有 case 分支,否则命令会被静默忽略。"""
    with open(AHK_SERVER, encoding="utf-8") as fp:
        server = fp.read()
    handled = set(re.findall(r"case\s+(CMD_\w+)\s*:", server))
    # 已定义但服务端未实现的命令(历史遗留),显式登记为豁免,避免默默变成死协议
    known_unimplemented = {"CMD_SEND_KEY", "CMD_SEND_SEQUENCE"}
    declared = set(_python_table())
    unhandled = declared - handled - known_unimplemented
    assert not unhandled, f"服务端缺少 case 分支(命令会被静默忽略): {sorted(unhandled)}"
    stale = known_unimplemented & handled
    assert not stale, f"以下命令已实现,请从 known_unimplemented 中移除: {sorted(stale)}"


if __name__ == "__main__":
    fns = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    for name, fn in fns:
        fn()
        print("PASS", name)
    print(f"ALL {len(fns)} PASSED")
