#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""优先级按键配置组件"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QInputDialog,
)
from PySide6.QtCore import Qt
from typing import Dict, Any, Set, List
import json

from .custom_widgets import ConfigCheckBox
from ..utils.debug_log import LOG_INFO, LOG_ERROR


class PriorityKeysWidget(QWidget):
    """优先级按键配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        self.priority_keys = []  # 使用列表保持顺序
        self._setup_ui()
        self._load_default_keys()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 主配置组
        group = QGroupBox("优先级按键配置")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 15, 10, 10)
        group_layout.setSpacing(8)

        # 说明文字
        info_label = QLabel(
            "优先级按键：当这些按键被按下时，所有技能执行会暂停，确保优先级操作不被打断。\n"
            "典型用途：闪避(space)、布雷(right_mouse)、特殊技能等。"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 9pt; margin-bottom: 5px;")
        group_layout.addWidget(info_label)

        # 启用开关
        self.widgets["enabled"] = ConfigCheckBox("启用优先级按键系统")
        self.widgets["enabled"].setChecked(True)
        group_layout.addWidget(self.widgets["enabled"])

        # 按键列表区域
        keys_frame = QFrame()
        keys_layout = QHBoxLayout(keys_frame)
        keys_layout.setContentsMargins(0, 5, 0, 0)

        # 左侧：当前按键列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("已配置的优先级按键:"))
        
        self.keys_list = QListWidget()
        self.keys_list.setMaximumHeight(120)
        self.keys_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f8f9fa;
                font-size: 11pt;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-bottom: 1px solid #e0e0e0;
                background-color: white;
                color: #333;
                margin: 1px;
                border-radius: 2px;
            }
            QListWidget::item:hover {
                background-color: #e3f2fd;
                color: #1976d2;
            }
            QListWidget::item:selected {
                background-color: #2196f3;
                color: white;
                font-weight: bold;
            }
        """)
        left_layout.addWidget(self.keys_list)
        
        keys_layout.addLayout(left_layout, 2)

        # 右侧：操作按钮
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("操作:"))
        
        # 添加按键按钮
        self.add_key_btn = QPushButton("+ 添加按键")
        self.add_key_btn.setMaximumHeight(28)
        self.add_key_btn.clicked.connect(self._add_key)
        right_layout.addWidget(self.add_key_btn)
        
        # 删除按键按钮
        self.remove_key_btn = QPushButton("- 删除选中")
        self.remove_key_btn.setMaximumHeight(28)
        self.remove_key_btn.clicked.connect(self._remove_key)
        self.remove_key_btn.setEnabled(False)
        right_layout.addWidget(self.remove_key_btn)
        
        # 重置按钮
        self.reset_btn = QPushButton("重置默认")
        self.reset_btn.setMaximumHeight(28)
        self.reset_btn.clicked.connect(self._reset_to_default)
        right_layout.addWidget(self.reset_btn)
        
        right_layout.addStretch()
        keys_layout.addLayout(right_layout, 1)

        group_layout.addWidget(keys_frame)

        # 预设配置
        presets_frame = QFrame()
        presets_layout = QHBoxLayout(presets_frame)
        presets_layout.setContentsMargins(0, 5, 0, 0)

        presets_layout.addWidget(QLabel("快速预设:"))
        
        dodge_btn = QPushButton("闪避模式 (space)")
        dodge_btn.setMaximumHeight(26)
        dodge_btn.clicked.connect(lambda: self._apply_preset(['space']))
        presets_layout.addWidget(dodge_btn)
        
        mine_btn = QPushButton("布雷模式 (right_mouse)")
        mine_btn.setMaximumHeight(26)
        mine_btn.clicked.connect(lambda: self._apply_preset(['right_mouse']))
        presets_layout.addWidget(mine_btn)
        
        combat_btn = QPushButton("战斗模式 (space, right_mouse)")
        combat_btn.setMaximumHeight(26)
        combat_btn.clicked.connect(lambda: self._apply_preset(['space', 'right_mouse']))
        presets_layout.addWidget(combat_btn)
        
        presets_layout.addStretch()
        group_layout.addWidget(presets_frame)

        layout.addWidget(group)

        # 连接信号
        self.keys_list.itemSelectionChanged.connect(self._on_selection_changed)

    def _load_default_keys(self):
        """加载默认按键配置"""
        default_keys = ['space', 'right_mouse']
        self.priority_keys = default_keys.copy()
        self._update_keys_display()

    def _update_keys_display(self):
        """更新按键列表显示"""
        self.keys_list.clear()
        
        for key in self.priority_keys:
            item = QListWidgetItem(self._format_key_display(key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.keys_list.addItem(item)

    def _format_key_display(self, key: str) -> str:
        """格式化按键显示名称"""
        key_names = {
            'space': '空格键 (space)',
            'left_mouse': '左键 (left_mouse)',
            'right_mouse': '右键 (right_mouse)',
            'middle_mouse': '中键 (middle_mouse)',
            'ctrl': 'Ctrl键 (ctrl)',
            'shift': 'Shift键 (shift)',
            'alt': 'Alt键 (alt)',
            'tab': 'Tab键 (tab)',
            'esc': 'Esc键 (esc)',
            'enter': '回车键 (enter)',
        }
        
        return key_names.get(key, f'{key.upper()}键 ({key})')

    def _normalize_key_name(self, key: str) -> str:
        """标准化按键名称，避免大小写和格式问题"""
        if not key:
            return ""
        
        # 基本标准化：小写并去除空格
        normalized = key.lower().strip()
        
        # 统一按键名称映射
        key_mapping = {
            # 鼠标按键标准化
            'leftmouse': 'left_mouse',
            'mouse_left': 'left_mouse',
            'lbutton': 'left_mouse',
            'leftclick': 'left_mouse',
            
            'rightmouse': 'right_mouse',
            'mouse_right': 'right_mouse',
            'rbutton': 'right_mouse',
            'rightclick': 'right_mouse',
            
            'middlemouse': 'middle_mouse',
            'mouse_middle': 'middle_mouse',
            'mbutton': 'middle_mouse',
            
            # 特殊键标准化
            'spacebar': 'space',
            'space_bar': 'space',
            'control': 'ctrl',
            'return': 'enter',
            'escape': 'esc',
        }
        
        # 应用映射
        return key_mapping.get(normalized, normalized)

    def _add_key(self):
        """添加新的优先级按键 - 使用简化的输入方式"""
        # 常用按键选项
        common_keys = [
            "space", "ctrl", "shift", "alt", "tab", "enter", "esc",
            "left_mouse", "right_mouse", "middle_mouse",
            "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
            "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
            "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"
        ]
        
        # 创建下拉选择对话框
        key_name, ok = QInputDialog.getItem(
            self, "选择按键", "请选择要添加的优先级按键:", 
            common_keys, 0, True
        )
        
        if ok and key_name:
            key_name = self._normalize_key_name(key_name)  # 使用标准化方法
            if key_name in self.priority_keys:
                QMessageBox.information(self, "提示", f"按键 '{key_name}' 已经在优先级列表中")
                return
            
            self.priority_keys.append(key_name)
            self._update_keys_display()
            LOG_INFO(f"[优先级按键] 添加按键: {key_name}")

    def _remove_key(self):
        """删除选中的按键"""
        current_item = self.keys_list.currentItem()
        if not current_item:
            return
        
        key_name = current_item.data(Qt.ItemDataRole.UserRole)
        if key_name in self.priority_keys:
            self.priority_keys.remove(key_name)
        self._update_keys_display()
        LOG_INFO(f"[优先级按键] 删除按键: {key_name}")

    def _reset_to_default(self):
        """重置为默认配置"""
        reply = QMessageBox.question(
            self, "确认重置", 
            "确定要重置为默认配置吗？\n默认配置：space, right_mouse",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._load_default_keys()
            LOG_INFO("[优先级按键] 重置为默认配置")

    def _apply_preset(self, keys: List[str]):
        """应用预设配置"""
        self.priority_keys = keys.copy()
        self._update_keys_display()
        LOG_INFO(f"[优先级按键] 应用预设: {keys}")

    def _on_selection_changed(self):
        """处理选择变化"""
        has_selection = bool(self.keys_list.currentItem())
        self.remove_key_btn.setEnabled(has_selection)

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            "enabled": self.widgets["enabled"].isChecked(),
            "keys": self.priority_keys.copy()
        }

    def set_config(self, config: Dict[str, Any]):
        """设置配置"""
        if "enabled" in config:
            self.widgets["enabled"].setChecked(config["enabled"])
        
        if "keys" in config:
            self.priority_keys = list(config["keys"])
            self._update_keys_display()

    def get_priority_keys(self) -> Set[str]:
        """获取优先级按键集合"""
        if self.widgets["enabled"].isChecked():
            return set(self.priority_keys)
        return set()