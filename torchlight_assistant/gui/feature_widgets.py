#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""功能特定UI组件"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QFrame,
    QTextEdit,
)
from PySide6.QtCore import Qt
from typing import Dict, Any, List, Tuple, Optional

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigCheckBox,
)
from ..utils.debug_log import LOG_INFO


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
        self.target_text.setPlaceholderText(
            "输入目标词缀关键词，每行一个\n例如：\n生命\n移动速度\n毒伤"
        )
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
            if hasattr(widget, "get_current_config"):
                skills_config[skill_name] = widget.get_current_config()
        return skills_config

    def update_from_config(
        self, skills_config: Dict[str, Any], global_config: Optional[Dict[str, Any]] = None
    ):
        LOG_INFO(f"[SkillConfigWidget] 接收到 skills_config: {skills_config}")
        self._skills_config = skills_config
        LOG_INFO("[SkillConfigWidget] 调用 _create_skill_widgets() 创建技能UI。")
        try:
            self._create_skill_widgets()
            LOG_INFO("[SkillConfigWidget] _create_skill_widgets() 执行完成")
        except Exception as e:
            LOG_INFO(f"[SkillConfigWidget] _create_skill_widgets() 执行失败: {e}")
            import traceback
            traceback.print_exc()

        # 更新序列配置
        if global_config and hasattr(self, "sequence_entry"):
            self.sequence_entry.setText(global_config.get("skill_sequence", ""))
            LOG_INFO(
                f"[SkillConfigWidget] 序列配置已更新: {self.sequence_entry.text()}"
            )

    def _create_skill_widgets(self):
        LOG_INFO("[SkillConfigWidget] 开始创建技能UI控件...")
        # 清理现有控件
        for widget in self.skill_widgets.values():
            if hasattr(widget, "destroy"):
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

    def _create_single_skill_widget(
        self, skill_name: str, skill_config: Dict[str, Any]
    ):
        """创建单个技能配置控件"""
        try:
            from ..core.event_bus import event_bus
            from .skill_config_widget import SimplifiedSkillWidget

            # 直接创建SimplifiedSkillWidget，不需要额外的容器
            skill_widget = SimplifiedSkillWidget(
                self, skill_name, skill_config, event_bus
            )
            
            # 添加到布局中
            self.skill_layout.addWidget(skill_widget)
            self.skill_widgets[skill_name] = skill_widget
            
            LOG_INFO(f"[SkillConfigWidget] 成功创建技能控件: {skill_name}")
            
        except Exception as e:
            LOG_INFO(f"[SkillConfigWidget] 创建技能控件失败 {skill_name}: {e}")
            import traceback
            traceback.print_exc()
        skill_widget.refresh(skill_config)