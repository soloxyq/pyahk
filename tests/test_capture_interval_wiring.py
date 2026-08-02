#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""捕获间隔接线回归(P1a)。

历史 BUG:GUI 的「图像捕获间隔」设置只作用于 READY 期的一次性捕获,
RUNNING 启动 start_capture_loop() 时不传 interval_ms,恒用默认 40ms ——
用户改配置无效且无任何提示。

修复口径:_capture_interval_ms() 是唯一读取口(10..1000ms 钳制,默认 40,
非法值 LOG_ERROR 可见),READY 与 RUNNING 两条路径都走它。

覆盖:钳制逻辑的边界(经由类调用真实方法);两条调用路径确实接上了钳制口径
(源码级钉死 —— 行为化验证需要真实 DXGI 捕获,不适合单测)。
"""

import os
import re
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as me
from torchlight_assistant.core.macro_engine import (
    MacroEngine,
    CAPTURE_INTERVAL_DEFAULT_MS,
    CAPTURE_INTERVAL_MAX_MS,
    CAPTURE_INTERVAL_MIN_MS,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(value_present, value=None):
    """构造只带 _global_config 的宿主,返回 (钳制结果, 警告行列表)。"""
    cfg = {"capture_interval": value} if value_present else {}
    state = SimpleNamespace(_global_config=cfg)
    lines = []
    with mock.patch.object(me, "LOG_ERROR", new=lines.append):
        result = MacroEngine._capture_interval_ms(state)
    return result, lines


def test_valid_values_pass_through_without_warning():
    for v in (10, 40, 250, 1000):
        result, lines = _read(True, v)
        assert result == v, f"{v} 被改成了 {result}"
        assert not lines, f"合法值 {v} 产生了警告: {lines}"


def test_missing_value_uses_default_silently():
    result, lines = _read(False)
    assert result == CAPTURE_INTERVAL_DEFAULT_MS == 40
    assert not lines, f"缺省值不该告警: {lines}"


def test_out_of_range_values_are_clamped_with_warning():
    result, lines = _read(True, 5)
    assert result == CAPTURE_INTERVAL_MIN_MS == 10
    assert lines and "钳制" in lines[0], f"钳低值没有可见警告: {lines}"

    result, lines = _read(True, 99999)
    assert result == CAPTURE_INTERVAL_MAX_MS == 1000
    assert lines and "钳制" in lines[0], f"钳高值没有可见警告: {lines}"


def test_non_numeric_value_falls_back_to_default_with_warning():
    for bad in ("abc", None, "40.5"):
        result, lines = _read(True, bad)
        assert result == CAPTURE_INTERVAL_DEFAULT_MS, f"{bad!r} → {result}"
        assert lines and "非法" in lines[0], f"非法值 {bad!r} 没有可见警告: {lines}"


def test_both_capture_paths_are_wired_to_the_clamped_reader():
    """源码级钉死:READY(capture_once_for_debug_and_cache)与
    RUNNING(start_capture_loop)都必须走 _capture_interval_ms(),
    并且不得再出现绕开钳制的裸 config.get("capture_interval") 读取。"""
    path = os.path.join(REPO, "torchlight_assistant", "core", "macro_engine.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(
        r"start_capture_loop\(\s*\n?\s*interval_ms=self\._capture_interval_ms\(\)",
        src,
    ), "RUNNING 路径 start_capture_loop 没有传入钳制后的捕获间隔(回归到恒 40ms)"
    assert re.search(
        r"capture_once_for_debug_and_cache\(\s*\n?\s*self\._capture_interval_ms\(\)",
        src,
    ), "READY 路径没有走统一的钳制读取口径"
    bare_reads = [
        ln
        for ln in src.splitlines()
        if re.search(r"""\.get\(\s*["']capture_interval["']""", ln)
        and "_capture_interval_ms" not in ln
        and "CAPTURE_INTERVAL_DEFAULT_MS" not in ln
    ]
    assert not bare_reads, f"发现绕开钳制的裸读取: {bare_reads}"


def test_gui_spinbox_range_matches_engine_clamp():
    """GUI 与引擎同口径:capture_interval 的 spinbox 必须限制到 10..1000。
    (实例化 widget 需要 QApplication,这里源码级钉死。)"""
    path = os.path.join(REPO, "torchlight_assistant", "gui", "basic_widgets.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(
        r'timing_spinboxes\["capture_interval"\]\.setRange\(10,\s*1000\)', src
    ), "GUI 的捕获间隔 spinbox 没有限制到 10..1000 范围"


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
