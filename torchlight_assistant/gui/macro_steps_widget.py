#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用宏(雷蛇式)步骤编辑器 —— 列表 + 增删 + 上下移 + 类型联动。

步骤模型:{"type": "down"|"up"|"press"|"delay", "key"?: str, "ms"?: int}
- 按下(down)/弹起(up)/单击(press): 需要按键(键盘键或鼠标键 LButton/RButton/MButton/XButton1/XButton2)
- 延时(delay): 需要毫秒,不发键
「按住一段时间」用 按下 → 延时 → 弹起 组合表达;按下/弹起可跨步骤保持按住。
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QLineEdit,
    QSpinBox,
)
from PySide6.QtCore import Qt
from typing import Any, Dict, List

from .key_capture import KeyCaptureMixin, PYNPUT_AVAILABLE
from ..utils.key_names import normalize_key_name, normalize_macro_steps

# (type 值, 显示名, 图标)
_STEP_TYPES = [
    ("down", "按下", "⬇"),
    ("up", "弹起", "⬆"),
    ("press", "单击", "▶"),
    ("delay", "延时", "⏱"),
]
_TYPE_LABEL = {t: f"{icon} {name}" for t, name, icon in _STEP_TYPES}


class MacroStepsEditor(KeyCaptureMixin, QWidget):
    """编辑通过 get_steps()/set_steps() 与外部交换的宏步骤列表。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        hint = QLabel(
            "宏按步骤列表循环执行(进入战斗 RUNNING 后)。按下/弹起可跨步骤保持按住;"
            "「按住一段时间」= 按下 → 延时 → 弹起。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;")
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(6)

        # 左:步骤列表
        self.steps_list = QListWidget()
        self.steps_list.setMinimumWidth(220)
        body.addWidget(self.steps_list, 3)

        # 右:编辑区
        side = QVBoxLayout()
        side.setSpacing(4)

        row_type = QHBoxLayout()
        row_type.addWidget(QLabel("类型:"))
        self.type_combo = QComboBox()
        for t, name, icon in _STEP_TYPES:
            self.type_combo.addItem(f"{icon} {name}", t)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        row_type.addWidget(self.type_combo, 1)
        side.addLayout(row_type)

        row_key = QHBoxLayout()
        row_key.addWidget(QLabel("按键:"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("如 1 / RButton / XButton1 / space")
        row_key.addWidget(self.key_input, 1)
        self.listen_btn = QPushButton("🎧")
        self.listen_btn.setMaximumWidth(36)
        if PYNPUT_AVAILABLE:
            self.listen_btn.setToolTip("点击后按下要录制的键(键盘或鼠标)")
        else:
            self.listen_btn.setToolTip("pynput 不可用,请手动输入键名")
        self.listen_btn.clicked.connect(self._on_listen)
        row_key.addWidget(self.listen_btn)
        side.addLayout(row_key)

        row_ms = QHBoxLayout()
        row_ms.addWidget(QLabel("时长:"))
        self.ms_input = QSpinBox()
        self.ms_input.setRange(0, 600000)
        self.ms_input.setValue(50)
        self.ms_input.setSuffix(" ms")
        row_ms.addWidget(self.ms_input, 1)
        side.addLayout(row_ms)

        self.add_btn = QPushButton("➕ 添加步骤")
        self.add_btn.clicked.connect(self._on_add)
        side.addWidget(self.add_btn)

        ops = QHBoxLayout()
        self.up_btn = QPushButton("⬆ 上移")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = QPushButton("⬇ 下移")
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.del_btn = QPushButton("🗑 删除")
        self.del_btn.clicked.connect(self._on_delete)
        ops.addWidget(self.up_btn)
        ops.addWidget(self.down_btn)
        ops.addWidget(self.del_btn)
        side.addLayout(ops)
        side.addStretch()

        body.addLayout(side, 2)
        root.addLayout(body)

        self._on_type_changed()

    # --- 类型联动 ---
    def _current_type(self) -> str:
        return self.type_combo.currentData()

    def _on_type_changed(self):
        is_delay = self._current_type() == "delay"
        self.key_input.setEnabled(not is_delay)
        self.listen_btn.setEnabled(not is_delay and PYNPUT_AVAILABLE)
        self.ms_input.setEnabled(is_delay)

    # --- 录制 ---
    def _on_listen(self):
        self.listen_btn.setText("⏹")
        if not self.capture_key_once(self._on_captured, on_timeout=self._on_listen_done):
            self._on_listen_done()

    def _on_captured(self, name: str):
        self.key_input.setText(name)
        self._on_listen_done()

    def _on_listen_done(self):
        self.listen_btn.setText("🎧")

    # --- 列表渲染/操作 ---
    def _format(self, step: Dict[str, Any]) -> str:
        t = step.get("type")
        if t == "delay":
            return f"{_TYPE_LABEL['delay']}    {int(step.get('ms', 0))} ms"
        return f"{_TYPE_LABEL.get(t, t)}    {step.get('key', '')}"

    def _append_step(self, step: Dict[str, Any]):
        item = QListWidgetItem(self._format(step))
        item.setData(Qt.UserRole, step)
        self.steps_list.addItem(item)

    def _on_add(self):
        t = self._current_type()
        if t == "delay":
            step = {"type": "delay", "ms": int(self.ms_input.value())}
        else:
            k = normalize_key_name(self.key_input.text())
            if not k:
                return  # 无键名不添加
            step = {"type": t, "key": k}
        self._append_step(step)
        self.steps_list.setCurrentRow(self.steps_list.count() - 1)

    def _on_delete(self):
        row = self.steps_list.currentRow()
        if row >= 0:
            self.steps_list.takeItem(row)

    def _move(self, delta: int):
        row = self.steps_list.currentRow()
        new = row + delta
        if row < 0 or new < 0 or new >= self.steps_list.count():
            return
        item = self.steps_list.takeItem(row)
        self.steps_list.insertItem(new, item)
        self.steps_list.setCurrentRow(new)

    # --- 配置读写(与外部交换 macro_steps) ---
    def get_steps(self) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        for i in range(self.steps_list.count()):
            data = self.steps_list.item(i).data(Qt.UserRole)
            if isinstance(data, dict):
                steps.append(data)
        return normalize_macro_steps(steps)

    def set_steps(self, steps: Any):
        self.steps_list.clear()
        for step in normalize_macro_steps(steps):
            self._append_step(step)
