#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""基础UI组件模块"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QCheckBox,
    QGroupBox,
    QFrame,
    QTextEdit,
    QSpinBox,
)
from PySide6.QtCore import Qt
from typing import Dict, Any

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
    ConfigComboBox,
)


# 状态字符串常量
class StatusStrings:
    STOPPED = "停止"
    READY = "准备就绪,按Z键开始"
    RUNNING = "运行中"
    PAUSED = "暂停"
    RUNNING_INTERACTION = "运行中(交互)"
    RUNNING_STATIONARY = "运行中(原地)"
    PAUSED_INTERACTION = "暂停(交互)"
    PAUSED_STATIONARY = "暂停(原地)"


class TopControlsWidget(QWidget):
    """顶部控件组件"""

    def __init__(self):
        super().__init__()
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 配置文件管理
        layout.addWidget(QLabel("当前配置:"))
        self.current_config_label = QLabel("default.json")
        layout.addWidget(self.current_config_label)

        self.save_btn = QPushButton("保存配置")
        self.save_btn.setMaximumHeight(28)
        layout.addWidget(self.save_btn)

        self.load_btn = QPushButton("加载配置")
        self.load_btn.setMaximumHeight(28)
        layout.addWidget(self.load_btn)

        # 分隔符
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("color: #666666;")
        layout.addWidget(separator)

        # 游戏模式选择
        layout.addWidget(QLabel("游戏技能模式:"))
        self.mode_combo = ConfigComboBox()
        self.mode_combo.setMaximumHeight(28)
        self.mode_combo.addItems(["技能", "序列"])
        layout.addWidget(self.mode_combo)

        layout.addStretch()

    def set_current_config(self, filename: str):
        """设置当前配置文件名"""
        self.current_config_label.setText(filename)

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        return {"sequence_enabled": self.mode_combo.currentText() == "序列"}

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        is_sequence = config.get("sequence_enabled", False)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("序列" if is_sequence else "技能")
        self.mode_combo.blockSignals(False)


class TimingSettingsWidget(QWidget):
    """时间间隔和通用设置组件"""

    def __init__(self):
        super().__init__()
        self.timing_spinboxes = {}
        self.sound_feedback_checkbox = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 时间间隔设置
        time_group = QGroupBox("时间间隔设置 (毫秒)")
        grid_layout = QGridLayout(time_group)
        grid_layout.setContentsMargins(15, 20, 15, 15)
        grid_layout.setSpacing(12)

        settings = [
            ("队列处理:", "queue_processor"),
            ("按键时长:", "key_press"),
            ("鼠标时长:", "mouse_click"),
            ("冷却检查:", "cooldown_checker"),
            ("按键间隔:", "sequence_timer"),
            ("图像捕获间隔:", "capture_interval"),
        ]

        row, col = 0, 0
        for label_text, key in settings:
            grid_layout.addWidget(QLabel(label_text), row, col)
            spinbox = ConfigSpinBox()
            spinbox.setMinimumHeight(32)
            spinbox.setMinimum(1)
            spinbox.setMaximum(999999)
            self.timing_spinboxes[key] = spinbox
            grid_layout.addWidget(spinbox, row, col + 1)
            col += 2
            if col >= 4:
                col, row = 0, row + 1
        layout.addWidget(time_group)

        # 声音设置
        sound_group = QGroupBox("声音设置")
        sound_layout = QHBoxLayout(sound_group)
        sound_layout.setContentsMargins(15, 20, 15, 15)
        self.sound_feedback_checkbox = ConfigCheckBox(
            "启用状态切换声音提示 (start, stop, pause, resume)"
        )
        sound_layout.addWidget(self.sound_feedback_checkbox)
        sound_layout.addStretch()
        layout.addWidget(sound_group)

        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        config = {
            "queue_processor_interval": self.timing_spinboxes[
                "queue_processor"
            ].value(),
            "key_press_duration": self.timing_spinboxes["key_press"].value(),
            "mouse_click_duration": self.timing_spinboxes["mouse_click"].value(),
            "cooldown_checker_interval": self.timing_spinboxes[
                "cooldown_checker"
            ].value(),
            "sequence_timer_interval": self.timing_spinboxes["sequence_timer"].value(),
            "capture_interval": self.timing_spinboxes["capture_interval"].value(),
        }
        if self.sound_feedback_checkbox:
            config["sound_feedback_enabled"] = self.sound_feedback_checkbox.isChecked()
        return config

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        mapping = {
            "queue_processor": config.get("queue_processor_interval", 50),
            "key_press": config.get("key_press_duration", 10),
            "mouse_click": config.get("mouse_click_duration", 5),
            "cooldown_checker": config.get("cooldown_checker_interval", 100),
            "sequence_timer": config.get("sequence_timer_interval", 250),
            "capture_interval": config.get("capture_interval", 40),
        }
        for key, value in mapping.items():
            if key in self.timing_spinboxes:
                self.timing_spinboxes[key].setValue(value)

        if self.sound_feedback_checkbox:
            self.sound_feedback_checkbox.setChecked(
                config.get("sound_feedback_enabled", False)
            )