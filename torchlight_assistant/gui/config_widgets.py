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
from PySide6.QtCore import QSignalBlocker
from copy import deepcopy
from typing import Dict, Any

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
    ConfigComboBox,
)
from ..utils.config_values import config_int
from ..utils.key_names import normalize_key_name


class WindowActivationWidget(QWidget):
    """窗口激活配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._config_snapshot: Dict[str, Any] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("「通用」窗口激活配置")
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
        # 目标进程可能在编辑配置时尚未启动。保持可编辑，才能保留 JSON 中
        # 不在当前进程枚举结果里的 ahk_exe，而不是悄悄换成列表第一项。
        self.widgets["exe"].setEditable(True)
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
        window_config = deepcopy(self._config_snapshot)
        window_config.update(
            {
                "enabled": self.widgets["enabled"].isChecked(),
                "ahk_class": self.widgets["class"].text().strip(),
                "ahk_exe": self.widgets["exe"].currentText().strip(),
            }
        )
        return {
            "window_activation": window_config
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        raw_win_config = (
            config.get("window_activation", {}) if isinstance(config, dict) else {}
        )
        win_config = raw_win_config if isinstance(raw_win_config, dict) else {}
        self._config_snapshot = deepcopy(win_config)

        self.widgets["enabled"].setChecked(win_config.get("enabled") is True)
        self.widgets["class"].setText(str(win_config.get("ahk_class", "") or ""))

        exe_name = str(win_config.get("ahk_exe", "") or "")
        combo = self.widgets["exe"]
        blocker = QSignalBlocker(combo)
        try:
            if exe_name and combo.findText(exe_name) < 0:
                combo.addItem(exe_name)
            # 可编辑下拉框在 setCurrentText("") 时可能保留当前枚举项。
            # 显式写编辑框，并阻断“用户选择进程”信号，防止加载配置时
            # 自动探测逻辑反过来覆盖刚加载的 ahk_class。
            combo.setEditText(str(exe_name or ""))
        finally:
            del blocker

        if win_config.get("ahk_class") or win_config.get("ahk_exe"):
            self.widgets["status_label"].setText("已加载目标窗口配置")
        else:
            self.widgets["status_label"].setText("当前未设置窗口激活")
        # 进程探测失败时状态标签会临时变红。切换 profile 属于一次完整状态
        # 替换，文字和样式必须一起复位，不能让上一份配置的错误样式串过来。
        self.widgets["status_label"].setStyleSheet("color: gray; font-size: 8pt;")


class StationaryModeWidget(QWidget):
    """原地与交互模式配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._config_snapshot: Dict[str, Any] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("「通用」原地与交互模式配置")
        grid_layout = QGridLayout(group)
        grid_layout.setContentsMargins(15, 20, 15, 15)
        grid_layout.setSpacing(12)

        # 原地模式热键
        grid_layout.addWidget(QLabel("原地模式热键:"), 0, 0)
        self.hotkey_entry = ConfigLineEdit()
        self.hotkey_entry.setMaximumHeight(32)
        self.hotkey_entry.setPlaceholderText("清空则禁用")
        grid_layout.addWidget(self.hotkey_entry, 0, 1)

        # 原地实现方式
        grid_layout.addWidget(QLabel("原地实现方式:"), 1, 0)
        self.mode_combo = ConfigComboBox()
        self.mode_combo.setMaximumHeight(32)
        self.mode_combo.addItems(["为所有按键添加Shift修饰符", "阻止左键和右键执行"])
        grid_layout.addWidget(self.mode_combo, 1, 1)

        # 交互/强制移动键
        grid_layout.addWidget(QLabel("交互/强制移动键:"), 2, 0)
        self.force_move_hotkey_entry = ConfigLineEdit()
        self.force_move_hotkey_entry.setMaximumHeight(32)
        self.force_move_hotkey_entry.setPlaceholderText("清空则禁用")
        grid_layout.addWidget(self.force_move_hotkey_entry, 2, 1)

        # 交互替换键
        grid_layout.addWidget(QLabel("交互替换键:"), 3, 0)
        self.force_move_replacement_key_entry = ConfigLineEdit()
        self.force_move_replacement_key_entry.setMaximumHeight(32)
        self.force_move_replacement_key_entry.setPlaceholderText("默认: f")
        grid_layout.addWidget(self.force_move_replacement_key_entry, 3, 1)

        # 强制移动白名单
        grid_layout.addWidget(QLabel("强制移动白名单:"), 4, 0)
        self.force_move_passthrough_keys_entry = ConfigLineEdit()
        self.force_move_passthrough_keys_entry.setMaximumHeight(32)
        self.force_move_passthrough_keys_entry.setPlaceholderText("如: RButton, space")
        self.force_move_passthrough_keys_entry.setToolTip(
            "强制移动期间允许程序自动发出的按键。特殊按键不会自动加入这里，"
            "需要保留时请显式填写。"
        )
        grid_layout.addWidget(self.force_move_passthrough_keys_entry, 4, 1)

        description_label = QLabel(
            "• 原地模式: 开启后，角色将原地释放技能而不移动。\n"
            "• 交互/强制移动键: 按住此键将临时屏蔽所有技能，只执行移动（鼠标左键）或交互。\n"
            "• 交互替换键: 交互模式激活时，非白名单技能键将被替换为此键（通常设置为移动键，如f）。\n"
            "• 强制移动白名单: 逗号分隔，仅控制程序自动发键；特殊按键不会自动加入，HP/MP 紧急药剂始终不会被替换。"
        )
        description_label.setStyleSheet("color: #888888; font-size: 9pt;")
        description_label.setWordWrap(True)
        grid_layout.addWidget(description_label, 5, 0, 1, 2)

        self.status_label = QLabel("当前未设置")
        self.status_label.setStyleSheet("color: #4a90e2; font-weight: bold;")
        grid_layout.addWidget(self.status_label, 6, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        stationary_hotkey = normalize_key_name(self.hotkey_entry.text())
        force_move_hotkey = normalize_key_name(self.force_move_hotkey_entry.text())
        force_move_replacement_key = normalize_key_name(
            self.force_move_replacement_key_entry.text()
        )
        force_move_passthrough_keys = self._parse_force_move_passthrough_keys()

        stationary_config = deepcopy(self._config_snapshot)
        if self.mode_combo.currentIndex() == 0:
            mode_type = "shift_modifier"
        elif self.mode_combo.currentIndex() == 1:
            mode_type = "block_mouse"
        else:
            # 未知枚举保持原值，交给运行时拒绝；只有用户主动选择受支持项
            # 才迁移。这样加载/保存不会把未来值静默改成另一种输入语义。
            mode_type = str(
                self._config_snapshot.get("mode_type", "block_mouse")
            )

        stationary_config.update(
            {
                "hotkey": stationary_hotkey if stationary_hotkey else "",
                "mode_type": mode_type,
                "force_move_hotkey": force_move_hotkey if force_move_hotkey else "",
                "force_move_replacement_key": force_move_replacement_key if force_move_replacement_key else "f",
                "force_move_passthrough_keys": force_move_passthrough_keys,
            }
        )
        return {"stationary_mode_config": stationary_config}

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        raw_stationary_config = (
            config.get("stationary_mode_config", {})
            if isinstance(config, dict)
            else {}
        )
        stationary_config = (
            raw_stationary_config
            if isinstance(raw_stationary_config, dict)
            else {}
        )
        self._config_snapshot = deepcopy(stationary_config)

        self.hotkey_entry.setText(normalize_key_name(stationary_config.get("hotkey")))
        self.force_move_hotkey_entry.setText(
            normalize_key_name(stationary_config.get("force_move_hotkey"))
        )
        self.force_move_replacement_key_entry.setText(
            normalize_key_name(
                stationary_config.get("force_move_replacement_key", "f")
            )
        )
        raw_passthrough = stationary_config.get("force_move_passthrough_keys", [])
        passthrough = raw_passthrough if isinstance(raw_passthrough, list) else []
        self.force_move_passthrough_keys_entry.setText(
            ", ".join(
                normalized
                for value in passthrough
                if (normalized := normalize_key_name(value))
            )
        )

        mode_type = str(stationary_config.get("mode_type", "block_mouse") or "")
        mode_index = {"shift_modifier": 0, "block_mouse": 1}.get(mode_type, -1)
        self.mode_combo.setCurrentIndex(mode_index)

        # 更新状态显示
        hotkey = normalize_key_name(stationary_config.get("hotkey"))
        force_move_hotkey = normalize_key_name(
            stationary_config.get("force_move_hotkey")
        )

        if not hotkey and not force_move_hotkey:
            self.status_label.setText("当前未设置")
        else:
            mode_desc = {
                "shift_modifier": "Shift修饰符",
                "block_mouse": "阻止鼠标键",
            }.get(mode_type, f"无效模式:{mode_type}")
            status_parts = []
            if hotkey:
                status_parts.append(f"原地模式: {hotkey.upper()}")
            if force_move_hotkey:
                status_parts.append(f"交互键: {force_move_hotkey.upper()}")
            status_text = " | ".join(status_parts) + f" ({mode_desc})"
            self.status_label.setText(status_text)

    def _parse_force_move_passthrough_keys(self) -> list[str]:
        """解析强制移动白名单，逗号分隔，保持 AHK 标准名大小写。"""
        raw = self.force_move_passthrough_keys_entry.text().strip()
        if not raw:
            return []
        keys = []
        for part in raw.split(","):
            key = self._normalize_passthrough_key(part.strip())
            if key:
                keys.append(key)
        return keys

    def _normalize_passthrough_key(self, key: str) -> str:
        """委托给全项目唯一的 AHK 按键名归一化器。"""
        return normalize_key_name(key)


class PathfindingWidget(QWidget):
    """自动寻路配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self._path_config: Dict[str, Any] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        group = QGroupBox("自动寻路设置")
        grid = QGridLayout(group)

        fixed_hotkey = QLabel("寻路热键: F9（永久根热键，不可配置）")
        grid.addWidget(fixed_hotkey, 0, 0, 1, 2)

        grid.addWidget(QLabel("小地图区域 (X, Y, W, H):"), 1, 0, 1, 2)
        self.widgets["minimap_x"] = ConfigSpinBox()
        self.widgets["minimap_y"] = ConfigSpinBox()
        self.widgets["minimap_w"] = ConfigSpinBox()
        self.widgets["minimap_h"] = ConfigSpinBox()

        # x/y 使用虚拟桌面绝对坐标，左侧或上方副屏可以为负；尺寸仍不可为负。
        self.widgets["minimap_x"].setRange(-32768, 32767)
        self.widgets["minimap_y"].setRange(-32768, 32767)
        self.widgets["minimap_w"].setRange(0, 32767)
        self.widgets["minimap_h"].setRange(0, 32767)

        coords_layout = QHBoxLayout()
        coords_layout.addWidget(self.widgets["minimap_x"])
        coords_layout.addWidget(self.widgets["minimap_y"])
        coords_layout.addWidget(self.widgets["minimap_w"])
        coords_layout.addWidget(self.widgets["minimap_h"])
        grid.addLayout(coords_layout, 2, 0, 1, 2)

        layout.addWidget(group)
        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        path_config = deepcopy(self._path_config)
        # 旧版曾展示一个实际无人读取的 hotkey 输入框；运行时始终由永久 F9
        # 根热键驱动，保存时迁移掉该伪配置，避免给用户可定制的错觉。
        path_config.pop("hotkey", None)
        path_config["minimap_area"] = [
            self.widgets["minimap_x"].value(),
            self.widgets["minimap_y"].value(),
            self.widgets["minimap_w"].value(),
            self.widgets["minimap_h"].value(),
        ]
        return {
            "pathfinding_config": path_config
        }

    def update_from_config(self, config: Dict[str, Any]):
        raw_path_config = (
            config.get("pathfinding_config", {}) if isinstance(config, dict) else {}
        )
        path_config = raw_path_config if isinstance(raw_path_config, dict) else {}
        self._path_config = deepcopy(path_config)
        minimap_area = path_config.get("minimap_area", [0, 0, 0, 0])
        values = [0, 0, 0, 0]
        if isinstance(minimap_area, (list, tuple)) and len(minimap_area) == 4:
            try:
                candidate = [config_int(value) for value in minimap_area]
                if candidate[2] >= 0 and candidate[3] >= 0:
                    values = candidate
            except ValueError:
                pass
        for name, value in zip(
            ("minimap_x", "minimap_y", "minimap_w", "minimap_h"), values
        ):
            self.widgets[name].setValue(value)
