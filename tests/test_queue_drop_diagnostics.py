#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`MacroEngine._on_queue_drop` 的直接回归测试(Python 端队列丢弃诊断)。

AHK 侧的 queue_drop 载荷已由 test_ahk_queue_throughput 钉死为
`queue_drop:overload=N,expired=M`(均为**累计**计数);事件层
(ahk_input_handler.py 按第一个 ":" 切分)剥掉前缀后,handler 收到
"overload=N,expired=M"。此前只测了 AHK 侧的载荷格式,handler 本身的
差值计算、计数回退兜底和两类文案没有任何直接回归 —— 这里补上:

- 按与上次的**差值**报告本轮新增(AHK 端节流会把多轮丢弃合并进累计值);
- overload / expired 两种原因给**不同**的诊断建议(过载→调生产侧;
  过期→查长 delay / 优先级压制,与生产速率无关);
- 计数回退(AHK 重启后归零)不误报增量,兜底报累计值,后续增长恢复正常;
- 解析不了的载荷不抛异常,原文可见。

不需要 AutoHotkey,也不构造完整引擎:_on_queue_drop 只依赖
self._last_queue_drop_counts 与模块级 LOG_ERROR。用一个空对象承载状态、
**经由类调用真实方法**(被测的是实现本身,不是复制一份逻辑)。
"""

import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import torchlight_assistant.core.macro_engine as me
from torchlight_assistant.core.macro_engine import MacroEngine


def _drive(payloads, state=None):
    """依次投喂载荷,返回 (每轮产生的日志行列表, 状态对象)。"""
    state = state if state is not None else SimpleNamespace()
    lines = []
    with mock.patch.object(me, "LOG_ERROR", new=lines.append):
        for p in payloads:
            MacroEngine._on_queue_drop(state, p)
    return lines, state


def test_overload_growth_reports_delta_with_production_advice():
    """首次事件:差值基线是 (0, 0),overload=5 → 报 +5 与生产侧建议。"""
    lines, state = _drive(["overload=5,expired=0"])
    assert len(lines) == 1
    assert "过载丢弃 +5" in lines[0], f"缺过载增量: {lines[0]}"
    assert "入队速度超过" in lines[0], f"缺生产侧诊断建议: {lines[0]}"
    assert "过期丢弃" not in lines[0], f"expired 未增长却出了过期文案: {lines[0]}"
    assert state._last_queue_drop_counts == (5, 0)


def test_expired_growth_reports_delta_with_delay_advice_not_overload():
    """只有 expired 增长时必须给过期文案,**不得**混出过载建议 ——
    把"只是配了个长 delay"的用户引去调技能生产率正是拆分两个计数的动机。"""
    lines, _ = _drive(["overload=5,expired=0", "overload=5,expired=3"])
    assert len(lines) == 2
    assert "过期丢弃 +3" in lines[1], f"缺过期增量: {lines[1]}"
    assert "长 delay" in lines[1], f"缺过期诊断建议: {lines[1]}"
    assert "与生产速率无关" in lines[1]
    assert "过载丢弃" not in lines[1], f"overload 未增长却出了过载文案: {lines[1]}"


def test_both_causes_growing_reports_both_deltas():
    lines, _ = _drive(["overload=2,expired=1", "overload=10,expired=4"])
    assert "过载丢弃 +8" in lines[1], lines[1]
    assert "过期丢弃 +3" in lines[1], lines[1]


def test_counter_regression_falls_back_to_cumulative_then_recovers():
    """AHK 重启后累计计数归零:不得按 0-9 报负增量,也不得沉默
    (收到通知就说明确实发生过丢弃),兜底报累计值;
    且基线必须跟随回退 —— 重启后的首轮增长要能报出来。"""
    lines, state = _drive([
        "overload=9,expired=4",
        "overload=0,expired=0",   # AHK 重启,计数回卷
        "overload=2,expired=0",   # 重启后新发生的过载
    ])
    assert len(lines) == 3
    assert "累计 过载 0 / 过期 0" in lines[1], f"计数回退没有走累计兜底: {lines[1]}"
    assert "+" not in lines[1], f"计数回退报出了增量: {lines[1]}"
    assert "过载丢弃 +2" in lines[2], f"回退后的新增长没报出来: {lines[2]}"
    assert state._last_queue_drop_counts == (2, 0)


def test_unparseable_payload_is_visible_and_does_not_poison_state():
    """协议意外变化时不抛异常、原文可见,且不污染差值基线。"""
    lines, state = _drive(["borked-payload", "overload=1,expired=0"], state=SimpleNamespace())
    assert "AHK 报告丢弃了待发动作" in lines[0]
    assert "borked-payload" in lines[0], f"原始载荷不可见: {lines[0]}"
    assert "过载丢弃 +1" in lines[1], f"坏载荷污染了差值基线: {lines[1]}"


def test_payload_shape_pinned_on_ahk_side_parses_here():
    """与 AHK 侧钉死的线上格式互锁:test_ahk_queue_throughput 断言事件为
    `queue_drop:overload=N,expired=M`,事件层剥掉 `queue_drop:` 前缀 ——
    剩余部分必须能被本 handler 完整解析出两个计数。"""
    wire = "queue_drop:overload=485,expired=16"
    data = wire.split(":", 1)[1]      # ahk_input_handler.py:74 的切分方式
    lines, state = _drive([data])
    assert "过载丢弃 +485" in lines[0], lines[0]
    assert "过期丢弃 +16" in lines[0], lines[0]
    assert state._last_queue_drop_counts == (485, 16)


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
