#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主窗口类 - 从main.py拆分出来的核心UI类"""

import os
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QMessageBox,
    QFileDialog,
    QFrame,
)
from PySide6.QtCore import Qt, QTimer
from typing import Dict, Optional, Any

# Windows API常量
WM_COPYDATA = 0x004A

from ..core.macro_engine import MacroState
from ..core.event_bus import event_bus
from .status_window import OSDStatusWindow
from .debug_osd_window import DebugOsdWindow # Import DebugOsdWindow
from .skill_config_widget import SimplifiedSkillWidget
from .ui_components import (
    StatusStrings,
    TopControlsWidget,
    TimingSettingsWidget,
    WindowActivationWidget,
    StationaryModeWidget,
    AffixRerollWidget,
    SkillConfigWidget,
    PathfindingWidget,
    ResourceManagementWidget,
    PriorityKeysWidget,
)
from ..utils.sound_manager import SoundManager
from ..utils.debug_log import LOG, LOG_INFO, LOG_ERROR


class GameSkillConfigUI(QMainWindow):
    """主窗口UI类"""

    def __init__(self, macro_engine, sound_manager: SoundManager):
        super().__init__()

        self.macro_engine = macro_engine
        self.sound_manager = sound_manager
        self.osd_status_window: Optional[OSDStatusWindow] = None
        self.skill_widgets: Dict[str, SimplifiedSkillWidget] = {}

        self._skills_config = {}
        self._global_config = {}
        self._updating_ui = False

        self.top_controls = None
        self.timing_settings = None
        self.window_activation = None
        self.stationary_mode = None
        self.affix_reroll = None
        self.skill_config = None
        self.pathfinding_settings = None
        self.resource_management = None
        self.status_label = None

        self._setup_window()
        self._create_widgets()
        self._connect_to_engine()
        self._setup_hotkeys()
        self._load_initial_config_to_ui()

    def _setup_window(self):
        # 设置窗口标题供AHK查找（显示名称保持中文，但内部标识用于AHK）
        self.setWindowTitle("TorchLightAssistant_MainWindow_12345")
        self.setGeometry(100, 100, 1200, 800)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setSpacing(8)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        from .styles import get_modern_style
        self.setStyleSheet(get_modern_style())

    def _create_widgets(self):
        self.top_controls = TopControlsWidget()
        self.top_controls.mode_combo.currentTextChanged.connect(self._on_mode_selection_changed)
        self.top_controls.save_btn.clicked.connect(self._save_config_file)
        self.top_controls.load_btn.clicked.connect(self._load_config_file)
        # 连接DEBUG MODE复选框信号
        self.top_controls.debug_mode_checkbox.stateChanged.connect(self._on_debug_mode_changed)
        self.main_layout.addWidget(self.top_controls)

        self._create_tab_widget()
        self._create_control_buttons()  # 恢复控制按钮
        self._create_status_bar()       # 恢复状态栏

        self.osd_status_window = OSDStatusWindow(parent=self)
        self.debug_osd_window = DebugOsdWindow() # 实例化DebugOsdWindow，不设父级，使其独立

        # 现在DEBUG OSD窗口已经创建，可以连接事件订阅
        if self.debug_osd_window:
            event_bus.subscribe("debug_osd_show", self.debug_osd_window.show)
            event_bus.subscribe("debug_osd_hide", self.debug_osd_window.hide)

    def _create_tab_widget(self):
        self.tab_widget = QTabWidget()
        self.skill_config = SkillConfigWidget()
        self.tab_widget.addTab(self.skill_config, "技能配置")
        self.window_activation = WindowActivationWidget()
        self._connect_window_activation_signals()
        self.tab_widget.addTab(self.window_activation, "目标窗口")
        self.timing_settings = TimingSettingsWidget()
        self.tab_widget.addTab(self.timing_settings, "时间间隔/声音")
        self.stationary_mode = StationaryModeWidget()
        self.tab_widget.addTab(self.stationary_mode, "原地模式")
        self.affix_reroll = AffixRerollWidget()
        self.affix_reroll.ocr_load_button.clicked.connect(self._on_load_ocr_clicked)
        self.tab_widget.addTab(self.affix_reroll, "洗练")

        self.pathfinding_settings = PathfindingWidget()
        self.tab_widget.addTab(self.pathfinding_settings, "寻路")

        self.resource_management = ResourceManagementWidget()
        self.resource_management.set_main_window(self)
        self.tab_widget.addTab(self.resource_management, "智能药剂")

        # 优先级按键配置
        self.priority_keys_widget = PriorityKeysWidget()
        self.tab_widget.addTab(self.priority_keys_widget, "优先级按键")

        self.main_layout.addWidget(self.tab_widget, 1)

    def _create_control_buttons(self):
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        info_label = QLabel("F8: 隐藏/启动 | Z: 暂停/恢复 | F7: 洗练 | F9: 寻路")
        info_label.setStyleSheet("color: gray; font-size: 8pt;")
        layout.addWidget(info_label)
        layout.addStretch()
        toggle_btn = QPushButton("隐藏界面并启动 (F8)")
        toggle_btn.setMaximumHeight(28)
        toggle_btn.clicked.connect(self._toggle_visibility_and_macro)
        layout.addWidget(toggle_btn)
        self.main_layout.addWidget(frame)

    def _create_status_bar(self):
        self.status_label = QLabel(StatusStrings.READY)
        self.status_label.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_label.setMaximumHeight(24)
        self.status_label.setStyleSheet("font-size: 8pt; padding-left: 5px;")
        self.main_layout.addWidget(self.status_label)

    def _connect_to_engine(self):
        event_bus.subscribe("engine:state_changed", self._on_macro_state_changed)
        event_bus.subscribe("engine:status_updated", self._on_macro_status_updated)
        event_bus.subscribe("engine:config_updated", self._on_config_update)
        event_bus.subscribe("affix_reroll:status_updated", self._on_affix_reroll_status_updated)
        event_bus.subscribe("affix_reroll:success", self._on_affix_reroll_success)
        event_bus.subscribe("affix_reroll:hide_ui", self._on_affix_reroll_hide_ui)
        event_bus.subscribe("affix_reroll:show_ui", self._on_affix_reroll_show_ui)
        event_bus.subscribe("ocr:init_success", self._on_ocr_init_success)
        event_bus.subscribe("ocr:init_failed", self._on_ocr_init_failed)
        # DEBUG OSD窗口的事件订阅已移到_create_widgets方法中处理

    def _setup_hotkeys(self):
        event_bus.subscribe("hotkey:f8_system_toggle", self._toggle_visibility_and_macro)

    def _load_initial_config_to_ui(self):
        if self.top_controls:
            self.top_controls.set_current_config("default.json")
        event_bus.publish("ui:request_current_config")

    def _perform_macro_state_changed_ui(self, new_state: MacroState):
        LOG_INFO(f"[UI] 执行UI状态变更: {new_state}")
        # 声音逻辑集中于此, 确保只有主状态变更才播放声音
        if new_state == MacroState.STOPPED:
            self.sound_manager.play("goodbye")
        elif new_state == MacroState.READY:
            self.sound_manager.play("hello")
        elif new_state == MacroState.RUNNING:
            self.sound_manager.play("resume")
        elif new_state == MacroState.PAUSED:
            self.sound_manager.play("pause")


        # UI可见性逻辑 (由MacroEngine控制OSD显示/隐藏)
        if new_state == MacroState.STOPPED:
            LOG_INFO("[UI] STOPPED状态 - 隐藏OSD，显示主窗口")
            try:
                self.osd_status_window.hide()
                LOG_INFO("[UI] OSD已隐藏")
            except Exception as e:
                LOG_ERROR(f"[UI] 隐藏OSD失败: {e}")
            
            # 🎯 通知AHK切换到主窗口
            try:
                self.macro_engine.input_handler.set_python_window_state("main")
                LOG_INFO("[UI] 已通知AHK切换到主窗口")
            except Exception as e:
                LOG_ERROR(f"[UI] 通知AHK切换窗口失败: {e}")
            
            if not self.isVisible():
                try:
                    self._show_main_window()
                    LOG_INFO("[UI] 主窗口已显示")
                except Exception as e:
                    LOG_ERROR(f"[UI] 显示主窗口失败: {e}")
        else:
            LOG_INFO(f"[UI] {new_state}状态 - 显示OSD，隐藏主窗口")
            try:
                LOG_INFO(f"[UI] 准备显示OSD，当前主窗口可见: {self.isVisible()}")
                self.osd_status_window.show()
                LOG_INFO("[UI] OSD.show()已调用")
            except Exception as e:
                LOG_ERROR(f"[UI] 显示OSD失败: {e}")
            
            # 🎯 通知AHK切换到OSD窗口  
            try:
                self.macro_engine.input_handler.set_python_window_state("osd")
                LOG_INFO("[UI] 已通知AHK切换到OSD窗口")
            except Exception as e:
                LOG_ERROR(f"[UI] 通知AHK切换窗口失败: {e}")
            
            if self.isVisible():
                try:
                    LOG_INFO("[UI] 准备隐藏主窗口")
                    self.hide()
                    LOG_INFO("[UI] 主窗口已隐藏")
                except Exception as e:
                    LOG_ERROR(f"[UI] 隐藏主窗口失败: {e}")

    def _on_macro_state_changed(self, new_state: MacroState, old_state: MacroState):
        LOG_INFO(f"[UI] 收到状态变更事件: {old_state} → {new_state}")
        LOG_INFO(f"[UI] 准备调度UI更新，使用QTimer.singleShot")
        # 使用QTimer避免在锁内执行UI操作导致死锁
        # 必须捕获new_state的值，避免lambda闭包问题
        state = new_state
        
        def delayed_ui_update():
            LOG_INFO(f"[UI] QTimer回调被执行，状态: {state}")
            self._perform_macro_state_changed_ui(state)
        
        QTimer.singleShot(0, delayed_ui_update)
        LOG_INFO(f"[UI] QTimer.singleShot已调度")

    def _perform_macro_status_updated_ui(self, engine_state: Dict[str, Any]):
        # 此函数现在只负责更新文本和OSD, 不播放声音
        state = engine_state.get("state", MacroState.STOPPED)
        queue_len = engine_state.get("queue_length", 0)
        stationary_mode = engine_state.get("stationary_mode", False)
        force_move_active = engine_state.get("force_move_active", False)

        state_text = {
            MacroState.STOPPED: StatusStrings.STOPPED,
            MacroState.READY: StatusStrings.READY,
            MacroState.RUNNING: StatusStrings.RUNNING,
            MacroState.PAUSED: StatusStrings.PAUSED,
        }.get(state, "未知")

        if state == MacroState.RUNNING:
            if force_move_active:
                state_text = StatusStrings.RUNNING_INTERACTION
            elif stationary_mode:
                state_text = StatusStrings.RUNNING_STATIONARY
        elif state == MacroState.PAUSED:
            if force_move_active:
                state_text = StatusStrings.PAUSED_INTERACTION
            elif stationary_mode:
                state_text = StatusStrings.PAUSED_STATIONARY

        self.status_label.setText(f"状态: {state_text} | 按键队列: {queue_len}")
        if self.osd_status_window:
            color = {"STOPPED": "red", "READY": "yellow", "RUNNING": "lime", "PAUSED": "yellow", "DEBUG": "cyan"}.get(state.name, "white")
            self.osd_status_window.update_status(state_text, color)

    def _on_macro_status_updated(self, engine_state: Dict[str, Any]):
        QTimer.singleShot(0, lambda: self._perform_macro_status_updated_ui(engine_state))


    def _on_config_update(self, skills_config: Dict[str, Any], global_config: Dict[str, Any]):
        LOG_INFO(f"[UI] 接收到 engine:config_updated 事件。skills_config: {skills_config}")
        LOG_INFO(f"[UI] global_config: {global_config}")
        if self._updating_ui: return
        self._skills_config = skills_config
        self._global_config = global_config
        self.sound_manager.update_config(global_config)

        # 更新DEBUG MODE复选框状态
        debug_mode_enabled = global_config.get("debug_mode", {}).get("enabled", False)
        if self.top_controls and hasattr(self.top_controls, 'debug_mode_checkbox'):
            self.top_controls.debug_mode_checkbox.setChecked(debug_mode_enabled)
            LOG_INFO(f"[UI] DEBUG MODE复选框状态已更新为: {debug_mode_enabled}")

        LOG_INFO("[UI] 调用 _refresh_all_widgets() 刷新UI。")
        self._refresh_all_widgets()

    # 洗练相关事件处理
    def _perform_affix_reroll_ui_update(self, osd_text: str, osd_color: str):
        if self.osd_status_window:
            self.osd_status_window.update_status(osd_text, osd_color)

    def _on_affix_reroll_status_updated(self, status_data: Dict[str, Any]):
        try:
            is_running = status_data.get("is_running", False)
            current_attempts = status_data.get("current_attempts", 0)
            current_state = status_data.get("current_state", "idle")
            matched_affix = status_data.get("matched_affix", "")
            error_message = status_data.get("error_message", "")
            osd_text, osd_color = "", "#4a90e2"
            if error_message:
                osd_text, osd_color = f"错误: {error_message}", "red"
            elif is_running:
                state_map = {"idle": "准备中...", "初始界面": "初始界面\n准备点击附魔...", "词缀选择": "词缀选择\n正在查找目标...", "确认/关闭": "确认/关闭\n准备开始下一轮...", "未知": "未知状态\n尝试恢复..."}
                targets = self._global_config.get("affix_reroll", {}).get("target_affixes", [])
                base_text = state_map.get(current_state, "未知状态")
                if current_state == "词缀选择" and targets:
                    display_targets = ", ".join(targets)
                    if len(display_targets) > 40: display_targets = display_targets[:37] + "..."
                    base_text = f"词缀选择\n正在查找: [{display_targets}]"
                osd_text = f"{base_text}\n(第 {current_attempts} 次尝试)"
            else:
                if matched_affix:
                    osd_text, osd_color = f"洗练成功!\n找到: {matched_affix}", "lime"
                else:
                    osd_text, osd_color = "洗练已停止\n等待F7启动", "#E0E0E0"
            QTimer.singleShot(0, lambda: self._perform_affix_reroll_ui_update(osd_text, osd_color))
        except Exception as e:
            LOG_ERROR(f"[UI] 更新洗练状态异常: {e}")

    def _perform_affix_reroll_success_ui(self, success_data: Dict[str, Any]):
        if not self.isVisible(): self.show()
        self.raise_()
        self.activateWindow()
        if self.osd_status_window: self.osd_status_window.hide()
        title = "洗练成功！"
        message = f"找到目标词缀：{success_data.get('matched_affix', '')}\n\n尝试次数：{success_data.get('attempts', 0)} 次"
        QTimer.singleShot(100, lambda: QMessageBox.information(self, title, message))

    def _on_affix_reroll_success(self, success_data: Dict[str, Any]):
        QTimer.singleShot(0, lambda: self._perform_affix_reroll_success_ui(success_data))

    def _perform_affix_reroll_hide_ui(self):
        if self.isVisible(): self.hide()
        if self.osd_status_window: self.osd_status_window.show()

    def _on_affix_reroll_hide_ui(self):
        QTimer.singleShot(0, self._perform_affix_reroll_hide_ui)

    def _perform_affix_reroll_show_ui(self):
        if self.osd_status_window: self.osd_status_window.hide()
        if not self.isVisible(): self.show()

    def _on_affix_reroll_show_ui(self):
        QTimer.singleShot(0, self._perform_affix_reroll_show_ui)

    # OCR相关方法
    def _on_load_ocr_clicked(self):
        try:
            LOG_INFO("[UI] 用户点击加载OCR引擎按钮")
            if self.affix_reroll:
                self.affix_reroll.ocr_load_button.setEnabled(False)
                self.affix_reroll.ocr_load_button.setText("正在加载...")
                self.affix_reroll.update_ocr_status("正在初始化OCR引擎...", "#4a90e2")
            from ..utils.paddle_ocr_manager import get_paddle_ocr_manager
            ocr_manager = get_paddle_ocr_manager()
            if ocr_manager.get_initialization_status()["initialized"]:
                self._on_ocr_init_success()
            elif not ocr_manager.get_initialization_status()["initializing"]:
                ocr_manager.start_async_initialization()
        except Exception as e:
            LOG_ERROR(f"[UI] 加载OCR引擎时出错: {e}")
            self._on_ocr_init_failed({"error": str(e)})
            
    def _on_ocr_init_success(self):
        QTimer.singleShot(0, lambda: self.affix_reroll.update_ocr_status("OCR引擎已就绪", "#4CAF50") if self.affix_reroll else None)

    def _on_ocr_init_failed(self, error_data=None):
        error_msg = error_data.get("error", "未知错误") if error_data else "初始化失败"
        QTimer.singleShot(0, lambda: self.affix_reroll.update_ocr_status(f"OCR引擎加载失败: {error_msg}", "#f44336") if self.affix_reroll else None)

    def _update_ocr_status_display(self):
        if not self.affix_reroll: return
        try:
            from ..utils.paddle_ocr_manager import get_paddle_ocr_manager
            status = get_paddle_ocr_manager().get_initialization_status()
            if status["initialized"]:
                self.affix_reroll.ocr_load_button.setText("重新加载OCR")
                self.affix_reroll.ocr_load_button.setEnabled(True)
                self.affix_reroll.update_ocr_status("OCR引擎已就绪", "#4CAF50")
            elif status["initializing"]:
                self.affix_reroll.ocr_load_button.setText("正在加载...")
                self.affix_reroll.ocr_load_button.setEnabled(False)
                self.affix_reroll.update_ocr_status("正在初始化OCR引擎...", "#4a90e2")
            elif status["error"]:
                self.affix_reroll.ocr_load_button.setText("重试加载OCR")
                self.affix_reroll.ocr_load_button.setEnabled(True)
                self.affix_reroll.update_ocr_status(f"OCR引擎加载失败: {status['error']}", "#f44336")
            else:
                self.affix_reroll.ocr_load_button.setText("加载OCR引擎")
                self.affix_reroll.ocr_load_button.setEnabled(True)
                self.affix_reroll.update_ocr_status("OCR引擎未加载", "#888888")
        except Exception as e:
            LOG_ERROR(f"[UI] 更新OCR状态显示时出错: {e}")

    def _show_main_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _refresh_all_widgets(self):
        self._updating_ui = True
        try:
            if self.top_controls: self.top_controls.update_from_config(self._global_config)
            if self.timing_settings: self.timing_settings.update_from_config(self._global_config)
            if self.window_activation: self.window_activation.update_from_config(self._global_config)
            if self.stationary_mode: self.stationary_mode.update_from_config(self._global_config)
            if self.affix_reroll: 
                self.affix_reroll.update_from_config(self._global_config)
                self._update_ocr_status_display()
            if self.pathfinding_settings:
                self.pathfinding_settings.update_from_config(self._global_config)
            if self.resource_management:
                self.resource_management.update_from_config(self._global_config)
            if self.priority_keys_widget:
                priority_keys_config = self._global_config.get("priority_keys", {})
                self.priority_keys_widget.set_config(priority_keys_config)
            if self.skill_config:
                LOG_INFO("[UI] 刷新 SkillConfigWidget...")
                self.skill_config.update_from_config(self._skills_config, self._global_config)
                is_sequence = self._global_config.get("sequence_enabled", False)
                self._on_mode_selection_changed("序列" if is_sequence else "技能")
                LOG_INFO("[UI] SkillConfigWidget 刷新完成。")
        finally:
            self._updating_ui = False

    def _toggle_visibility_and_macro(self):
        full_config = self._gather_current_config_from_ui()
        event_bus.publish("ui:sync_and_toggle_state_requested", full_config)

    def _on_mode_selection_changed(self, text: str):
        if self.skill_config:
            is_sequence_mode = text == "序列"
            self.skill_config.skill_frame.setVisible(not is_sequence_mode)
            self.skill_config.sequence_frame.setVisible(is_sequence_mode)

    def _on_debug_mode_changed(self, state: int):
        """处理DEBUG MODE复选框状态变化"""
        enabled = state == 2  # Qt.CheckState.Checked == 2
        try:
            self.macro_engine.set_debug_mode(enabled)
            LOG_INFO(f"[UI] DEBUG MODE设置为: {enabled}")
        except Exception as e:
            LOG_ERROR(f"[UI] 设置DEBUG MODE失败: {e}")

    def _gather_current_config_from_ui(self) -> Dict[str, Any]:
        global_config = {}
        if self.top_controls: global_config.update(self.top_controls.get_config())
        if self.timing_settings: global_config.update(self.timing_settings.get_config())
        if self.window_activation: global_config.update(self.window_activation.get_config())
        if self.stationary_mode: global_config.update(self.stationary_mode.get_config())
        if self.affix_reroll: global_config.update(self.affix_reroll.get_config())
        if self.pathfinding_settings: global_config.update(self.pathfinding_settings.get_config())
        if self.resource_management: global_config.update(self.resource_management.get_config())
        if self.priority_keys_widget: global_config["priority_keys"] = self.priority_keys_widget.get_config()
        skills_config = self.skill_config.get_config() if self.skill_config else {}
        if hasattr(self.skill_config, "sequence_entry"): global_config["skill_sequence"] = self.skill_config.sequence_entry.text()
        # 保留不在UI中编辑的配置段
        global_config["process_history"] = self._global_config.get("process_history", {})
        
        # tesseract_ocr 配置：如果为空则使用默认值
        tesseract_config = self._global_config.get("tesseract_ocr", {})
        if not tesseract_config:
            tesseract_config = {
                "tesseract_cmd": "D:\\Program Files\\Tesseract-OCR\\tesseract.exe",
                "lang": "eng",
                "psm_mode": 7,
                "char_whitelist": "0123456789/"
            }
        global_config["tesseract_ocr"] = tesseract_config
        
        return {"skills": skills_config, "global": global_config}

    def _save_config_file(self):
        try:
            filename, _ = QFileDialog.getSaveFileName(self, "保存配置文件", "", "JSON Files (*.json)")
            if filename:
                full_config = self._gather_current_config_from_ui()
                event_bus.publish("ui:save_full_config_requested", filename, full_config)
                if self.top_controls:
                    self.top_controls.set_current_config(os.path.basename(filename))
        except Exception as e:
            LOG_ERROR(f"[UI] 保存配置文件时出错: {e}")
            QMessageBox.critical(self, "错误", f"保存配置文件失败: {e}")

    def _load_config_file(self):
        try:
            filename, _ = QFileDialog.getOpenFileName(self, "加载配置文件", "", "JSON Files (*.json)")
            if filename:
                event_bus.publish("ui:load_config_requested", filename)
                if self.top_controls:
                    self.top_controls.set_current_config(os.path.basename(filename))
        except Exception as e:
            LOG_ERROR(f"[UI] 加载配置文件时出错: {e}")
            QMessageBox.critical(self, "错误", f"加载配置文件失败: {e}")

    def _connect_window_activation_signals(self):
        if self.window_activation:
            for child in self.window_activation.findChildren(QPushButton):
                if "刷新进程列表" in child.text():
                    child.clicked.connect(self._populate_process_list)
                elif "MuMu模拟器" in child.text():
                    child.clicked.connect(self._set_mumu_config)
            if hasattr(self.window_activation, "widgets") and "exe" in self.window_activation.widgets:
                self.window_activation.widgets["exe"].currentTextChanged.connect(self._on_process_selection_changed)

    def _populate_process_list(self):
        try:
            import psutil
            processes = [p.info["name"] for p in psutil.process_iter(["name"])]
            unique_processes = sorted(list(set(processes)))
            if self.window_activation and hasattr(self.window_activation, "widgets"):
                combo = self.window_activation.widgets.get("exe")
                if combo:
                    current_text = combo.currentText()
                    combo.clear()
                    combo.addItems(unique_processes)
                    if current_text in unique_processes:
                        combo.setCurrentText(current_text)
        except Exception as e:
            LOG_ERROR(f"[UI] 刷新进程列表时出错: {e}")

    def _set_mumu_config(self):
        if self.window_activation and hasattr(self.window_activation, "widgets"):
            self.window_activation.widgets["class"].setText("Qt5QWindowIcon")
            self.window_activation.widgets["exe"].setCurrentText("MuMuPlayer.exe")

    def _on_process_selection_changed(self, process_name: str):
        """处理进程选择变化，并自动获取窗口类名"""
        if self.window_activation and hasattr(self.window_activation, "widgets"):
            status_label = self.window_activation.widgets.get("status_label")
            class_input = self.window_activation.widgets.get("class")
            
            if not (status_label and class_input):
                return

            if process_name:
                status_label.setText(f"已选择进程: {process_name}")
                status_label.setStyleSheet("color: #4a90e2; font-size: 8pt;")
                try:
                    from ..utils.window_utils import WindowUtils
                    class_name = WindowUtils.get_window_class_by_process(process_name)
                    if class_name:
                        class_input.setText(class_name)
                        status_label.setText(f"已选择进程: {process_name} (类名: {class_name})")
                        LOG_INFO(f"[窗口激活] 自动获取窗口类名: {process_name} -> {class_name}")
                    else:
                        class_input.setText("（未找到对应窗口）")
                        LOG_INFO(f"[窗口激活] 未找到进程 {process_name} 对应的窗口类名")
                except Exception as e:
                    LOG_ERROR(f"[窗口激活] 获取窗口类名时出错: {e}")
                    class_input.setText("（获取类名时出错）")
            else:
                status_label.setText("当前未设置窗口激活")
                status_label.setStyleSheet("color: gray; font-size: 8pt;")
                class_input.setText("")


    def nativeEvent(self, eventType, message):
        """处理Windows原生消息，特别是WM_COPYDATA"""
        if eventType == "windows_generic_MSG":
            try:
                # 解析消息结构
                msg = wintypes.MSG.from_address(message.__int__())
                
                if msg.message == WM_COPYDATA:
                    # 处理WM_COPYDATA消息
                    self._handle_wm_copydata(msg.wParam, msg.lParam)
                    return True, 0
                    
            except Exception as e:
                LOG_ERROR(f"[WM_COPYDATA] 处理原生消息失败: {e}")
                
        return False, 0
    
    def _handle_wm_copydata(self, wParam, lParam):
        """处理WM_COPYDATA消息内容"""
        try:
            # 定义COPYDATASTRUCT结构
            class COPYDATASTRUCT(ctypes.Structure):
                _fields_ = [
                    ("dwData", ctypes.c_void_p),
                    ("cbData", ctypes.c_ulong),
                    ("lpData", ctypes.c_void_p)
                ]
            
            # 从lParam解析COPYDATASTRUCT
            cds = COPYDATASTRUCT.from_address(lParam)
            
            # 检查是否是AHK事件消息（使用9999作为标识）
            if cds.dwData == 9999 and cds.cbData > 0:
                # 读取事件数据
                event_data = ctypes.string_at(cds.lpData, cds.cbData).decode('utf-8')
                
                # 处理AHK事件
                self._process_ahk_event(event_data)
                
                LOG(f"[WM_COPYDATA] 收到AHK事件: {event_data}")
                
        except Exception as e:
            LOG_ERROR(f"[WM_COPYDATA] 解析消息失败: {e}")
    
    def _process_ahk_event(self, event_data: str):
        """处理从AHK接收到的事件"""
        try:
            # 直接使用信号桥接发射事件
            from ..core.signal_bridge import ahk_signal_bridge
            ahk_signal_bridge.ahk_event.emit(event_data)
            
        except Exception as e:
            LOG_ERROR(f"[WM_COPYDATA] 处理AHK事件失败: {e}")

    def closeEvent(self, event):
        self.macro_engine.cleanup()
        event.accept()
