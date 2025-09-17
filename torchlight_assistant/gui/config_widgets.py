#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置相关UI组件"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QFrame,
)
from typing import Dict, Any

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
    ConfigComboBox,
)


class WindowActivationWidget(QWidget):
    """窗口激活配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("窗口激活配置")
        grid_layout = QGridLayout(group)
        grid_layout.setContentsMargins(6, 8, 6, 6)
        grid_layout.setSpacing(4)

        self.widgets["enabled"] = ConfigCheckBox("自动激活窗口")
        grid_layout.addWidget(self.widgets["enabled"], 0, 0, 1, 4)

        grid_layout.addWidget(QLabel("窗口类名:"), 1, 0)
        self.widgets["class"] = ConfigLineEdit()
        self.widgets["class"].setMaximumHeight(26)
        grid_layout.addWidget(self.widgets["class"], 1, 1)

        grid_layout.addWidget(QLabel("进程名:"), 1, 2)
        self.widgets["exe"] = ConfigComboBox()
        self.widgets["exe"].setMaximumHeight(26)
        self.widgets["exe"].setEditable(False)
        grid_layout.addWidget(self.widgets["exe"], 1, 3)

        # 按钮
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)

        refresh_btn = QPushButton("刷新进程列表")
        refresh_btn.setMaximumHeight(26)
        button_layout.addWidget(refresh_btn)

        mumu_btn = QPushButton("MuMu模拟器")
        mumu_btn.setMaximumHeight(26)
        button_layout.addWidget(mumu_btn)

        button_layout.addStretch()
        grid_layout.addWidget(button_frame, 2, 0, 1, 4)

        self.widgets["status_label"] = QLabel("当前未设置窗口激活")
        self.widgets["status_label"].setStyleSheet("color: gray; font-size: 8pt;")
        grid_layout.addWidget(self.widgets["status_label"], 3, 0, 1, 4)

        layout.addWidget(group)

        # 初始化进程列表
        self._populate_initial_process_list()

    def _populate_initial_process_list(self):
        """初始化进程列表"""
        try:
            import psutil

            processes = []
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    processes.append(proc.info["name"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # 去重并排序
            unique_processes = sorted(list(set(processes)))
            self.widgets["exe"].addItems(unique_processes)
        except Exception:
            # 如果获取进程列表失败，添加一些常见的进程名
            common_processes = [
                "MuMuPlayer.exe",
                "chrome.exe",
                "firefox.exe",
                "notepad.exe",
            ]
            self.widgets["exe"].addItems(common_processes)

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        return {
            "window_activation": {
                "enabled": self.widgets["enabled"].isChecked(),
                "ahk_class": self.widgets["class"].text().strip(),
                "ahk_exe": self.widgets["exe"].currentText().strip(),
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        win_config = config.get("window_activation", {})

        self.widgets["enabled"].setChecked(win_config.get("enabled", False))
        self.widgets["class"].setText(win_config.get("ahk_class", ""))

        exe_name = win_config.get("ahk_exe", "")
        if exe_name:
            self.widgets["exe"].setCurrentText(exe_name)


class StationaryModeWidget(QWidget):
    """原地与交互模式配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("原地与交互模式配置")
        grid_layout = QGridLayout(group)
        grid_layout.setContentsMargins(15, 20, 15, 15)
        grid_layout.setSpacing(12)

        # 原地模式热键
        grid_layout.addWidget(QLabel("原地模式热键:"), 0, 0)
        self.hotkey_entry = ConfigLineEdit()
        self.hotkey_entry.setMaximumHeight(32)
        self.hotkey_entry.setPlaceholderText("清空则禁用")
        grid_layout.addWidget(self.hotkey_entry, 0, 1)

        # 交互/强制移动键
        grid_layout.addWidget(QLabel("交互/强制移动键:"), 1, 0)
        self.force_move_hotkey_entry = ConfigLineEdit()
        self.force_move_hotkey_entry.setMaximumHeight(32)
        self.force_move_hotkey_entry.setPlaceholderText("清空则禁用")
        grid_layout.addWidget(self.force_move_hotkey_entry, 1, 1)

        # 原地实现方式
        grid_layout.addWidget(QLabel("原地实现方式:"), 2, 0)
        self.mode_combo = ConfigComboBox()
        self.mode_combo.setMaximumHeight(32)
        self.mode_combo.addItems(["为所有按键添加Shift修饰符", "阻止左键和右键执行"])
        grid_layout.addWidget(self.mode_combo, 2, 1)

        description_label = QLabel(
            "• 原地模式: 开启后，角色将原地释放技能而不移动。\n"
            "• 交互/强制移动键: 按住此键将临时屏蔽所有技能，只执行移动（鼠标左键）或交互。"
        )
        description_label.setStyleSheet("color: #888888; font-size: 9pt;")
        description_label.setWordWrap(True)
        grid_layout.addWidget(description_label, 3, 0, 1, 2)

        self.status_label = QLabel("当前未设置")
        self.status_label.setStyleSheet("color: #4a90e2; font-weight: bold;")
        grid_layout.addWidget(self.status_label, 4, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        stationary_hotkey = self.hotkey_entry.text().strip().lower()
        force_move_hotkey = self.force_move_hotkey_entry.text().strip().lower()

        return {
            "stationary_mode_config": {
                "hotkey": stationary_hotkey if stationary_hotkey else "",
                "mode_type": (
                    "shift_modifier"
                    if self.mode_combo.currentIndex() == 0
                    else "block_mouse"
                ),
                "force_move_hotkey": force_move_hotkey if force_move_hotkey else "",
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        stationary_config = config.get(
            "stationary_mode_config",
            {"hotkey": "", "mode_type": "block_mouse", "force_move_hotkey": ""},
        )

        self.hotkey_entry.setText(stationary_config.get("hotkey", ""))
        self.force_move_hotkey_entry.setText(
            stationary_config.get("force_move_hotkey", "")
        )

        mode_type = stationary_config.get("mode_type", "block_mouse")
        self.mode_combo.setCurrentIndex(0 if mode_type == "shift_modifier" else 1)

        # 更新状态显示
        hotkey = stationary_config.get("hotkey", "")
        force_move_hotkey = stationary_config.get("force_move_hotkey", "")

        if not hotkey and not force_move_hotkey:
            self.status_label.setText("当前未设置")
        else:
            mode_desc = "Shift修饰符" if mode_type == "shift_modifier" else "阻止鼠标键"
            status_parts = []
            if hotkey:
                status_parts.append(f"原地模式: {hotkey.upper()}")
            if force_move_hotkey:
                status_parts.append(f"交互键: {force_move_hotkey.upper()}")
            status_text = " | ".join(status_parts) + f" ({mode_desc})"
            self.status_label.setText(status_text)


class PathfindingWidget(QWidget):
    """自动寻路配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        group = QGroupBox("自动寻路设置")
        grid = QGridLayout(group)

        grid.addWidget(QLabel("寻路热键:"), 0, 0)
        self.widgets["hotkey"] = ConfigLineEdit()
        self.widgets["hotkey"].setText("f9")
        grid.addWidget(self.widgets["hotkey"], 0, 1)

        grid.addWidget(QLabel("小地图区域 (X, Y, W, H):"), 1, 0, 1, 2)
        self.widgets["minimap_x"] = ConfigSpinBox()
        self.widgets["minimap_y"] = ConfigSpinBox()
        self.widgets["minimap_w"] = ConfigSpinBox()
        self.widgets["minimap_h"] = ConfigSpinBox()

        for w in self.widgets.values():
            if isinstance(w, ConfigSpinBox):
                w.setRange(0, 8000)

        coords_layout = QHBoxLayout()
        coords_layout.addWidget(self.widgets["minimap_x"])
        coords_layout.addWidget(self.widgets["minimap_y"])
        coords_layout.addWidget(self.widgets["minimap_w"])
        coords_layout.addWidget(self.widgets["minimap_h"])
        grid.addLayout(coords_layout, 2, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        return {
            "pathfinding_config": {
                "hotkey": self.widgets["hotkey"].text().strip().lower(),
                "minimap_area": [
                    self.widgets["minimap_x"].value(),
                    self.widgets["minimap_y"].value(),
                    self.widgets["minimap_w"].value(),
                    self.widgets["minimap_h"].value(),
                ],
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        path_config = config.get("pathfinding_config", {})
        self.widgets["hotkey"].setText(path_config.get("hotkey", "f9"))
        minimap_area = path_config.get("minimap_area", [0, 0, 0, 0])
        if len(minimap_area) == 4:
            self.widgets["minimap_x"].setValue(minimap_area[0])
            self.widgets["minimap_y"].setValue(minimap_area[1])
            self.widgets["minimap_w"].setValue(minimap_area[2])
            self.widgets["minimap_h"].setValue(minimap_area[3])