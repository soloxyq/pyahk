#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""队列观测的 Python 侧回归(P3)。

AHK 端每秒推送 `stats:e=..,h=..,n=..,l=..,p=..,d=..,x=..`(载荷格式与
"e/h/n/l 必须是实时深度"由 test_ahk_queue_throughput 钉死);事件层剥掉
前缀后以事件名 "stats" 发布。这里覆盖:

- format_queue_stats_line 的格式化契约(紧凑一行;丢弃计数零时不加噪音;
  坏载荷返回 "" 而不是抛异常);
- main_window 确实订阅了 "stats" 并把队列行挂到 OSD(源码级钉死 ——
  实例化 MainWindow 需要 QApplication 与完整引擎);
- 损坏且从未工作过的 get_stats() 请求路径已删除,不再有人把字符串当命令 ID 发。
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from torchlight_assistant.gui.status_window import format_queue_stats_line

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_line_shows_realtime_depths():
    line = format_queue_stats_line("e=0,h=1,n=3,l=0,p=120,d=0,x=0")
    assert line == "队列 e0 h1 n3 l0", f"格式不对: {line!r}"


def test_drop_counts_appear_only_when_nonzero():
    """常态(零丢弃)不加噪音;有丢弃时必须可见,且过载/过期分开(与
    queue_drop 诊断同口径,用户看到 OSD 数字就知道去查哪一类原因)。"""
    quiet = format_queue_stats_line("e=0,h=0,n=0,l=0,p=9,d=0,x=0")
    assert "丢弃" not in quiet, f"零丢弃时不该展示丢弃计数: {quiet!r}"

    noisy = format_queue_stats_line("e=0,h=2,n=5,l=1,p=300,d=12,x=3")
    assert "过载12" in noisy and "过期3" in noisy, f"丢弃计数缺失或未分类: {noisy!r}"


def test_malformed_payload_returns_empty_not_crash():
    for bad in ("", "garbage", "e=x,h=1,n=1,l=1", "p=1,d=2,x=3"):
        line = format_queue_stats_line(bad)
        assert line == "", f"坏载荷 {bad!r} 应返回空串,得到 {line!r}"


def test_missing_cumulative_fields_default_to_zero():
    """向后兼容:载荷缺 d/x 时按 0 处理(深度仍展示),不因协议演进而消失。"""
    line = format_queue_stats_line("e=0,h=1,n=0,l=0,p=5")
    assert line == "队列 e0 h1 n0 l0", f"缺 d/x 时应按零丢弃处理: {line!r}"


def test_main_window_subscribes_stats_and_mounts_queue_line():
    path = os.path.join(REPO, "torchlight_assistant", "gui", "main_window.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    assert re.search(r'event_bus\.subscribe\(\s*"stats"', src), (
        "main_window 没有订阅 stats 事件 —— AHK 的每秒推送无人消费"
    )
    assert "format_queue_stats_line" in src, "OSD 队列行没有使用统一的格式化函数"
    m = re.search(r"def _on_queue_stats\b(.*?)\n    def ", src, re.S)
    assert m, "未找到 _on_queue_stats 处理器"
    assert '"RUNNING", "PAUSED"' in m.group(1), (
        "队列行没有限制在 RUNNING/PAUSED 展示"
    )


def test_broken_get_stats_request_path_is_gone():
    path = os.path.join(REPO, "torchlight_assistant", "core", "ahk_command_sender.py")
    with open(path, encoding="utf-8") as fp:
        src = fp.read()
    assert "def get_stats" not in src, (
        "get_stats() 请求路径又回来了 —— 它把字符串当命令 ID 发送,从未工作过;"
        "统计是 AHK 端每秒主动推送的,不需要请求接口"
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
