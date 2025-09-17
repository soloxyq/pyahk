#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资源管理相关UI组件"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QDialog,
    QApplication,
)
from PySide6.QtCore import Qt, QRect, Signal as QSignal
from PySide6.QtGui import QPainter, QPen, QColor, QCursor
from typing import Dict, Any

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigComboBox,
    ConfigCheckBox,
)


class ResourceManagementWidget(QWidget):
    """POE智能药剂助手 - 配置HP/MP技能"""

    def __init__(self):
        super().__init__()
        self.hp_widgets = {}
        self.mp_widgets = {}
        self.main_window = None  # 引用主窗口，用于隐藏/显示

        # 存储拾取的HSV值 - 使用游戏实际测量值
        self.hp_hsv_values = {"h": 314, "s": 75, "v": 29}  # 血药颜色
        self.mp_hsv_values = {"h": 208, "s": 80, "v": 58}  # 蓝药颜色

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 移除标题和说明文本，保持界面简洁

        # 药剂配置区域
        flask_group = QGroupBox("药剂技能配置 (Flask Skills)")
        flask_layout = QHBoxLayout(flask_group)
        flask_layout.setContentsMargins(15, 20, 15, 15)
        flask_layout.setSpacing(20)

        # 生命药剂配置
        life_group = self._create_flask_skill_config_group(
            "生命药剂技能 (Life Flask)", "hp", "#FF6B6B"
        )
        flask_layout.addWidget(life_group)

        # 魔力药剂配置
        mana_group = self._create_flask_skill_config_group(
            "魔力药剂技能 (Mana Flask)", "mp", "#4ECDC4"
        )
        flask_layout.addWidget(mana_group)

        layout.addWidget(flask_group)

        # 全局设置区域
        global_group = self._create_global_settings_group()
        layout.addWidget(global_group)

        # 移除配置说明，保持界面简洁

        layout.addStretch()

    def _create_flask_skill_config_group(self, title, prefix, color):
        """创建药剂技能配置组"""
        group = QGroupBox(title)
        group.setStyleSheet(
            f"""
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {color};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: {color};
                font-weight: bold;
            }}
        """
        )

        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 20, 10, 10)
        layout.setSpacing(12)

        # 启用开关
        enabled_checkbox = ConfigCheckBox(f"启用{title.split('(')[0].strip()}")
        enabled_checkbox.setChecked(True)
        layout.addWidget(enabled_checkbox)

        # 基础配置
        basic_group = QGroupBox("基础配置")
        basic_layout = QGridLayout(basic_group)
        basic_layout.setContentsMargins(8, 12, 8, 8)
        basic_layout.setSpacing(8)

        # 快捷键
        basic_layout.addWidget(QLabel("快捷键:"), 0, 0)
        key_edit = ConfigLineEdit()
        key_edit.setText("1" if prefix == "hp" else "2")
        key_edit.setMaximumWidth(50)
        key_edit.setAlignment(Qt.AlignCenter)
        basic_layout.addWidget(key_edit, 0, 1)

        # 触发阈值
        basic_layout.addWidget(QLabel("触发阈值 (%):"), 0, 2)
        threshold_spinbox = ConfigSpinBox()
        threshold_spinbox.setRange(0, 100)
        threshold_spinbox.setValue(50)  # HP和MP都设置为50%
        threshold_spinbox.setMaximumWidth(60)
        basic_layout.addWidget(threshold_spinbox, 0, 3)

        # 冷却时间
        basic_layout.addWidget(QLabel("冷却时间 (ms):"), 1, 0)
        cooldown_spinbox = ConfigSpinBox()
        cooldown_spinbox.setRange(1000, 30000)
        cooldown_spinbox.setValue(5000 if prefix == "hp" else 8000)
        cooldown_spinbox.setMaximumWidth(80)
        basic_layout.addWidget(cooldown_spinbox, 1, 1)

        layout.addWidget(basic_group)

        # 检测区域设置
        region_group = QGroupBox("检测区域设置")
        region_layout = QVBoxLayout(region_group)
        region_layout.setContentsMargins(8, 12, 8, 8)
        region_layout.setSpacing(8)

        # 区域坐标
        coords_layout = QGridLayout()
        coords_layout.addWidget(QLabel("左上角:"), 0, 0)
        x1_edit = ConfigSpinBox()
        x1_edit.setRange(0, 8000)
        # 设置默认坐标值 (1080P屏幕)
        if prefix == "hp":
            x1_edit.setValue(136)  # 血药左上角X
        else:
            x1_edit.setValue(1552)  # 蓝药左上角X
        coords_layout.addWidget(x1_edit, 0, 1)

        y1_edit = ConfigSpinBox()
        y1_edit.setRange(0, 8000)
        if prefix == "hp":
            y1_edit.setValue(910)  # 血药左上角Y
        else:
            y1_edit.setValue(910)  # 蓝药左上角Y
        coords_layout.addWidget(y1_edit, 0, 2)

        coords_layout.addWidget(QLabel("右下角:"), 1, 0)
        x2_edit = ConfigSpinBox()
        x2_edit.setRange(0, 8000)
        if prefix == "hp":
            x2_edit.setValue(213)  # 血药右下角X
        else:
            x2_edit.setValue(1560)  # 蓝药右下角X
        coords_layout.addWidget(x2_edit, 1, 1)

        y2_edit = ConfigSpinBox()
        y2_edit.setRange(0, 8000)
        if prefix == "hp":
            y2_edit.setValue(1004)  # 血药右下角Y
        else:
            y2_edit.setValue(1004)  # 蓝药右下角Y
        coords_layout.addWidget(y2_edit, 1, 2)

        region_layout.addLayout(coords_layout)

        # 区域选择按钮
        select_btn = QPushButton("📦 选择区域")
        select_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 12px;
                padding: 8px;
                background-color: #17a2b8;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #138496;
            }
        """
        )
        select_btn.clicked.connect(lambda: self._start_region_selection(prefix))
        region_layout.addWidget(select_btn)

        layout.addWidget(region_group)

        # 颜色设置
        color_group = QGroupBox("颜色设置")
        color_layout = QVBoxLayout(color_group)
        color_layout.setContentsMargins(8, 12, 8, 8)

        # 移除旧的颜色拾取区域，将在颜色配置输入框旁边添加

        # 简化的颜色配置 - 使用逗号分隔格式
        colors_layout = QVBoxLayout()

        # 颜色配置说明
        colors_info = QLabel("颜色配置格式: H,S,V,H容差,S容差,V容差 (多颜色用逗号继续)")
        colors_info.setStyleSheet("color: #666; font-size: 10pt; font-style: italic;")
        colors_layout.addWidget(colors_info)

        # 颜色配置输入框
        colors_input_layout = QHBoxLayout()
        colors_input_layout.addWidget(QLabel("颜色配置:"))

        colors_edit = ConfigLineEdit()
        colors_edit.setPlaceholderText("例如: 314,75,29,10,20,20,80,84,48,20,27,27")

        # 设置默认值
        if prefix == "hp":
            # HP默认：正常血量 + 中毒状态
            default_colors = "314,75,29,10,20,20,80,84,48,20,27,27"
        else:
            # MP默认：只有蓝色
            default_colors = "208,80,58,7,5,5"

        colors_edit.setText(default_colors)
        colors_edit.setMinimumWidth(400)
        colors_input_layout.addWidget(colors_edit)

        # 添加解析按钮
        parse_btn = QPushButton("🔍 解析")
        parse_btn.setMaximumWidth(60)
        parse_btn.clicked.connect(
            lambda: self._parse_colors_input(prefix, colors_edit.text())
        )
        colors_input_layout.addWidget(parse_btn)

        # 添加颜色拾取按钮
        pick_btn = QPushButton("🎨 拾取")
        pick_btn.setMaximumWidth(60)
        pick_btn.clicked.connect(
            lambda: self._start_color_picking_for_input(prefix, colors_edit)
        )
        colors_input_layout.addWidget(pick_btn)

        colors_layout.addLayout(colors_input_layout)

        # 解析结果显示
        colors_result = QLabel("")
        colors_result.setStyleSheet("color: #333; font-size: 9pt; padding: 5px;")
        colors_result.setWordWrap(True)
        colors_layout.addWidget(colors_result)

        color_layout.addLayout(colors_layout)
        layout.addWidget(color_group)

        # 保存控件引用
        widgets = {
            "enabled": enabled_checkbox,
            "key": key_edit,
            "threshold": threshold_spinbox,
            "cooldown": cooldown_spinbox,
            "x1": x1_edit,
            "y1": y1_edit,
            "x2": x2_edit,
            "y2": y2_edit,
            "select_region_btn": select_btn,
            "colors_edit": colors_edit,
            "colors_result": colors_result,
            "parse_btn": parse_btn,
            "pick_btn": pick_btn,
        }

        # 连接颜色配置变化事件
        colors_edit.textChanged.connect(
            lambda: self._parse_colors_input(prefix, colors_edit.text())
        )

        if prefix == "hp":
            self.hp_widgets = widgets
        else:
            self.mp_widgets = widgets

        return group

    def _create_global_settings_group(self):
        """创建全局设置组"""
        group = QGroupBox("全局设置 (Global Settings)")
        group.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 2px solid #6C757D;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #6C757D;
                font-weight: bold;
            }
        """
        )

        layout = QHBoxLayout(group)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(15)

        # 检测间隔设置
        layout.addWidget(QLabel("检测间隔:"))
        self.check_interval_spinbox = ConfigSpinBox()
        self.check_interval_spinbox.setRange(50, 1000)  # 50ms到1000ms
        self.check_interval_spinbox.setValue(200)  # 默认200ms
        self.check_interval_spinbox.setSuffix(" ms")
        self.check_interval_spinbox.setMaximumWidth(80)
        layout.addWidget(self.check_interval_spinbox)

        # 添加说明
        info_label = QLabel("(检测频率，数值越小检测越频繁)")
        info_label.setStyleSheet("color: #666; font-size: 10pt; font-style: italic;")
        layout.addWidget(info_label)

        layout.addStretch()

        return group

    def _parse_colors_input(self, prefix: str, colors_text: str):
        """解析颜色配置输入"""
        try:
            # 获取对应的结果显示控件
            widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
            result_label = widgets["colors_result"]

            if not colors_text.strip():
                result_label.setText("请输入颜色配置")
                return

            # 解析逗号分隔的数值
            values = [int(x.strip()) for x in colors_text.split(",") if x.strip()]

            if len(values) % 6 != 0:
                result_label.setText(
                    "❌ 格式错误：每种颜色需要6个值 (H,S,V,H容差,S容差,V容差)"
                )
                return

            color_count = len(values) // 6
            result_text = f"✅ 解析成功：{color_count}种颜色\n"

            for i in range(color_count):
                base_idx = i * 6
                h, s, v = values[base_idx : base_idx + 3]
                h_tol, s_tol, v_tol = values[base_idx + 3 : base_idx + 6]

                # 验证范围
                if not (0 <= h <= 359):
                    result_label.setText(f"❌ 颜色{i+1}的H值({h})超出范围(0-359)")
                    return
                if not (0 <= s <= 255):
                    result_label.setText(f"❌ 颜色{i+1}的S值({s})超出范围(0-255)")
                    return
                if not (0 <= v <= 255):
                    result_label.setText(f"❌ 颜色{i+1}的V值({v})超出范围(0-255)")
                    return

                result_text += (
                    f"  颜色{i+1}: HSV({h},{s},{v}) 容差(±{h_tol},±{s_tol},±{v_tol})\n"
                )

            result_label.setText(result_text.strip())

        except ValueError:
            result_label.setText("❌ 格式错误：请输入数字，用逗号分隔")
        except Exception as e:
            result_label.setText(f"❌ 解析错误：{str(e)}")

    def _build_hp_config(self) -> Dict[str, Any]:
        """构建HP配置，使用新的colors列表格式"""
        # 基础配置
        hp_config = {
            "enabled": self.hp_widgets["enabled"].isChecked(),
            "key": self.hp_widgets["key"].text().strip(),
            "threshold": self.hp_widgets["threshold"].value(),
            "cooldown": self.hp_widgets["cooldown"].value(),
            "region_x1": self.hp_widgets["x1"].value(),
            "region_y1": self.hp_widgets["y1"].value(),
            "region_x2": self.hp_widgets["x2"].value(),
            "region_y2": self.hp_widgets["y2"].value(),
        }

        # 从颜色配置输入框解析颜色列表
        colors = self._parse_colors_to_list(self.hp_widgets["colors_edit"].text())
        hp_config["colors"] = colors

        # 为了向后兼容，从第一个颜色提取旧格式字段
        if colors:
            first_color = colors[0]
            hp_config.update(
                {
                    "target_h": first_color.get("target_h", 314),
                    "target_s": first_color.get("target_s", 75),
                    "target_v": first_color.get("target_v", 29),
                    "tolerance_h": first_color.get("tolerance_h", 10),
                    "tolerance_s": first_color.get("tolerance_s", 20),
                    "tolerance_v": first_color.get("tolerance_v", 20),
                }
            )

            # 如果有第二个颜色，设置poison字段
            if len(colors) > 1:
                second_color = colors[1]
                hp_config.update(
                    {
                        "poison_enabled": True,
                        "poison_h": second_color.get("target_h", 80),
                        "poison_s": second_color.get("target_s", 84),
                        "poison_v": second_color.get("target_v", 48),
                        "poison_tolerance_h": second_color.get("tolerance_h", 20),
                        "poison_tolerance_s": second_color.get("tolerance_s", 27),
                        "poison_tolerance_v": second_color.get("tolerance_v", 27),
                    }
                )
            else:
                hp_config.update(
                    {
                        "poison_enabled": False,
                        "poison_h": 80,
                        "poison_s": 84,
                        "poison_v": 48,
                        "poison_tolerance_h": 20,
                        "poison_tolerance_s": 27,
                        "poison_tolerance_v": 27,
                    }
                )

        return hp_config

    def _parse_colors_to_list(self, colors_text: str) -> list:
        """将颜色配置文本解析为颜色列表"""
        colors = []
        try:
            values = [int(x.strip()) for x in colors_text.split(",") if x.strip()]
            if len(values) % 6 == 0:
                color_count = len(values) // 6
                for i in range(color_count):
                    base_idx = i * 6
                    h, s, v = values[base_idx : base_idx + 3]
                    h_tol, s_tol, v_tol = values[base_idx + 3 : base_idx + 6]

                    color = {
                        "name": f"Color{i+1}",
                        "enabled": True,
                        "target_h": h,
                        "target_s": s,
                        "target_v": v,
                        "tolerance_h": h_tol,
                        "tolerance_s": s_tol,
                        "tolerance_v": v_tol,
                    }
                    colors.append(color)
        except:
            # 如果解析失败，返回默认配置
            colors = [
                {
                    "name": "Default",
                    "enabled": True,
                    "target_h": 314,
                    "target_s": 75,
                    "target_v": 29,
                    "tolerance_h": 10,
                    "tolerance_s": 20,
                    "tolerance_v": 20,
                }
            ]

        return colors

    def _build_mp_config(self) -> Dict[str, Any]:
        """构建MP配置，使用新的colors列表格式"""
        # 基础配置
        mp_config = {
            "enabled": self.mp_widgets["enabled"].isChecked(),
            "key": self.mp_widgets["key"].text().strip(),
            "threshold": self.mp_widgets["threshold"].value(),
            "cooldown": self.mp_widgets["cooldown"].value(),
            "region_x1": self.mp_widgets["x1"].value(),
            "region_y1": self.mp_widgets["y1"].value(),
            "region_x2": self.mp_widgets["x2"].value(),
            "region_y2": self.mp_widgets["y2"].value(),
        }

        # 从颜色配置输入框解析颜色列表
        colors = self._parse_colors_to_list(self.mp_widgets["colors_edit"].text())
        mp_config["colors"] = colors

        # 为了向后兼容，从第一个颜色提取旧格式字段
        if colors:
            first_color = colors[0]
            mp_config.update(
                {
                    "target_h": first_color.get("target_h", 208),
                    "target_s": first_color.get("target_s", 80),
                    "target_v": first_color.get("target_v", 58),
                    "tolerance_h": first_color.get("tolerance_h", 7),
                    "tolerance_s": first_color.get("tolerance_s", 5),
                    "tolerance_v": first_color.get("tolerance_v", 5),
                }
            )

        return mp_config

    def get_config(self) -> Dict[str, Any]:
        """获取配置（匹配ResourceManager期望的格式）"""
        return {
            "resource_management": {
                "hp_config": self._build_hp_config(),
                "mp_config": self._build_mp_config(),
                "check_interval": self.check_interval_spinbox.value(),  # 从UI获取检测间隔
            }
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI"""
        res_config = config.get("resource_management", {})

        # HP配置 - 使用1080P默认值
        hp_config = res_config.get("hp_config", {})
        if self.hp_widgets:
            self.hp_widgets["enabled"].setChecked(hp_config.get("enabled", True))
            self.hp_widgets["key"].setText(hp_config.get("key", "1"))
            self.hp_widgets["threshold"].setValue(
                hp_config.get("threshold", 50)
            )  # 默认50%
            self.hp_widgets["cooldown"].setValue(hp_config.get("cooldown", 5000))
            self.hp_widgets["x1"].setValue(
                hp_config.get("region_x1", 136)
            )  # 1080P血药区域
            self.hp_widgets["y1"].setValue(hp_config.get("region_y1", 910))
            self.hp_widgets["x2"].setValue(hp_config.get("region_x2", 213))
            self.hp_widgets["y2"].setValue(hp_config.get("region_y2", 1004))

            # 加载颜色配置
            colors_text = self._colors_list_to_text(hp_config.get("colors", []))
            if not colors_text:
                # 如果没有colors配置，从旧格式构建
                colors_text = self._build_colors_text_from_old_format(hp_config, True)

            self.hp_widgets["colors_edit"].setText(colors_text)
            self._parse_colors_input("hp", colors_text)

        # MP配置 - 使用1080P默认值
        mp_config = res_config.get("mp_config", {})
        if self.mp_widgets:
            self.mp_widgets["enabled"].setChecked(mp_config.get("enabled", True))
            self.mp_widgets["key"].setText(mp_config.get("key", "2"))
            self.mp_widgets["threshold"].setValue(
                mp_config.get("threshold", 50)
            )  # 默认50%
            self.mp_widgets["cooldown"].setValue(mp_config.get("cooldown", 8000))
            self.mp_widgets["x1"].setValue(
                mp_config.get("region_x1", 1552)
            )  # 1080P蓝药区域
            self.mp_widgets["y1"].setValue(mp_config.get("region_y1", 910))
            self.mp_widgets["x2"].setValue(mp_config.get("region_x2", 1560))
            self.mp_widgets["y2"].setValue(mp_config.get("region_y2", 1004))

            # 加载颜色配置
            colors_text = self._colors_list_to_text(mp_config.get("colors", []))
            if not colors_text:
                # 如果没有colors配置，从旧格式构建
                colors_text = self._build_colors_text_from_old_format(mp_config, False)

            self.mp_widgets["colors_edit"].setText(colors_text)
            self._parse_colors_input("mp", colors_text)

        # 更新全局设置
        check_interval = res_config.get("check_interval", 200)
        if hasattr(self, "check_interval_spinbox"):
            self.check_interval_spinbox.setValue(check_interval)

    def _colors_list_to_text(self, colors_list: list) -> str:
        """将颜色列表转换为文本格式"""
        if not colors_list:
            return ""

        values = []
        for color in colors_list:
            if color.get("enabled", True):
                values.extend(
                    [
                        color.get("target_h", 0),
                        color.get("target_s", 75),
                        color.get("target_v", 29),
                        color.get("tolerance_h", 10),
                        color.get("tolerance_s", 20),
                        color.get("tolerance_v", 20),
                    ]
                )

        return ",".join(map(str, values))

    def _build_colors_text_from_old_format(self, config: dict, is_hp: bool) -> str:
        """从旧格式配置构建颜色文本"""
        values = []

        # 主颜色
        if is_hp:
            values.extend(
                [
                    config.get("target_h", 314),
                    config.get("target_s", 75),
                    config.get("target_v", 29),
                    config.get("tolerance_h", 10),
                    config.get("tolerance_s", 20),
                    config.get("tolerance_v", 20),
                ]
            )

            # 中毒状态颜色（如果启用）
            if config.get("poison_enabled", False):
                values.extend(
                    [
                        config.get("poison_h", 80),
                        config.get("poison_s", 84),
                        config.get("poison_v", 48),
                        config.get("poison_tolerance_h", 20),
                        config.get("poison_tolerance_s", 27),
                        config.get("poison_tolerance_v", 27),
                    ]
                )
        else:
            # MP只有一种颜色
            values.extend(
                [
                    config.get("target_h", 208),
                    config.get("target_s", 80),
                    config.get("target_v", 58),
                    config.get("tolerance_h", 7),
                    config.get("tolerance_s", 5),
                    config.get("tolerance_v", 5),
                ]
            )

        return ",".join(map(str, values))

    def _start_color_picking_for_input(self, prefix: str, colors_edit):
        """启动颜色拾取，将结果添加到输入框末尾"""
        if self.main_window:
            self.main_window.hide()

        dialog = ColorPickingDialog()

        def on_color_picked(h, s, v):
            # 获取当前输入框的内容
            current_text = colors_edit.text().strip()

            # 构建新的颜色值（HSV + 默认容差）
            # h, s, v 已经是独立的参数

            # 设置默认容差
            if prefix == "hp":
                default_tolerance = "10,20,20"  # HP默认容差
            else:
                default_tolerance = "7,5,5"  # MP默认容差

            new_color = f"{h},{s},{v},{default_tolerance}"

            # 添加到输入框末尾
            if current_text:
                updated_text = f"{current_text},{new_color}"
            else:
                updated_text = new_color

            colors_edit.setText(updated_text)

            # 自动解析新的配置
            self._parse_colors_input(prefix, updated_text)

            # 显示主窗口
            if self.main_window:
                self.main_window.show()

        dialog.color_picked.connect(on_color_picked)
        dialog.exec()

    def set_main_window(self, main_window):
        """设置主窗口引用，用于隐藏/显示界面"""
        self.main_window = main_window

    def _start_region_selection(self, prefix: str):
        """开始区域选择"""
        if not self.main_window:
            return

        # 使用QTimer延迟执行，确保界面完全隐藏
        from PySide6.QtCore import QTimer

        def show_dialog():
            # 创建区域选择对话框
            dialog = RegionSelectionDialog(None)  # 不设置父窗口，避免焦点问题
            dialog.region_selected.connect(
                lambda x1, y1, x2, y2: self._on_region_selected(prefix, x1, y1, x2, y2)
            )

            # 执行对话框（showEvent会自动处理焦点）
            result = dialog.exec()

            # 显示主界面
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()

        # 隐藏主界面
        self.main_window.hide()

        # 延迟100ms执行对话框显示
        QTimer.singleShot(100, show_dialog)

    def _on_region_selected(self, prefix: str, x1: int, y1: int, x2: int, y2: int):
        """区域选择完成回调"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        widgets["x1"].setValue(x1)
        widgets["y1"].setValue(y1)
        widgets["x2"].setValue(x2)
        widgets["y2"].setValue(y2)

    def _hsv_to_rgb(self, h: int, s: int, v: int) -> tuple:
        """将HSV值转换为RGB值"""
        import colorsys

        # 将HSV值标准化到0-1范围
        h_norm = h / 359.0
        s_norm = s / 255.0
        v_norm = v / 255.0

        # 转换为RGB
        r, g, b = colorsys.hsv_to_rgb(h_norm, s_norm, v_norm)

        # 转换回0-255范围
        return (int(r * 255), int(g * 255), int(b * 255))


class RegionSelectionDialog(QDialog):
    """区域选择对话框"""

    region_selected = QSignal(int, int, int, int)  # x1, y1, x2, y2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择检测区域")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool  # 添加Tool标志，避免任务栏显示
            | Qt.WindowDoesNotAcceptFocus  # 移除这个标志，允许接收焦点
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # 获取屏幕截图
        screen = QApplication.primaryScreen()
        self.screenshot = screen.grabWindow(0)

        self.start_pos = None
        self.end_pos = None
        self.is_selecting = False

        self.setGeometry(screen.geometry())

        # 设置鼠标追踪
        self.setMouseTracking(True)

        # 确保窗口能接收键盘事件
        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event):
        """窗口显示事件"""
        super().showEvent(event)
        # 确保窗口获得焦点
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.screenshot)

        # 绘制半透明遮罩
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        # 绘制选择区域
        if self.start_pos and self.end_pos:
            rect = QRect(self.start_pos, self.end_pos).normalized()
            # 清除选择区域的遮罩
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # 绘制边框
            pen = QPen(QColor(255, 0, 0), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.is_selecting = True

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.end_pos = event.pos()
            self.is_selecting = False

            if self.start_pos and self.end_pos:
                rect = QRect(self.start_pos, self.end_pos).normalized()
                self.region_selected.emit(
                    rect.left(), rect.top(), rect.right(), rect.bottom()
                )

            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)


class ColorPickingDialog(QDialog):
    """颜色拾取对话框"""

    color_picked = QSignal(int, int, int)  # h, s, v

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("拾取颜色")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool  # 添加Tool标志，避免任务栏显示
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # 获取屏幕截图
        screen = QApplication.primaryScreen()
        self.screenshot = screen.grabWindow(0)

        self.setGeometry(screen.geometry())

        # 创建放大镜效果
        self.magnifier_size = 100
        self.zoom_factor = 4

        # 设置鼠标追踪
        self.setMouseTracking(True)

        # 确保窗口能接收键盘事件
        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event):
        """窗口显示事件"""
        super().showEvent(event)
        # 确保窗口获得焦点
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.screenshot)

        # 绘制半透明遮罩
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))

        # 绘制十字线
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        pen = QPen(QColor(255, 255, 255), 1)
        painter.setPen(pen)
        painter.drawLine(0, cursor_pos.y(), self.width(), cursor_pos.y())
        painter.drawLine(cursor_pos.x(), 0, cursor_pos.x(), self.height())

        # 绘制放大镜
        magnifier_rect = QRect(
            cursor_pos.x() - self.magnifier_size // 2,
            cursor_pos.y() - self.magnifier_size // 2,
            self.magnifier_size,
            self.magnifier_size,
        )

        # 放大镜背景
        painter.fillRect(magnifier_rect, QColor(255, 255, 255, 200))

        # 绘制放大的像素
        for x in range(self.magnifier_size):
            for y in range(self.magnifier_size):
                src_x = (
                    cursor_pos.x()
                    - self.magnifier_size // (2 * self.zoom_factor)
                    + x // self.zoom_factor
                )
                src_y = (
                    cursor_pos.y()
                    - self.magnifier_size // (2 * self.zoom_factor)
                    + y // self.zoom_factor
                )

                if (
                    0 <= src_x < self.screenshot.width()
                    and 0 <= src_y < self.screenshot.height()
                ):
                    color = self.screenshot.toImage().pixelColor(src_x, src_y)
                    painter.fillRect(
                        magnifier_rect.left() + x, magnifier_rect.top() + y, 1, 1, color
                    )

        # 放大镜边框
        pen.setColor(QColor(0, 0, 0))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(magnifier_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            cursor_pos = self.mapFromGlobal(QCursor.pos())

            if (
                0 <= cursor_pos.x() < self.screenshot.width()
                and 0 <= cursor_pos.y() < self.screenshot.height()
            ):
                color = self.screenshot.toImage().pixelColor(
                    cursor_pos.x(), cursor_pos.y()
                )

                # 转换为HSV
                h, s, v, _ = color.getHsv()

                # 转换Qt HSV到OpenCV HSV格式
                # Qt: H(0-359/-1), S(0-255), V(0-255)
                # OpenCV: H(0-179), S(0-255), V(0-255)
                if h == -1:  # 灰色/无色相
                    h = 0
                else:
                    h = h // 2  # 将360度范围转换为180度范围

                self.color_picked.emit(h, s, v)

            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
