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
from .color_picker_dialog import ColorPickingDialog
from .region_selection_dialog import RegionSelectionDialog


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
        colors_info = QLabel(
            "颜色配置格式: H,S,V,H容差,S容差,V容差 (OpenCV格式: H=0-179, 多颜色用逗号继续)"
        )
        colors_info.setStyleSheet("color: #666; font-size: 10pt; font-style: italic;")
        colors_layout.addWidget(colors_info)

        # 颜色配置输入框
        colors_input_layout = QHBoxLayout()
        colors_input_layout.addWidget(QLabel("颜色配置:"))

        colors_edit = ConfigLineEdit()
        colors_edit.setPlaceholderText("例如: 314,75,29,10,20,20,80,84,48,20,27,27")

        # 设置默认值 (OpenCV HSV格式: H=0-179)
        if prefix == "hp":
            # HP默认：正常血量 + 中毒状态 (转换为OpenCV格式)
            default_colors = "157,75,29,5,20,20,40,84,48,10,27,27"  # 314°→157°, 80°→40°
        else:
            # MP默认：只有蓝色 (转换为OpenCV格式)
            default_colors = "104,80,58,4,5,5"  # 208°→104°

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
        colors_result.setStyleSheet(
            "font-size: 9pt; padding: 5px; background-color: #f5f5f5; border-radius: 3px;"
        )
        colors_result.setWordWrap(True)
        colors_result.setTextFormat(Qt.RichText)  # 支持HTML格式
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

        # 立即解析默认值，显示彩色背景
        self._parse_colors_input(prefix, default_colors)

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

    def _hsv_to_rgb(self, h: int, s: int, v: int) -> tuple:
        """将OpenCV HSV颜色转换为RGB (使用OpenCV确保一致性)"""
        import cv2
        import numpy as np

        # 输入的h,s,v已经是OpenCV格式 (H: 0-179, S: 0-255, V: 0-255)
        hsv_array = np.uint8([[[h, s, v]]])
        rgb_array = cv2.cvtColor(hsv_array, cv2.COLOR_HSV2RGB)
        r, g, b = rgb_array[0][0]

        return int(r), int(g), int(b)

    def _get_contrast_color(self, r: int, g: int, b: int) -> str:
        """根据背景色亮度返回合适的文字颜色"""
        # 计算亮度 (使用相对亮度公式)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#ffffff" if luminance < 0.5 else "#000000"

    def _parse_colors_input(self, prefix: str, colors_text: str):
        """解析颜色配置输入并显示带实际颜色的结果"""
        try:
            # 获取对应的结果显示控件
            widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
            
            if not widgets or "colors_result" not in widgets:
                return
                
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

            # 构建HTML格式的结果文本
            html_parts = [
                f"<div style='margin-bottom: 8px; font-weight: bold;'>✅ 解析成功：{color_count}种颜色</div>"
            ]

            for i in range(color_count):
                base_idx = i * 6
                h, s, v = values[base_idx : base_idx + 3]
                h_tol, s_tol, v_tol = values[base_idx + 3 : base_idx + 6]

                # 验证OpenCV HSV范围
                if not (0 <= h <= 179):
                    result_label.setText(f"❌ 颜色{i+1}的H值({h})超出OpenCV范围(0-179)")
                    return
                if not (0 <= s <= 255):
                    result_label.setText(f"❌ 颜色{i+1}的S值({s})超出范围(0-255)")
                    return
                if not (0 <= v <= 255):
                    result_label.setText(f"❌ 颜色{i+1}的V值({v})超出范围(0-255)")
                    return

                # 转换HSV到RGB
                r, g, b = self._hsv_to_rgb(h, s, v)
                bg_color = f"rgb({r},{g},{b})"
                text_color = self._get_contrast_color(r, g, b)

                # 创建带颜色背景的HTML块
                color_html = f"""
                <div style='margin: 3px 0; padding: 6px 10px; border-radius: 6px; 
                           background-color: {bg_color}; color: {text_color}; 
                           border: 1px solid #ddd; font-size: 10pt; font-weight: bold;'>
                    颜色{i+1}: OpenCV-HSV({h},{s},{v}) 容差(±{h_tol},±{s_tol},±{v_tol})
                </div>
                """
                html_parts.append(color_html)

            result_html = "".join(html_parts)
            result_label.setText(result_html)

        except ValueError:
            if 'result_label' in locals():
                result_label.setText("❌ 格式错误：请输入数字，用逗号分隔")
        except Exception as e:
            if 'result_label' in locals():
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

        # 注意：不再写入旧格式字段，只使用新的colors数组格式

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

        # 注意：不再写入旧格式字段，只使用新的colors数组格式

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
                # 如果没有colors配置，使用默认值
                colors_text = "157,75,29,5,20,20,40,84,48,10,27,27"  # HP默认：红色+绿色

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
                # 如果没有colors配置，使用默认值
                colors_text = "104,80,58,4,5,5"  # MP默认：蓝色

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



    def _start_color_picking_for_input(self, prefix: str, colors_edit):
        """启动颜色拾取，将结果添加到输入框末尾"""
        # 完全隐藏主窗口，就像截图工具一样
        if self.main_window:
            self.main_window.hide()
            self.main_window.setWindowState(
                self.main_window.windowState() | Qt.WindowMinimized
            )

        # 延迟一下确保窗口完全隐藏
        from PySide6.QtCore import QTimer

        def start_color_picking():
            dialog = ColorPickingDialog()

            def on_color_picked(r, g, b):
                # 获取当前输入框的内容
                current_text = colors_edit.text().strip()

                # 使用OpenCV将RGB转换为HSV
                import cv2
                import numpy as np

                rgb_array = np.uint8([[[r, g, b]]])
                hsv_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2HSV)
                h, s, v = hsv_array[0][0]

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

                # 输出调试信息
                print(f"[颜色拾取] RGB({r},{g},{b}) -> HSV({h},{s},{v})")
                print(f"[颜色拾取] 已追加到配置: {new_color}")
                print(f"[颜色拾取] 完整配置: {updated_text}")

                # 自动解析新的配置
                self._parse_colors_input(prefix, updated_text)

                # 恢复显示主窗口
                if self.main_window:
                    self.main_window.setWindowState(
                        self.main_window.windowState() & ~Qt.WindowMinimized
                    )
                    self.main_window.show()
                    self.main_window.raise_()
                    self.main_window.activateWindow()

            dialog.color_picked.connect(on_color_picked)
            dialog.exec()

        # 延迟200ms启动，确保主窗口完全隐藏
        QTimer.singleShot(200, start_color_picking)

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
            dialog.region_analyzed.connect(
                lambda x1, y1, x2, y2, analysis: self._on_region_analyzed(
                    prefix, x1, y1, x2, y2, analysis
                )
            )

            # 执行对话框（showEvent会自动处理焦点）
            result = dialog.exec()

            # 恢复显示主界面
            self.main_window.setWindowState(
                self.main_window.windowState() & ~Qt.WindowMinimized
            )
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()

        # 完全隐藏主界面，就像截图工具一样
        self.main_window.hide()
        self.main_window.setWindowState(
            self.main_window.windowState() | Qt.WindowMinimized
        )

        # 延迟200ms执行对话框显示，确保主窗口完全隐藏
        QTimer.singleShot(200, show_dialog)

    def _on_region_selected(self, prefix: str, x1: int, y1: int, x2: int, y2: int):
        """区域选择完成回调"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        widgets["x1"].setValue(x1)
        widgets["y1"].setValue(y1)
        widgets["x2"].setValue(x2)
        widgets["y2"].setValue(y2)
        print(f"[区域更新] {prefix.upper()}检测区域已更新为: ({x1},{y1}) -> ({x2},{y2})")

    def _on_region_analyzed(
        self, prefix: str, x1: int, y1: int, x2: int, y2: int, analysis: dict
    ):
        """智能颜色分析完成回调"""
        if not analysis or not analysis.get("analysis_success"):
            return

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 🎯 关键修复：更新检测区域坐标为用户最后选择的区域
        widgets["x1"].setValue(x1)
        widgets["y1"].setValue(y1)
        widgets["x2"].setValue(x2)
        widgets["y2"].setValue(y2)
        print(f"[区域更新] {prefix.upper()}检测区域已更新为: ({x1},{y1}) -> ({x2},{y2})")

        # 获取分析结果
        mean_h, mean_s, mean_v = analysis["mean_hsv"]
        tolerance_h, tolerance_s, tolerance_v = analysis["tolerance"]
        total_pixels = analysis["total_pixels"]
        region_size = analysis["region_size"]

        # 构建颜色配置字符串
        new_color_config = (
            f"{mean_h},{mean_s},{mean_v},{tolerance_h},{tolerance_s},{tolerance_v}"
        )

        # 获取当前输入框内容
        current_text = widgets["colors_edit"].text().strip()

        # 追加到输入框末尾（支持多HSV）
        if current_text:
            updated_text = f"{current_text},{new_color_config}"
        else:
            updated_text = new_color_config

        # 更新颜色配置输入框
        widgets["colors_edit"].setText(updated_text)

        # 自动解析并显示
        self._parse_colors_input(prefix, updated_text)

        # 显示分析信息
        from PySide6.QtWidgets import QMessageBox

        info_msg = f"""🎯 智能颜色分析完成！

📊 分析结果：
• 区域大小: {region_size[0]}×{region_size[1]} 像素
• 总像素数: {total_pixels:,} 个
• 平均颜色: HSV({mean_h}, {mean_s}, {mean_v})
• 智能容差: ±({tolerance_h}, {tolerance_s}, {tolerance_v})

✅ 已自动配置颜色检测参数
💡 容差基于区域内颜色分布自动计算，覆盖约95%的像素"""

        # 使用简单的print输出替代消息框，避免UI问题
        print("=" * 50)
        print("🎯 智能颜色分析完成！")
        print(f"📊 区域大小: {region_size[0]}×{region_size[1]} 像素")
        print(f"📊 总像素数: {total_pixels:,} 个")
        print(f"🎨 平均颜色: HSV({mean_h}, {mean_s}, {mean_v})")
        print(f"⚙️  智能容差: ±({tolerance_h}, {tolerance_s}, {tolerance_v})")
        print(f"✅ 已追加到颜色配置: {new_color_config}")
        print("=" * 50)
