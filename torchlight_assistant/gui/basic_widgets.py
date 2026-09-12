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
from copy import deepcopy
from typing import Dict, Any

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
    ConfigComboBox,
)
from ..utils.key_names import normalize_key_name
from ..utils.config_values import config_int


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
        self._input_mode_snapshot = "direct"
        self._debug_config_snapshot: Dict[str, Any] = {}
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

        # 战斗模式选择(技能 / 序列 —— 输入系统的两大模块)
        layout.addWidget(QLabel("战斗模式:"))
        self.mode_combo = ConfigComboBox()
        self.mode_combo.setMaximumHeight(28)
        self.mode_combo.addItems(["技能", "序列"])
        self.mode_combo.setToolTip(
            "技能模式: 每个技能独立配置(定时/冷却检测/条件),按各自规则触发\n"
            "序列/宏模式: 按宏步骤列表循环执行,等待时间由「延时」步骤显式配置\n"
            "(HP/MP 智能药剂、优先级按键、强制移动等「通用」设置两种模式都生效)"
        )
        layout.addWidget(self.mode_combo)

        # DEBUG MODE选择
        layout.addWidget(QLabel("调试模式:"))
        self.debug_mode_checkbox = ConfigCheckBox("启用")
        self.debug_mode_checkbox.setMaximumHeight(28)
        layout.addWidget(self.debug_mode_checkbox)

        # Input Mode选择
        layout.addWidget(QLabel("输入模式:"))
        self.input_mode_combo = ConfigComboBox()
        self.input_mode_combo.setMaximumHeight(28)
        self.input_mode_combo.addItems(["Direct", "Control"])
        self.input_mode_combo.setToolTip(
            "Direct: 直接发送 (SendInput), 需要窗口激活\n"
            "Control: 控件发送 (ControlSend), 后台发送不需要激活"
        )
        layout.addWidget(self.input_mode_combo)

        # BOSS 模式切换键：只切换自动规则分组，不直接发送技能键
        layout.addWidget(QLabel("BOSS键:"))
        self.boss_mode_hotkey_entry = ConfigLineEdit()
        self.boss_mode_hotkey_entry.setMaximumWidth(80)
        self.boss_mode_hotkey_entry.setPlaceholderText("如 XButton1")
        self.boss_mode_hotkey_entry.setToolTip(
            "技能模式下切换 BOSS 模式。\n"
            "BOSS 模式关闭时，勾选 BOSS 的技能不会自动触发；开启后按原规则触发。\n"
            "建议使用闲置鼠标侧键，如 XButton1/XButton2。"
        )
        layout.addWidget(self.boss_mode_hotkey_entry)

        layout.addStretch()

    def set_current_config(self, filename: str):
        """设置当前配置文件名"""
        self.current_config_label.setText(filename)

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        current_input_mode = self.input_mode_combo.currentText().lower()
        input_mode = (
            current_input_mode
            if current_input_mode in {"direct", "control"}
            else deepcopy(self._input_mode_snapshot)
        )
        debug_config = deepcopy(self._debug_config_snapshot)
        debug_config["enabled"] = self.debug_mode_checkbox.isChecked()
        return {
            "sequence_enabled": self.mode_combo.currentText() == "序列",
            "debug_mode": debug_config,
            "input_mode": input_mode,
            "boss_mode_hotkey": normalize_key_name(
                self.boss_mode_hotkey_entry.text().strip()
            ) if self.boss_mode_hotkey_entry.text().strip() else "",
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        config = config if isinstance(config, dict) else {}
        is_sequence = config.get("sequence_enabled") is True
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentText("序列" if is_sequence else "技能")
        self.mode_combo.blockSignals(False)

        # DEBUG MODE
        raw_debug_config = config.get("debug_mode", {})
        debug_config = raw_debug_config if isinstance(raw_debug_config, dict) else {}
        self._debug_config_snapshot = deepcopy(debug_config)
        self.debug_mode_checkbox.setChecked(debug_config.get("enabled") is True)

        # INPUT MODE
        raw_input_mode = config.get("input_mode", "direct")
        input_mode = str(raw_input_mode or "").lower()
        self._input_mode_snapshot = deepcopy(
            input_mode if input_mode in {"direct", "control"} else raw_input_mode
        )
        self.input_mode_combo.blockSignals(True)
        if input_mode in {"direct", "control"}:
            self.input_mode_combo.setCurrentText(input_mode.capitalize())
        else:
            self.input_mode_combo.setCurrentIndex(-1)
        self.input_mode_combo.blockSignals(False)

        self.boss_mode_hotkey_entry.setText(
            normalize_key_name(config.get("boss_mode_hotkey"))
        )


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
        # 标签前缀标注生效模式:「技能」=仅技能模式生效,「通用」=两种模式都生效
        time_group = QGroupBox("时间间隔设置 (毫秒)  —  「技能」=仅技能模式, 「通用」=两种模式")
        grid_layout = QGridLayout(time_group)
        grid_layout.setContentsMargins(15, 20, 15, 15)
        grid_layout.setSpacing(12)

        settings = [
            ("「通用」按键时长:", "key_press"),
            ("「技能」冷却检查:", "cooldown_checker"),
            ("「通用」图像捕获间隔:", "capture_interval"),
            ("「通用」特殊键恢复保护:", "special_key_resume_delay"),
            ("「通用」HP药剂冷却:", "hp_cooldown"),
            ("「通用」MP药剂冷却:", "mp_cooldown"),
            ("「通用」MP/HP检测间隔:", "resource_check_interval"),
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
        # 捕获间隔与引擎侧钳制同一口径(macro_engine.CAPTURE_INTERVAL_MIN/MAX_MS):
        # 10..1000ms。Qt 会把加载进来的越界旧值静默钳到范围内,引擎读取处另有警告日志。
        self.timing_spinboxes["capture_interval"].setRange(10, 1000)
        self.timing_spinboxes["key_press"].setRange(1, 1000)
        # 0 保持立即恢复；正值只延迟自动输入，不延迟特殊键本身的 key-up。
        self.timing_spinboxes["special_key_resume_delay"].setRange(0, 1000)
        self.timing_spinboxes["special_key_resume_delay"].setToolTip(
            "特殊键松开后，自动技能/宏继续发送前的保护时间。0 表示立即恢复。"
        )
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
            "key_press_duration": self.timing_spinboxes["key_press"].value(),
            "cooldown_checker_interval": self.timing_spinboxes[
                "cooldown_checker"
            ].value(),
            "capture_interval": self.timing_spinboxes["capture_interval"].value(),
            "special_key_resume_delay_ms": self.timing_spinboxes[
                "special_key_resume_delay"
            ].value(),
            "hp_cooldown": self.timing_spinboxes["hp_cooldown"].value(),
            "mp_cooldown": self.timing_spinboxes["mp_cooldown"].value(),
            "resource_check_interval": self.timing_spinboxes["resource_check_interval"].value(),
        }
        if self.sound_feedback_checkbox:
            config["sound_feedback_enabled"] = self.sound_feedback_checkbox.isChecked()
        return config

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        config = config if isinstance(config, dict) else {}
        raw_resource_config = config.get("resource_management", {})
        resource_config = (
            raw_resource_config if isinstance(raw_resource_config, dict) else {}
        )
        raw_hp_config = resource_config.get("hp_config", {})
        raw_mp_config = resource_config.get("mp_config", {})
        hp_config = raw_hp_config if isinstance(raw_hp_config, dict) else {}
        mp_config = raw_mp_config if isinstance(raw_mp_config, dict) else {}
        mapping = {
            "key_press": config.get("key_press_duration", 10),
            "cooldown_checker": config.get("cooldown_checker_interval", 100),
            "capture_interval": config.get("capture_interval", 40),
            "special_key_resume_delay": config.get(
                "special_key_resume_delay_ms", 0
            ),
            # nested resource_management 是持久化权威；顶层三项只作为旧配置
            # 兼容别名读取，避免一次加载/保存把按 wiki 编写的 nested 值改回默认。
            "hp_cooldown": hp_config.get(
                "cooldown", config.get("hp_cooldown", 5000)
            ),
            "mp_cooldown": mp_config.get(
                "cooldown", config.get("mp_cooldown", 8000)
            ),
            "resource_check_interval": resource_config.get(
                "check_interval", config.get("resource_check_interval", 200)
            ),
        }
        bounds = {
            "key_press": (10, 1, 1000),
            "cooldown_checker": (100, 1, 999999),
            "capture_interval": (40, 10, 1000),
            "special_key_resume_delay": (0, 0, 1000),
            "hp_cooldown": (5000, 1, 999999),
            "mp_cooldown": (8000, 1, 999999),
            "resource_check_interval": (200, 1, 999999),
        }
        for key, value in mapping.items():
            if key in self.timing_spinboxes:
                default, minimum, maximum = bounds[key]
                try:
                    parsed = config_int(value)
                except ValueError:
                    parsed = default
                self.timing_spinboxes[key].setValue(
                    min(max(parsed, minimum), maximum)
                )

        if self.sound_feedback_checkbox:
            self.sound_feedback_checkbox.setChecked(
                config.get("sound_feedback_enabled") is True
            )
