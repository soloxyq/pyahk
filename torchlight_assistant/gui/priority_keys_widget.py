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
    QSpinBox,
    QLineEdit,
)
from PySide6.QtCore import Qt, QTimer
from typing import Dict, Any, Set, List
import json

from .custom_widgets import ConfigCheckBox
from ..utils.debug_log import LOG_INFO, LOG_ERROR

# 导入按键监听相关
try:
    from pynput import keyboard, mouse
    from pynput.keyboard import Key
    from pynput.mouse import Button
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    LOG_ERROR("[优先级按键] pynput 不可用，按键监听功能将被禁用")


class PriorityKeysWidget(QWidget):
    """优先级按键配置组件"""

    def __init__(self):
        super().__init__()
        self.widgets = {}
        # 改用字典存储按键配置: {key_name: delay_ms}
        self.priority_keys_config = {
            'space': 50,
            'right_mouse': 50
        }
        
        # 按键监听状态
        self._key_listening = False
        self._keyboard_listener = None
        self._mouse_listener = None
        
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
        list_label = QLabel("已配置的优先级按键:")
        list_label.setStyleSheet("font-weight: bold; color: #333; margin-bottom: 2px;")
        left_layout.addWidget(list_label)
        
        self.keys_list = QListWidget()
        self.keys_list.setMinimumHeight(150)
        self.keys_list.setMaximumHeight(200)
        self.keys_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f8f9fa;
                font-size: 10pt;
                padding: 2px;
            }
            QListWidget::item {
                padding: 6px 8px;
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

        # 右侧：按键配置和操作
        right_layout = QVBoxLayout()
        
        # 新增按键区域
        add_group = QFrame()
        add_layout = QVBoxLayout(add_group)
        add_layout.setContentsMargins(8, 8, 8, 8)
        add_layout.setSpacing(5)
        add_group.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 6px; background-color: #fafafa; }")
        
        add_title = QLabel("➕ 添加新按键")
        add_title.setStyleSheet("font-weight: bold; color: #333; font-size: 10pt; margin-bottom: 3px;")
        add_layout.addWidget(add_title)
        
        # 按键输入
        key_input_layout = QHBoxLayout()
        key_input_layout.setSpacing(5)
        key_label = QLabel("按键:")
        key_label.setMinimumWidth(40)
        key_label.setStyleSheet("color: #555;")
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("点击'监听按键'后按下要添加的按键")
        self.key_input.setMinimumHeight(24)
        self.key_input.setReadOnly(True)  # 设为只读，通过监听获取
        key_input_layout.addWidget(key_label)
        key_input_layout.addWidget(self.key_input)
        add_layout.addLayout(key_input_layout)
        
        # 按键监听按钮
        listen_layout = QHBoxLayout()
        listen_layout.setSpacing(5)
        self.listen_key_btn = QPushButton("🎧 监听按键")
        self.listen_key_btn.setMinimumHeight(26)
        self.listen_key_btn.setToolTip("点击后按下要添加的按键")
        self.listen_key_btn.clicked.connect(self._start_key_listening)
        
        self.manual_input_btn = QPushButton("✏️ 手动输入")
        self.manual_input_btn.setMinimumHeight(26)
        self.manual_input_btn.setToolTip("手动输入按键名称")
        self.manual_input_btn.clicked.connect(self._toggle_manual_input)
        
        listen_layout.addWidget(self.listen_key_btn)
        listen_layout.addWidget(self.manual_input_btn)
        add_layout.addLayout(listen_layout)
        
        # 延迟输入
        delay_input_layout = QHBoxLayout()
        delay_input_layout.setSpacing(5)
        delay_label = QLabel("延迟:")
        delay_label.setMinimumWidth(40)
        delay_label.setStyleSheet("color: #555;")
        self.delay_input = QSpinBox()
        self.delay_input.setRange(0, 500)
        self.delay_input.setValue(50)
        self.delay_input.setSuffix(" ms")
        self.delay_input.setMinimumHeight(24)
        self.delay_input.setToolTip("该按键的前置延迟时间")
        delay_input_layout.addWidget(delay_label)
        delay_input_layout.addWidget(self.delay_input)
        delay_input_layout.addStretch()
        add_layout.addLayout(delay_input_layout)
        
        # 添加按钮
        self.add_key_btn = QPushButton("➕ 添加按键")
        self.add_key_btn.setMinimumHeight(28)
        self.add_key_btn.clicked.connect(self._add_key)
        self.add_key_btn.setEnabled(False)  # 初始禁用，需要先获取按键
        self.add_key_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        add_layout.addWidget(self.add_key_btn)
        
        right_layout.addWidget(add_group)

        # 编辑区域
        edit_group = QFrame()
        edit_layout = QVBoxLayout(edit_group)
        edit_layout.setContentsMargins(8, 8, 8, 8)
        edit_layout.setSpacing(5)
        edit_group.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 6px; background-color: #fafafa; }")
        
        edit_title = QLabel("✏️ 编辑选中按键")
        edit_title.setStyleSheet("font-weight: bold; color: #333; font-size: 10pt; margin-bottom: 3px;")
        edit_layout.addWidget(edit_title)
        
        # 当前编辑按键显示
        self.current_key_label = QLabel("请先选择一个按键")
        self.current_key_label.setStyleSheet("""
            QLabel {
                background-color: #e3f2fd;
                border: 1px solid #2196f3;
                border-radius: 4px;
                padding: 6px 10px;
                font-weight: bold;
                color: #1976d2;
                font-size: 9pt;
                margin: 2px 0px;
            }
        """)
        self.current_key_label.setWordWrap(True)
        edit_layout.addWidget(self.current_key_label)
        
        # 编辑延迟
        edit_delay_layout = QHBoxLayout()
        edit_delay_layout.setSpacing(5)
        edit_delay_label = QLabel("延迟:")
        edit_delay_label.setMinimumWidth(40)
        edit_delay_label.setStyleSheet("color: #555;")
        self.edit_delay_input = QSpinBox()
        self.edit_delay_input.setRange(0, 500)
        self.edit_delay_input.setValue(50)
        self.edit_delay_input.setSuffix(" ms")
        self.edit_delay_input.setMinimumHeight(24)
        self.edit_delay_input.setEnabled(False)
        self.edit_delay_input.valueChanged.connect(self._update_selected_key_delay)
        edit_delay_layout.addWidget(edit_delay_label)
        edit_delay_layout.addWidget(self.edit_delay_input)
        edit_delay_layout.addStretch()
        edit_layout.addLayout(edit_delay_layout)

        # 操作按钮
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(5)
        self.remove_key_btn = QPushButton("🗑️ 删除")
        self.remove_key_btn.setMinimumHeight(26)
        self.remove_key_btn.setEnabled(False)
        self.remove_key_btn.clicked.connect(self._remove_selected_key)
        self.remove_key_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        
        self.reset_btn = QPushButton("🔄 重置")
        self.reset_btn.setMinimumHeight(26)
        self.reset_btn.clicked.connect(self._reset_to_default)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e68900;
            }
        """)
        
        buttons_layout.addWidget(self.remove_key_btn)
        buttons_layout.addWidget(self.reset_btn)
        edit_layout.addLayout(buttons_layout)
        
        right_layout.addWidget(edit_group)
        right_layout.addStretch()
        keys_layout.addLayout(right_layout, 1)

        group_layout.addWidget(keys_frame)

        # 预设配置
        presets_frame = QFrame()
        presets_layout = QVBoxLayout(presets_frame)
        presets_layout.setContentsMargins(0, 8, 0, 0)
        presets_layout.setSpacing(5)

        presets_title = QLabel("🚀 快速预设")
        presets_title.setStyleSheet("font-weight: bold; color: #333; font-size: 10pt;")
        presets_layout.addWidget(presets_title)
        
        presets_buttons_layout = QHBoxLayout()
        presets_buttons_layout.setSpacing(8)
        
        dodge_btn = QPushButton("⚡ 闪避模式")
        dodge_btn.setMinimumHeight(28)
        dodge_btn.setToolTip("只配置空格键闪避 (50ms延迟)")
        dodge_btn.clicked.connect(lambda: self._apply_preset({'space': 50}))
        dodge_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        
        mine_btn = QPushButton("💣 布雷模式")
        mine_btn.setMinimumHeight(28)
        mine_btn.setToolTip("只配置右键布雷 (50ms延迟)")
        mine_btn.clicked.connect(lambda: self._apply_preset({'right_mouse': 50}))
        mine_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #F57C00;
            }
        """)
        
        combat_btn = QPushButton("⚔️ 战斗模式")
        combat_btn.setMinimumHeight(28)
        combat_btn.setToolTip("配置空格键闪避 + 右键技能 (各50ms延迟)")
        combat_btn.clicked.connect(lambda: self._apply_preset({'space': 50, 'right_mouse': 50}))
        combat_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)
        
        presets_buttons_layout.addWidget(dodge_btn)
        presets_buttons_layout.addWidget(mine_btn)
        presets_buttons_layout.addWidget(combat_btn)
        presets_buttons_layout.addStretch()
        
        presets_layout.addLayout(presets_buttons_layout)
        group_layout.addWidget(presets_frame)

        layout.addWidget(group)

        # 连接信号
        self.keys_list.itemSelectionChanged.connect(self._on_selection_changed)

    def _load_default_keys(self):
        """加载默认按键配置"""
        default_config = {
            'space': 50,
            'right_mouse': 50
        }
        self.priority_keys_config = default_config.copy()
        self._update_keys_display()

    def _update_keys_display(self):
        """更新按键列表显示"""
        self.keys_list.clear()
        
        for key, delay in self.priority_keys_config.items():
            item = QListWidgetItem(self._format_key_display(key, delay))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.keys_list.addItem(item)

    def _format_key_display(self, key: str, delay: int) -> str:
        """格式化按键显示名称"""
        key_names = {
            'space': '空格键',
            'left_mouse': '左键',
            'right_mouse': '右键',
            'middle_mouse': '中键',
            'ctrl': 'Ctrl键',
            'shift': 'Shift键',
            'alt': 'Alt键',
            'tab': 'Tab键',
            'esc': 'Esc键',
            'enter': '回车键',
        }
        
        display_name = key_names.get(key, f'{key.upper()}键')
        return f"{display_name} ({key}) - {delay}ms"

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
        """添加新的优先级按键"""
        key_name = self.key_input.text().strip()
        delay = self.delay_input.value()
        
        if not key_name:
            QMessageBox.warning(self, "警告", "请输入按键名称")
            return
        
        key_name = self._normalize_key_name(key_name)
        
        if key_name in self.priority_keys_config:
            QMessageBox.information(self, "提示", f"按键 '{key_name}' 已经在优先级列表中")
            return
        
        self.priority_keys_config[key_name] = delay
        self._update_keys_display()
        
        # 清空输入框
        self.key_input.clear()
        self.delay_input.setValue(50)
        
        LOG_INFO(f"[优先级按键] 添加按键: {key_name} (延迟: {delay}ms)")

    def _remove_selected_key(self):
        """删除选中的按键"""
        current_item = self.keys_list.currentItem()
        if not current_item:
            return
        
        key_name = current_item.data(Qt.ItemDataRole.UserRole)
        if key_name in self.priority_keys_config:
            del self.priority_keys_config[key_name]
            self._update_keys_display()
            self._on_selection_changed()  # 更新UI状态
            LOG_INFO(f"[优先级按键] 删除按键: {key_name}")

    def _update_selected_key_delay(self):
        """更新选中按键的延迟"""
        current_item = self.keys_list.currentItem()
        if not current_item:
            return
        
        key_name = current_item.data(Qt.ItemDataRole.UserRole)
        new_delay = self.edit_delay_input.value()
        
        if key_name in self.priority_keys_config:
            self.priority_keys_config[key_name] = new_delay
            self._update_keys_display()
            
            # 重新选中相同的项
            for i in range(self.keys_list.count()):
                item = self.keys_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == key_name:
                    self.keys_list.setCurrentItem(item)
                    break
            
            # 更新当前编辑按键标签
            display_text = self._get_key_display_name(key_name)
            self.current_key_label.setText(f"正在编辑: {display_text} ({new_delay}ms)")
                    
            LOG_INFO(f"[优先级按键] 更新按键延迟: {key_name} -> {new_delay}ms")

    def _reset_to_default(self):
        """重置为默认配置"""
        reply = QMessageBox.question(
            self, "确认重置", 
            "确定要重置为默认配置吗？\n默认配置：space (50ms), right_mouse (50ms)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self._load_default_keys()
            self._on_selection_changed()  # 更新UI状态
            LOG_INFO("[优先级按键] 重置为默认配置")

    def _apply_preset(self, preset_config: Dict[str, int]):
        """应用预设配置"""
        self.priority_keys_config = preset_config.copy()
        self._update_keys_display()
        self._on_selection_changed()  # 更新UI状态
        LOG_INFO(f"[优先级按键] 应用预设: {preset_config}")

    def _on_selection_changed(self):
        """处理选择变化"""
        current_item = self.keys_list.currentItem()
        has_selection = bool(current_item)
        
        # 更新删除按钮状态
        self.remove_key_btn.setEnabled(has_selection)
        
        # 更新编辑区域状态
        self.edit_delay_input.setEnabled(has_selection)
        
        if has_selection:
            key_name = current_item.data(Qt.ItemDataRole.UserRole)
            if key_name in self.priority_keys_config:
                delay = self.priority_keys_config[key_name]
                
                # 更新延迟输入框
                self.edit_delay_input.blockSignals(True)  # 防止循环信号
                self.edit_delay_input.setValue(delay)
                self.edit_delay_input.blockSignals(False)
                
                # 更新当前编辑按键显示
                display_text = self._get_key_display_name(key_name)
                delay_text = f"{delay}ms"
                self.current_key_label.setText(f"正在编辑: {display_text} ({delay_text})")
                self.current_key_label.setStyleSheet("""
                    QLabel {
                        background-color: #e8f5e8;
                        border: 1px solid #4caf50;
                        border-radius: 4px;
                        padding: 6px 10px;
                        font-weight: bold;
                        color: #2e7d32;
                        font-size: 9pt;
                        margin: 2px 0px;
                    }
                """)
        else:
            self.edit_delay_input.setValue(50)
            self.current_key_label.setText("请先选择一个按键")
            self.current_key_label.setStyleSheet("""
                QLabel {
                    background-color: #f5f5f5;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    padding: 6px 10px;
                    font-weight: normal;
                    color: #666;
                    font-size: 9pt;
                    margin: 2px 0px;
                }
            """)

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            "enabled": self.widgets["enabled"].isChecked(),
            "keys_config": self.priority_keys_config.copy()
        }

    def set_config(self, config: Dict[str, Any]):
        """设置配置"""
        if "enabled" in config:
            self.widgets["enabled"].setChecked(config["enabled"])
        
        # 兼容旧格式 (keys + delay_ms)
        if "keys" in config and "delay_ms" in config:
            keys = config["keys"]
            delay_ms = config["delay_ms"]
            self.priority_keys_config = {key: delay_ms for key in keys}
            self._update_keys_display()
        # 新格式 (keys_config)
        elif "keys_config" in config:
            self.priority_keys_config = dict(config["keys_config"])
            self._update_keys_display()

    def get_priority_keys(self) -> Set[str]:
        """获取优先级按键集合"""
        if self.widgets["enabled"].isChecked():
            return set(self.priority_keys_config.keys())
        return set()
    
    def get_priority_keys_with_delay(self) -> Dict[str, int]:
        """获取优先级按键配置（包含延迟）"""
        if self.widgets["enabled"].isChecked():
            return self.priority_keys_config.copy()
        return {}

    def _start_key_listening(self):
        """开始监听按键"""
        if not PYNPUT_AVAILABLE:
            QMessageBox.warning(self, "功能不可用", "pynput 库不可用，无法监听按键。\n请使用手动输入功能。")
            return
            
        if self._key_listening:
            return
            
        self._key_listening = True
        self.listen_key_btn.setText("⏹️ 停止监听")
        self.listen_key_btn.setStyleSheet("QPushButton { background-color: #ff6b6b; color: white; }")
        self.key_input.setText("正在监听按键，请按下要添加的按键...")
        self.key_input.setStyleSheet("QLineEdit { background-color: #fff3cd; }")
        
        try:
            # 启动键盘和鼠标监听
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_captured,
                suppress=False
            )
            self._mouse_listener = mouse.Listener(
                on_click=self._on_mouse_captured,
                suppress=False
            )
            
            self._keyboard_listener.start()
            self._mouse_listener.start()
            
            # 设置5秒超时
            QTimer.singleShot(5000, self._stop_key_listening_timeout)
            
        except Exception as e:
            LOG_ERROR(f"[按键监听] 启动失败: {e}")
            QMessageBox.warning(self, "启动失败", f"无法启动按键监听: {e}")
            self._stop_key_listening()

    def _stop_key_listening(self):
        """停止监听按键"""
        if not self._key_listening:
            return
            
        self._key_listening = False
        self.listen_key_btn.setText("🎧 监听按键")
        self.listen_key_btn.setStyleSheet("")
        
        # 停止监听器
        try:
            if self._keyboard_listener:
                self._keyboard_listener.stop()
                self._keyboard_listener = None
            if self._mouse_listener:
                self._mouse_listener.stop()
                self._mouse_listener = None
        except Exception as e:
            LOG_ERROR(f"[按键监听] 停止失败: {e}")
        
        # 如果没有捕获到按键，恢复原始状态
        if self.key_input.text() in ["正在监听按键，请按下要添加的按键...", "监听超时，请重试或使用手动输入"]:
            self.key_input.setText("")
            self.key_input.setStyleSheet("")
            self.add_key_btn.setEnabled(False)

    def _stop_key_listening_timeout(self):
        """监听超时处理"""
        if self._key_listening:
            self.key_input.setText("监听超时，请重试或使用手动输入")
            self.key_input.setStyleSheet("QLineEdit { background-color: #f8d7da; }")
            self._stop_key_listening()

    def _on_key_captured(self, key):
        """捕获键盘按键"""
        if not self._key_listening:
            return
            
        try:
            key_name = self._get_key_name(key)
            if key_name:
                self._set_captured_key(key_name)
                self._stop_key_listening()
        except Exception as e:
            LOG_ERROR(f"[按键监听] 键盘按键处理失败: {e}")

    def _on_mouse_captured(self, x, y, button, pressed):
        """捕获鼠标按键"""
        if not self._key_listening or not pressed:
            return
            
        try:
            button_name = self._get_button_name(button)
            if button_name:
                self._set_captured_key(button_name)
                self._stop_key_listening()
        except Exception as e:
            LOG_ERROR(f"[按键监听] 鼠标按键处理失败: {e}")

    def _set_captured_key(self, key_name: str):
        """设置捕获到的按键"""
        self.key_input.setText(key_name)
        self.key_input.setStyleSheet("QLineEdit { background-color: #d4edda; }")
        self.add_key_btn.setEnabled(True)
        LOG_INFO(f"[按键监听] 捕获到按键: {key_name}")

    def _get_key_name(self, key) -> str:
        """获取按键名称"""
        try:
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            elif hasattr(key, 'name'):
                # 特殊按键映射
                key_mapping = {
                    'space': 'space',
                    'ctrl_l': 'ctrl', 'ctrl_r': 'ctrl',
                    'shift_l': 'shift', 'shift_r': 'shift', 
                    'alt_l': 'alt', 'alt_r': 'alt',
                    'tab': 'tab',
                    'esc': 'esc',
                    'enter': 'enter',
                    'backspace': 'backspace',
                    'delete': 'delete'
                }
                return key_mapping.get(key.name, key.name or "unknown")
        except Exception as e:
            LOG_ERROR(f"[按键监听] 获取按键名称失败: {e}")
        return ""

    def _get_button_name(self, button) -> str:
        """获取鼠标按钮名称"""
        try:
            button_mapping = {
                Button.left: 'left_mouse',
                Button.right: 'right_mouse', 
                Button.middle: 'middle_mouse'
            }
            return button_mapping.get(button, "")
        except Exception as e:
            LOG_ERROR(f"[按键监听] 获取鼠标按钮名称失败: {e}")
        return ""

    def _toggle_manual_input(self):
        """切换手动输入模式"""
        if self.key_input.isReadOnly():
            # 切换到手动输入模式
            self.key_input.setReadOnly(False)
            self.key_input.setPlaceholderText("手动输入按键名称 (如: space, right_mouse, ctrl)")
            self.key_input.setText("")
            self.key_input.setStyleSheet("")
            self.manual_input_btn.setText("🎧 监听模式")
            self.listen_key_btn.setEnabled(False)
            self.add_key_btn.setEnabled(True)
            # 连接文本变化信号
            self.key_input.textChanged.connect(self._on_manual_input_changed)
        else:
            # 切换回监听模式
            self.key_input.setReadOnly(True)
            self.key_input.setPlaceholderText("点击'监听按键'后按下要添加的按键")
            self.key_input.setText("")
            self.key_input.setStyleSheet("")
            self.manual_input_btn.setText("✏️ 手动输入")
            self.listen_key_btn.setEnabled(True)
            self.add_key_btn.setEnabled(False)
            # 断开文本变化信号
            try:
                self.key_input.textChanged.disconnect(self._on_manual_input_changed)
            except:
                pass

    def _on_manual_input_changed(self):
        """手动输入文本变化处理"""
        text = self.key_input.text().strip()
        self.add_key_btn.setEnabled(bool(text))

    def closeEvent(self, event):
        """组件关闭时清理资源"""
        self._stop_key_listening()
        super().closeEvent(event)

    def _get_key_display_name(self, key: str) -> str:
        """获取按键的友好显示名称"""
        key_names = {
            'space': '空格键',
            'left_mouse': '左键',
            'right_mouse': '右键',
            'middle_mouse': '中键',
            'ctrl': 'Ctrl键',
            'shift': 'Shift键',
            'alt': 'Alt键',
            'tab': 'Tab键',
            'esc': 'Esc键',
            'enter': '回车键',
            'backspace': '退格键',
            'delete': '删除键',
        }
        return key_names.get(key, f'{key.upper()}键')

    def __del__(self):
        """析构函数，确保清理资源"""
        try:
            self._stop_key_listening()
        except:
            pass