#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AHK v2 作用域陷阱的静态护栏(AGENTS.md 4.3)。

规则:AHK v2 函数内**对一个变量赋值**会自动把它定为 local,除非函数顶部 `global` 声明。
后果是静默的:函数写进一个同名局部变量,全局状态纹丝不动。历史上 `ClearQueue` 就是
这样"清了队列却没清",PAUSED 看着停了实际残留旧动作;`CMD_PAUSE` 漏声明 `IsPaused`
让暂停闸门从来没生效过。

旧文档里的检查脚本只覆盖一张硬编码的函数名单。这里做全量检查:
扫出所有顶层全局,再逐个函数看有没有"赋值了但没声明"的。

注:`Map[key] := v` / `obj.prop := v` 是**读**该变量再改其内容,不需要声明,已排除。
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:
    pytest = None

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AHK_SCRIPT = os.path.join(REPO, "hold_server_extended.ahk")

# 顶层全局声明:行首 `global Name := ...`
_TOP_GLOBAL = re.compile(r"^global\s+([A-Za-z_]\w*)\s*:=")
# 函数头:行首 `Name(args) {`
_FUNC_HEAD = re.compile(r"^([A-Za-z_]\w*)\(.*\)\s*\{\s*$")
# 函数内 global 声明行
_GLOBAL_DECL = re.compile(r"^\s*global\s+(.+)$")
# 裸赋值(排除 X[...] := 和 X.y := ,那两种是读取后改内容)
_ASSIGN = re.compile(r"^\s*([A-Za-z_]\w*)\s*(:=|\+=|-=|\*=|/=|\.=)")
_INCDEC = re.compile(r"^\s*([A-Za-z_]\w*)\s*(\+\+|--)\s*$")


def _strip_comment(line):
    """去掉行尾注释。字符串里的分号很少见于本文件,按简单规则处理即可。"""
    out = []
    in_str = False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        elif ch == ";" and not in_str:
            break
        out.append(ch)
    return "".join(out)


def _parse(lines):
    """返回 [(函数名, 起始行号, 已声明的 global 集合, [(被赋值的名字, 行号)])]"""
    top_globals = set()
    for line in lines:
        m = _TOP_GLOBAL.match(line)
        if m:
            top_globals.add(m.group(1))

    funcs = []
    i = 0
    n = len(lines)
    while i < n:
        m = _FUNC_HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        depth = 0
        declared = set()
        assigns = []
        j = i
        while j < n:
            code = _strip_comment(lines[j])
            if j > i:
                dm = _GLOBAL_DECL.match(code)
                if dm:
                    for v in dm.group(1).split(","):
                        v = v.strip().split(":=")[0].strip()
                        if v:
                            declared.add(v)
                else:
                    am = _ASSIGN.match(code) or _INCDEC.match(code)
                    if am:
                        assigns.append((am.group(1), j + 1))
            depth += code.count("{") - code.count("}")
            if depth == 0 and j > i:
                break
            j += 1
        funcs.append((name, i + 1, declared, assigns))
        i = j + 1
    return top_globals, funcs


def test_every_global_assignment_is_declared():
    """任何函数里对顶层全局的赋值,都必须在该函数内有 global 声明。

    失败不是风格问题:漏声明会让这次赋值静默作用在局部变量上,全局状态不变 ——
    表现为"代码明明写了却没生效",极难从行为上定位。
    """
    assert os.path.isfile(AHK_SCRIPT), AHK_SCRIPT
    with open(AHK_SCRIPT, "r", encoding="utf-8") as fp:
        lines = fp.read().splitlines()

    top_globals, funcs = _parse(lines)
    assert len(top_globals) > 20, f"只解析出 {len(top_globals)} 个顶层全局,解析逻辑可能失效"
    assert len(funcs) > 30, f"只解析出 {len(funcs)} 个函数,解析逻辑可能失效"

    violations = []
    for name, head_line, declared, assigns in funcs:
        for var, line_no in assigns:
            if var in top_globals and var not in declared:
                violations.append(
                    f"  {AHK_SCRIPT}:{line_no}  函数 {name}()(第 {head_line} 行)"
                    f" 赋值全局 `{var}` 却未声明 global"
                )

    assert not violations, (
        "发现 AHK v2 作用域陷阱(赋值会写进同名局部变量,全局纹丝不动):\n"
        + "\n".join(violations)
    )


def test_parser_detects_a_synthetic_violation():
    """护栏自检:上面的检查必须真的能抓到问题,而不是永远绿。"""
    fake = [
        "global Counter := 0",
        "global Other := 1",
        "Bad() {",
        "    Counter := 5",
        "}",
        "Good() {",
        "    global Counter",
        "    Counter := 5",
        "}",
        "Indexed() {",
        "    Other[1] := 5",
        "}",
    ]
    top_globals, funcs = _parse(fake)
    assert top_globals == {"Counter", "Other"}
    by_name = {f[0]: f for f in funcs}

    bad = by_name["Bad"]
    assert any(v == "Counter" for v, _ in bad[3]) and "Counter" not in bad[2], (
        "未能识别出漏声明的赋值"
    )
    good = by_name["Good"]
    assert "Counter" in good[2], "误判了正确声明的函数"
    indexed = by_name["Indexed"]
    assert not [v for v, _ in indexed[3] if v == "Other"], (
        "把 Map[key] := v 误判成了裸赋值"
    )


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
