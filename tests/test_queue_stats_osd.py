#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""队列观测的 Python 侧回归(P3)。

AHK 端每秒推送 `stats:e=..,h=..,n=..,l=..,p=..,d=..,x=..`(载荷格式与
"e/h/n/l 必须是实时深度"由 test_ahk_queue_throughput 钉死);事件层剥掉
前缀后以事件名 "stats" 发布。这里覆盖:

- format_queue_stats_line 的格式化契约(紧凑一行;丢弃计数零时不加噪音;
  坏载荷返回 "" 而不是抛异常);
- main_window 的真实订阅和 Qt 桥将 stats 投递到 OSD（隔离引擎）；
- 损坏且从未工作过的 get_stats() 请求路径已删除,不再有人把字符串当命令 ID 发。
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
    from PySide6.QtWidgets import QApplication, QMainWindow
    from torchlight_assistant.core.states import MacroState
    from torchlight_assistant.gui import main_window

    app = QApplication.instance() or QApplication([])
    subscribers = {}
    bus = SimpleNamespace(
        subscribe=lambda name, handler: subscribers.__setitem__(name, handler),
        unsubscribe=lambda name, handler: subscribers.pop(name, None),
    )

    class QueueWindow(main_window.GameSkillConfigUI):
        def __init__(self):
            QMainWindow.__init__(self)
            self.macro_engine = SimpleNamespace(get_current_state=lambda: MacroState.RUNNING)
            self.osd_status_window = SimpleNamespace(update_status=Mock())
            self._osd_state_name = "RUNNING"
            self._osd_base_text = "运行中"
            self._osd_base_color = "lime"
            self._connect_to_engine()

    with patch.object(main_window, "event_bus", bus):
        window = QueueWindow()
    try:
        subscribers["stats"](key="e=0,h=1,n=3,l=0,p=120,d=0,x=0")
        window.osd_status_window.update_status.assert_not_called()
        app.processEvents()
        window.osd_status_window.update_status.assert_called_once_with(
            "运行中\n队列 e0 h1 n3 l0", "lime"
        )
        window.osd_status_window.update_status.reset_mock()
        window._osd_state_name = "STOPPED"
        subscribers["stats"](key="e=0,h=0,n=0,l=0")
        app.processEvents()
        window.osd_status_window.update_status.assert_not_called()
    finally:
        window.close()


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
