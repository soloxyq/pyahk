#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI组件模块 - 包含各种UI组件类"""

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
from typing import Dict, Any, List, Tuple, Optional

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
    ConfigComboBox,
)
from ..utils.debug_log import LOG_INFO


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
        return {
            "sequence_enabled": self.mode_combo.currentText() == "序列"
        }

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
        self.sound_feedback_checkbox = ConfigCheckBox("启用状态切换声音提示 (start, stop, pause, resume)")
        sound_layout.addWidget(self.sound_feedback_checkbox)
        sound_layout.addStretch()
        layout.addWidget(sound_group)

        layout.addStretch()

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        config = {
            "queue_processor_interval": self.timing_spinboxes["queue_processor"].value(),
            "key_press_duration": self.timing_spinboxes["key_press"].value(),
            "mouse_click_duration": self.timing_spinboxes["mouse_click"].value(),
            "cooldown_checker_interval": self.timing_spinboxes["cooldown_checker"].value(),
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
            self.sound_feedback_checkbox.setChecked(config.get("sound_feedback_enabled", False))


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
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    processes.append(proc.info['name'])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # 去重并排序
            unique_processes = sorted(list(set(processes)))
            self.widgets['exe'].addItems(unique_processes)
        except Exception:
            # 如果获取进程列表失败，添加一些常见的进程名
            common_processes = ["MuMuPlayer.exe", "chrome.exe", "firefox.exe", "notepad.exe"]
            self.widgets['exe'].addItems(common_processes)

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
                "mode_type": "shift_modifier" if self.mode_combo.currentIndex() == 0 else "block_mouse",
                "force_move_hotkey": force_move_hotkey if force_move_hotkey else "",
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        stationary_config = config.get("stationary_mode_config", {"hotkey": "", "mode_type": "block_mouse", "force_move_hotkey": ""})
        
        self.hotkey_entry.setText(stationary_config.get("hotkey", ""))
        self.force_move_hotkey_entry.setText(stationary_config.get("force_move_hotkey", ""))
        
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
            if isinstance(w, QSpinBox):
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
                ]
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



class AffixRerollWidget(QWidget):
    """洗练配置组件"""
    
    def __init__(self):
        super().__init__()
        self.coord_widgets = {}
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # OCR引擎管理
        ocr_group = QGroupBox("OCR引擎管理")
        ocr_layout = QHBoxLayout(ocr_group)
        ocr_layout.setContentsMargins(15, 20, 15, 15)
        ocr_layout.setSpacing(12)

        self.ocr_load_button = QPushButton("加载OCR引擎")
        self.ocr_load_button.setMaximumHeight(32)
        ocr_layout.addWidget(self.ocr_load_button)

        self.ocr_status_label = QLabel("OCR引擎未加载")
        self.ocr_status_label.setStyleSheet("color: #888888; font-weight: bold;")
        ocr_layout.addWidget(self.ocr_status_label)

        ocr_layout.addStretch()
        layout.addWidget(ocr_group)

        # 洗练配置
        config_group = QGroupBox("装备词缀洗练配置")
        config_layout = QGridLayout(config_group)
        config_layout.setContentsMargins(15, 20, 15, 15)
        config_layout.setSpacing(12)

        # 启用洗练功能
        self.enabled_checkbox = ConfigCheckBox("启用洗练功能")
        config_layout.addWidget(self.enabled_checkbox, 0, 0, 1, 2)

        # 最大尝试次数
        config_layout.addWidget(QLabel("最大尝试次数:"), 1, 0)
        self.max_attempts_spinbox = ConfigSpinBox()
        self.max_attempts_spinbox.setMinimum(1)
        self.max_attempts_spinbox.setMaximum(1000)
        self.max_attempts_spinbox.setValue(100)
        config_layout.addWidget(self.max_attempts_spinbox, 1, 1)

        # 目标词缀输入
        config_layout.addWidget(QLabel("目标词缀 (每行一个):"), 2, 0, 1, 2)
        self.target_text = QTextEdit()
        self.target_text.setPlaceholderText("输入目标词缀关键词，每行一个\n例如：\n生命\n移动速度\n毒伤")
        self.target_text.setMaximumHeight(100)
        config_layout.addWidget(self.target_text, 3, 0, 1, 2)

        # 坐标设置
        coords_group = QGroupBox("坐标设置 (格式: x,y)")
        coords_layout = QGridLayout(coords_group)
        coords_layout.setContentsMargins(10, 15, 10, 10)
        coords_layout.setSpacing(8)

        coord_names = {
            "enchant_button_coord": "附魔按钮",
            "first_affix_button_coord": "首位词缀",
            "replace_button_coord": "替换按钮",
            "close_button_coord": "关闭按钮",
        }

        row = 0
        for name, label_text in coord_names.items():
            label = QLabel(f"{label_text}坐标:")
            line_edit = ConfigLineEdit()
            line_edit.setPlaceholderText("例如: 123,456")
            coords_layout.addWidget(label, row, 0)
            coords_layout.addWidget(line_edit, row, 1)
            self.coord_widgets[name] = line_edit
            row += 1

        config_layout.addWidget(coords_group, 4, 0, 1, 2)

        # 使用说明
        help_label = QLabel(
            """使用说明：
1. 先点击"加载OCR引擎"按钮初始化文字识别功能
2. 启用洗练功能并设置目标词缀
3. 配置各按钮的坐标位置
4. 按F7开始/停止洗练
5. 洗练状态将显示在OSD窗口中"""
        )
        help_label.setStyleSheet("color: #888888; font-size: 9pt; padding: 10px;")
        help_label.setWordWrap(True)
        config_layout.addWidget(help_label, 5, 0, 1, 2)

        layout.addWidget(config_group)
        layout.addStretch()

    def update_ocr_status(self, text: str, color: str):
        """更新OCR状态显示"""
        self.ocr_status_label.setText(text)
        self.ocr_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def get_config(self) -> Dict[str, Any]:
        """获取配置"""
        return {
            "affix_reroll": {
                "enabled": self.enabled_checkbox.isChecked(),
                "target_affixes": self._parse_target_affixes(),
                "max_attempts": self.max_attempts_spinbox.value(),
                "click_delay": 200,
                "enchant_button_coord": self._parse_coord_from_text(
                    self.coord_widgets["enchant_button_coord"].text()
                ),
                "first_affix_button_coord": self._parse_coord_from_text(
                    self.coord_widgets["first_affix_button_coord"].text()
                ),
                "replace_button_coord": self._parse_coord_from_text(
                    self.coord_widgets["replace_button_coord"].text()
                ),
                "close_button_coord": self._parse_coord_from_text(
                    self.coord_widgets["close_button_coord"].text()
                ),
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        affix_config = config.get("affix_reroll", {})

        self.enabled_checkbox.setChecked(affix_config.get("enabled", False))
        self.max_attempts_spinbox.setValue(affix_config.get("max_attempts", 100))

        target_affixes = affix_config.get("target_affixes", [])
        self.target_text.setPlainText("\n".join(target_affixes))

        for name, widget in self.coord_widgets.items():
            coord = affix_config.get(name)
            if coord and isinstance(coord, (list, tuple)) and len(coord) == 2:
                widget.setText(f"{coord[0]},{coord[1]}")
            else:
                widget.setText("")

    def _parse_target_affixes(self) -> List[str]:
        """解析目标词缀列表"""
        text = self.target_text.toPlainText().strip()
        if not text:
            return []

        lines = text.split("\n")
        affixes = []
        for line in lines:
            cleaned = line.strip()
            if cleaned:
                affixes.append(cleaned)

        return affixes

    def _parse_coord_from_text(self, text: str) -> Optional[Tuple[int, int]]:
        """从文本解析坐标"""
        try:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) == 2:
                return (int(parts[0]), int(parts[1]))
            return None
        except (ValueError, IndexError):
            return None


class SkillConfigWidget(QWidget):
    """技能配置组件"""
    
    def __init__(self):
        super().__init__()
        self.skill_widgets = {}
        self._skills_config = {}
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # 序列配置
        self.sequence_frame = QGroupBox("按键序列配置")
        seq_layout = QVBoxLayout(self.sequence_frame)
        seq_layout.setContentsMargins(6, 8, 6, 6)
        seq_layout.setSpacing(4)

        seq_layout.addWidget(QLabel("序列 (逗号分隔):"))
        self.sequence_entry = ConfigLineEdit()
        self.sequence_entry.setMaximumHeight(24)
        seq_layout.addWidget(self.sequence_entry)

        # 技能配置
        self.skill_frame = QGroupBox("技能配置")
        self.skill_layout = QVBoxLayout(self.skill_frame)
        self.skill_layout.setContentsMargins(6, 8, 6, 4)
        self.skill_layout.setSpacing(1)

        layout.addWidget(self.skill_frame, 3)
        layout.addWidget(self.sequence_frame, 3)
        self.sequence_frame.hide()

    def get_config(self) -> Dict[str, Any]:
        """获取技能配置"""
        skills_config = {}
        for skill_name, widget in self.skill_widgets.items():
            if hasattr(widget, 'get_current_config'):
                skills_config[skill_name] = widget.get_current_config()
        return skills_config

    def update_from_config(self, skills_config: Dict[str, Any], global_config: Dict[str, Any] = None):
        LOG_INFO(f"[SkillConfigWidget] 接收到 skills_config: {skills_config}")
        self._skills_config = skills_config
        LOG_INFO("[SkillConfigWidget] 调用 _create_skill_widgets() 创建技能UI。")
        self._create_skill_widgets()
        
        # 更新序列配置
        if global_config and hasattr(self, 'sequence_entry'):
            self.sequence_entry.setText(global_config.get("skill_sequence", ""))
            LOG_INFO(f"[SkillConfigWidget] 序列配置已更新: {self.sequence_entry.text()}")

    def _create_skill_widgets(self):
        LOG_INFO("[SkillConfigWidget] 开始创建技能UI控件...")
        # 清理现有控件
        for widget in self.skill_widgets.values():
            if hasattr(widget, 'destroy'):
                widget.destroy()
        self.skill_widgets.clear()

        # 清理布局
        while self.skill_layout.count():
            child = self.skill_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 创建新的技能控件
        from ..core.event_bus import event_bus
        from .skill_config_widget import SimplifiedSkillWidget
        
        count = 0
        for skill_name, config in self._skills_config.items():
            if count >= 8:  # 限制显示8个技能
                LOG_INFO("[SkillConfigWidget] 已达到最大显示技能数 (8)，停止创建。")
                break
            self._create_single_skill_widget(skill_name, config)
            count += 1

        self.skill_layout.addStretch()
        LOG_INFO("[SkillConfigWidget] 技能UI控件创建完成。")

    def _create_single_skill_widget(self, skill_name: str, skill_config: Dict[str, Any]):
        """创建单个技能配置控件"""
        from ..core.event_bus import event_bus
        from .skill_config_widget import SimplifiedSkillWidget
        
        skill_container = QFrame()
        skill_container.setFrameStyle(QFrame.Box)
        skill_container.setLineWidth(1)
        container_layout = QVBoxLayout(skill_container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(1)

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)

        title_label = QLabel(f"技能: {skill_name}")
        title_label.setStyleSheet("font-weight: bold; color: #2E86AB;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        container_layout.addLayout(title_layout)

        skill_widget = SimplifiedSkillWidget(
            skill_container, skill_name, skill_config, event_bus
        )
        container_layout.addWidget(skill_widget.get_frame())

        self.skill_layout.addWidget(skill_container)
        self.skill_widgets[skill_name] = skill_widget
        skill_widget.refresh(skill_config)