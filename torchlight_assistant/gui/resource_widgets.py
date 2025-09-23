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
    QTextEdit,
    QFrame,
    QLineEdit,
)
from PySide6.QtCore import Qt, QRect, Signal as QSignal
from PySide6.QtGui import QPainter, QPen, QColor, QCursor
from typing import Dict, Any, Optional

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
        self.tolerance_widgets = {}  # 容差配置控件
        self.main_window = None  # 引用主窗口，用于隐藏/显示

        # 存储拾取的HSV值 - 使用游戏实际测量值
        self.hp_hsv_values = {"h": 314, "s": 75, "v": 29}  # 血药颜色
        self.mp_hsv_values = {"h": 208, "s": 80, "v": 58}  # 蓝药颜色

        # 检测模式跟踪
        self.hp_detection_mode = "rectangle"  # "rectangle" 或 "circle"
        self.mp_detection_mode = "rectangle"  # "rectangle" 或 "circle"

        # 圆形配置存储
        self.hp_circle_config = {}
        self.mp_circle_config = {}

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 移除标题和说明文本，保持界面简洁

        # 药剂配置区域
        flask_group = QGroupBox("药剂技能配置 (Flask Skills)")
        flask_layout = QHBoxLayout(flask_group)
        flask_layout.setContentsMargins(10, 15, 10, 10)
        flask_layout.setSpacing(15)

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
                margin-top: 6px;
                padding-top: 8px;
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
        layout.setContentsMargins(8, 12, 8, 6)
        layout.setSpacing(6)

        # 启用开关
        enabled_checkbox = ConfigCheckBox(f"启用{title.split('(')[0].strip()}")
        enabled_checkbox.setChecked(True)
        layout.addWidget(enabled_checkbox)

        # 基础配置
        basic_group = QGroupBox("基础配置")
        basic_layout = QGridLayout(basic_group)
        basic_layout.setContentsMargins(6, 8, 6, 6)
        basic_layout.setSpacing(6)

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

        # 注意：冷却时间已移到时间间隔页面统一管理

        layout.addWidget(basic_group)

        # 检测区域设置
        region_group = QGroupBox("检测区域设置")
        region_layout = QVBoxLayout(region_group)
        region_layout.setContentsMargins(8, 8, 8, 6)
        region_layout.setSpacing(6)

        # 第一行：坐标输入 - 单行文本框
        coords_layout = QHBoxLayout()
        coords_layout.setSpacing(6)
        
        coords_layout.addWidget(QLabel("坐标:"))
        
        # 创建单个坐标输入框
        coord_input = QLineEdit()
        coord_input.setPlaceholderText("x1,y1,x2,y2 或 x,y,r")
        coord_input.setStyleSheet("QLineEdit { padding: 5px; }")
        
        # 从默认值设置初始坐标
        if prefix == "hp":
            coord_input.setText("136,910,213,1004")
        else:
            coord_input.setText("1552,910,1560,1004")
        
        coords_layout.addWidget(coord_input)
        coords_layout.addStretch()
        
        region_layout.addLayout(coords_layout)


        # 第二行：容差设置 - 单行文本框
        tolerance_layout = QHBoxLayout()
        tolerance_layout.setSpacing(6)
        
        tolerance_layout.addWidget(QLabel("容差HSV:"))
        
        # 创建单个容差输入框
        tolerance_input = QLineEdit()
        tolerance_input.setPlaceholderText("h,s,v")
        tolerance_input.setText("10,30,50")
        tolerance_input.setStyleSheet("QLineEdit { padding: 5px; }")
        
        tolerance_layout.addWidget(tolerance_input)
        tolerance_layout.addStretch()
        
        region_layout.addLayout(tolerance_layout)

        # 保存控件引用
        setattr(self, f"{prefix}_coord_input", coord_input)
        setattr(self, f"{prefix}_tolerance_input", tolerance_input)

        # 当前检测模式显示
        mode_label = QLabel()
        mode_label.setStyleSheet("font-size: 10pt; color: #666; margin: 2px 0;")
        region_layout.addWidget(mode_label)

        # 存储模式标签引用
        if prefix == "hp":
            self.hp_mode_label = mode_label
        else:
            self.mp_mode_label = mode_label

        # 更新模式显示
        self._update_detection_mode_display(prefix)

        # 操作按钮区域 - 紧凑布局
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)

        # 区域选择按钮
        select_btn = QPushButton("📦 选择区域")
        select_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 11px;
                padding: 6px 12px;
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
        buttons_layout.addWidget(select_btn)

        # 自动检测球体按钮
        detect_btn = QPushButton("🔍 Detect Orbs")
        detect_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 11px;
                padding: 6px 12px;
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """
        )
        detect_btn.clicked.connect(lambda: self._start_auto_detect_orbs(prefix))
        buttons_layout.addWidget(detect_btn)
        
        buttons_layout.addStretch()
        region_layout.addLayout(buttons_layout)

        layout.addWidget(region_group)

        # 程序配置区域完成，添加分隔线
        separator = QLabel()
        separator.setStyleSheet("border-bottom: 1px solid #ccc; margin: 5px 0;")
        layout.addWidget(separator)

        # 添加一个状态标签用于反馈
        status_label = QLabel("")
        status_label.setStyleSheet("font-size: 10pt; font-weight: bold;")
        layout.addWidget(status_label)

        # 保存控件引用 (冷却时间已移到时间间隔页面)
        widgets = {
            "enabled": enabled_checkbox,
            "key": key_edit,
            "threshold": threshold_spinbox,
            "coord_input": coord_input,
            "tolerance_input": tolerance_input,
            "mode_label": mode_label,
            "select_region_btn": select_btn,
            "detect_orbs_btn": detect_btn,
        }

        if prefix == "hp":
            self.hp_widgets = widgets
        else:
            self.mp_widgets = widgets

        return group

    def _create_global_settings_group(self):
        """创建全局设置组，包含颜色工具区域"""
        main_layout = QVBoxLayout()
        
        # 颜色工具区域（小工具）
        tools_group = QGroupBox("🎨 颜色分析工具 (Color Analysis Tools)")
        tools_group.setStyleSheet(
            """
            QGroupBox {
                font-weight: bold;
                border: 2px solid #9C27B0;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #9C27B0;
                font-weight: bold;
            }
        """
        )
        
        tools_layout = QVBoxLayout(tools_group)
        tools_layout.setContentsMargins(15, 20, 15, 15)
        tools_layout.setSpacing(15)

        # 工具按钮区域
        tools_buttons_layout = QHBoxLayout()
        
        # 单点取色按钮
        pick_btn = QPushButton("🎨 单点取色")
        pick_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 12px;
                padding: 8px 15px;
                background-color: #FF5722;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #E64A19;
            }
        """
        )
        pick_btn.clicked.connect(self._start_single_color_picking)
        tools_buttons_layout.addWidget(pick_btn)
        
        # 区域取HSV平均色和容差按钮
        region_btn = QPushButton("🔍 区域取色")
        region_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 12px;
                padding: 8px 15px;
                background-color: #9C27B0;
                color: white;
                border: none;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
        """
        )
        region_btn.clicked.connect(self._start_region_color_analysis)
        tools_buttons_layout.addWidget(region_btn)
        
        tools_buttons_layout.addStretch()
        tools_layout.addLayout(tools_buttons_layout)


        # 多颜色配置工具
        colors_group = QGroupBox("多颜色配置")
        colors_group.setStyleSheet("QGroupBox { font-weight: bold; color: #666; }")
        colors_layout = QVBoxLayout(colors_group)
        colors_layout.setContentsMargins(8, 12, 8, 8)

        # 颜色配置说明
        colors_info = QLabel(
            "工具用途: 测试多颜色配置格式，每行一个颜色 H,S,V,H容差,S容差,V容差 (OpenCV格式)"
        )
        colors_info.setStyleSheet("color: #666; font-size: 10pt; font-style: italic;")
        colors_layout.addWidget(colors_info)

        # 颜色配置输入框
        colors_input_layout = QHBoxLayout()
        colors_input_layout.addWidget(QLabel("颜色列表:"))

        self.global_colors_edit = QTextEdit()
        self.global_colors_edit.setPlaceholderText("格式：\n每行一个颜色+容差(H,S,V,H容差,S容差,V容差)\n\n例如:\n157,75,29,10,30,50\n40,84,48,15,25,35\n104,80,58,8,20,25")
        self.global_colors_edit.setPlainText("157,75,29,10,30,50\n40,84,48,15,25,35\n104,80,58,8,20,25")
        self.global_colors_edit.setMinimumWidth(300)
        self.global_colors_edit.setMaximumHeight(80)
        self.global_colors_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        colors_input_layout.addWidget(self.global_colors_edit)

        # 解析按钮
        parse_btn = QPushButton("🔍 解析")
        parse_btn.setMaximumWidth(60)
        parse_btn.clicked.connect(self._parse_global_colors)
        colors_input_layout.addWidget(parse_btn)

        colors_layout.addLayout(colors_input_layout)

        # 解析结果显示
        self.global_colors_result = QLabel("")
        self.global_colors_result.setStyleSheet(
            "font-size: 9pt; padding: 5px; background-color: #f5f5f5; border-radius: 3px; min-height: 40px;"
        )
        self.global_colors_result.setWordWrap(True)
        self.global_colors_result.setTextFormat(Qt.RichText)
        colors_layout.addWidget(self.global_colors_result)

        # 连接颜色配置变化事件
        self.global_colors_edit.textChanged.connect(self._parse_global_colors)
        
        tools_layout.addWidget(colors_group)
        main_layout.addWidget(tools_group)

        # 创建容器Widget
        container = QWidget()
        container.setLayout(main_layout)
        return container

    def _get_current_tolerance(self):
        """获取当前容差设置，优先从全局容差获取，否则使用默认值"""
        try:
            # 尝试从全局容差输入框获取
            if hasattr(self, 'tolerance_widgets') and self.tolerance_widgets:
                tolerance_input = self.tolerance_widgets.get("tolerance_input")
                if tolerance_input:
                    tolerance_text = tolerance_input.text().strip()
                    if tolerance_text:
                        values = [int(x.strip()) for x in tolerance_text.split(',') if x.strip()]
                        if len(values) == 3:
                            return values  # [h_tol, s_tol, v_tol]
            
            # 默认容差
            return [10, 30, 50]
        except:
            return [10, 30, 50]
    
    def _add_color_to_list(self, h, s, v, h_tol=None, s_tol=None, v_tol=None):
        """将颜色添加到颜色列表，格式为H,S,V,H容差,S容差,V容差"""
        if h_tol is None or s_tol is None or v_tol is None:
            h_tol, s_tol, v_tol = self._get_current_tolerance()
        
        current_text = self.global_colors_edit.toPlainText().strip()
        new_color = f"{h},{s},{v},{h_tol},{s_tol},{v_tol}"
        
        if current_text:
            updated_text = current_text + "\n" + new_color
        else:
            updated_text = new_color
        
        self.global_colors_edit.setPlainText(updated_text)
        print(f"[颜色添加] 添加颜色到列表: HSV({h},{s},{v}) 容差(±{h_tol},±{s_tol},±{v_tol})")

    def _parse_global_colors(self):
        """解析全局颜色配置工具中的颜色"""
        try:
            colors_text = self.global_colors_edit.toPlainText().strip()
            
            if not colors_text:
                self.global_colors_result.setText("请输入颜色配置")
                return

            # 解析纯颜色列表格式
            lines = [line.strip() for line in colors_text.split('\n') if line.strip()]
            
            if not lines:
                self.global_colors_result.setText("请输入有效的颜色配置")
                return

            # 构建HTML格式的结果文本
            html_parts = [
                f"<div style='margin-bottom: 8px; font-weight: bold;'>✅ 解析成功：{len(lines)}种颜色</div>"
            ]

            for i, line in enumerate(lines):
                try:
                    # 解析单行颜色值 H,S,V,H容差,S容差,V容差
                    values = [int(x.strip()) for x in line.split(',') if x.strip()]
                    
                    if len(values) == 3:
                        # 兼容旧的3值格式 H,S,V，使用默认容差
                        h, s, v = values
                        h_tol, s_tol, v_tol = self._get_current_tolerance()
                    elif len(values) == 6:
                        # 新的6值格式 H,S,V,H容差,S容差,V容差
                        h, s, v, h_tol, s_tol, v_tol = values
                    else:
                        self.global_colors_result.setText(f"❌ 第{i+1}行格式错误：应为H,S,V或H,S,V,H容差,S容差,V容差格式")
                        return
                    
                    # 验证OpenCV HSV范围
                    if not (0 <= h <= 179):
                        self.global_colors_result.setText(f"❌ 第{i+1}行H值({h})超出OpenCV范围(0-179)")
                        return
                    if not (0 <= s <= 255):
                        self.global_colors_result.setText(f"❌ 第{i+1}行S值({s})超出范围(0-255)")
                        return
                    if not (0 <= v <= 255):
                        self.global_colors_result.setText(f"❌ 第{i+1}行V值({v})超出范围(0-255)")
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
                        颜色{i+1}: HSV({h},{s},{v}) 容差(±{h_tol},±{s_tol},±{v_tol}) → RGB({r},{g},{b})
                    </div>
                    """
                    html_parts.append(color_html)
                    
                except ValueError:
                    self.global_colors_result.setText(f"❌ 第{i+1}行数值格式错误：{line}")
                    return

            result_html = "".join(html_parts)
            self.global_colors_result.setText(result_html)

        except Exception as e:
            self.global_colors_result.setText(f"❌ 解析错误：{str(e)}")

    def _start_color_analysis(self):
        """开始颜色分析"""
        if not self.main_window:
            return

        # 隐藏主窗口
        self.main_window.hide()

        # 使用QTimer延迟执行，确保界面完全隐藏
        from PySide6.QtCore import QTimer

        def show_dialog():
            # 创建区域选择对话框
            from .region_selection_dialog import RegionSelectionDialog
            dialog = RegionSelectionDialog(None, enable_color_analysis=True)
            dialog.region_analyzed.connect(
                lambda x1, y1, x2, y2, analysis: self._handle_region_analysis(
                    x1, y1, x2, y2, analysis
                )
            )

            # 执行对话框（showEvent会自动处理焦点）
            result = dialog.exec()

            # 恢复显示主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()

        QTimer.singleShot(100, show_dialog)

    def _handle_region_analysis(self, x1: int, y1: int, x2: int, y2: int, analysis: dict):
        """处理区域分析结果"""
        try:
            if not analysis:
                return

            # 直接添加到颜色列表
            if 'average_hsv' in analysis:
                h, s, v = analysis['average_hsv']
                current_text = self.global_colors_edit.toPlainText().strip()
                new_color = f"{h},{s},{v}"
                if current_text:
                    updated_text = current_text + "\n" + new_color
                else:
                    updated_text = new_color
                self.global_colors_edit.setPlainText(updated_text)

        except Exception as e:
            print(f"分析错误：{str(e)}")

    def _start_single_color_picking(self):
        """开始单点取色，直接添加到颜色列表"""
        try:
            from .color_picker_dialog import ColorPickingDialog
            
            def on_color_picked(r, g, b):
                # 转换为HSV并添加到颜色列表
                import cv2
                import numpy as np
                rgb_array = np.uint8([[[r, g, b]]])
                hsv_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2HSV)
                h, s, v = hsv_array[0][0]
                
                # 添加到颜色列表（包含容差）
                self._add_color_to_list(int(h), int(s), int(v))
                print(f"[单点取色] 获取颜色: RGB({r},{g},{b}) -> HSV({h},{s},{v})")
            
            # 创建取色器
            picker = ColorPickingDialog()
            picker.color_picked.connect(on_color_picked)
            picker.exec()
            
        except Exception as e:
            print(f"取色错误：{str(e)}")

    def _start_region_color_analysis(self):
        """开始区域取HSV平均色和容差，直接添加到颜色列表"""
        if not self.main_window:
            return

        # 隐藏主窗口
        self.main_window.hide()

        # 使用QTimer延迟执行，确保界面完全隐藏
        from PySide6.QtCore import QTimer

        def show_dialog():
            # 创建区域选择对话框
            from .region_selection_dialog import RegionSelectionDialog
            dialog = RegionSelectionDialog(None)
            
            def on_region_analyzed(x1, y1, x2, y2, analysis):
                if analysis and 'average_hsv' in analysis:
                    h, s, v = analysis['average_hsv']
                    # 使用分析结果中的建议容差（如果有的话）
                    if 'suggested_tolerances' in analysis:
                        suggested = analysis['suggested_tolerances']
                        h_tol = suggested.get('h', 10)
                        s_tol = suggested.get('s', 30) 
                        v_tol = suggested.get('v', 50)
                        self._add_color_to_list(int(h), int(s), int(v), h_tol, s_tol, v_tol)
                    else:
                        self._add_color_to_list(int(h), int(s), int(v))
                    print(f"[区域分析] 获取平均颜色: HSV({h},{s},{v})")
            
            dialog.region_analyzed.connect(on_region_analyzed)
            
            # 执行对话框
            result = dialog.exec()

            # 恢复显示主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()

        QTimer.singleShot(100, show_dialog)

    def _hsv_to_rgb(self, h: int, s: int, v: int) -> tuple:
        """将OpenCV HSV颜色转换为RGB (使用OpenCV确保一致性)"""
        import cv2
        import numpy as np

        # 输入的h,s,v已经是OpenCV格式 (H: 0-179, S: 0-255, V: 0-255)
        hsv_array = np.array([[[h, s, v]]], dtype=np.uint8)
        rgb_array = cv2.cvtColor(hsv_array, cv2.COLOR_HSV2RGB)
        r, g, b = rgb_array[0][0]

        return int(r), int(g), int(b)

    def _get_contrast_color(self, r: int, g: int, b: int) -> str:
        """根据背景色亮度返回合适的文字颜色"""
        # 计算亮度 (使用相对亮度公式)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#ffffff" if luminance < 0.5 else "#000000"

    def _parse_colors_to_list(self, colors_text: str) -> list:
        """解析颜色文本为颜色列表（纯颜色格式，使用全局容差）"""
        try:
            if not colors_text.strip():
                return []

            # 解析纯颜色列表格式 (每行一个颜色 H,S,V)
            lines = [line.strip() for line in colors_text.split('\n') if line.strip()]
            colors = []
            
            for line in lines:
                # 解析单行颜色值 H,S,V
                values = [int(x.strip()) for x in line.split(',') if x.strip()]
                
                if len(values) != 3:
                    continue  # 跳过格式错误的行
                
                h, s, v = values
                
                # 获取全局容差设置
                if hasattr(self, 'tolerance_widgets') and self.tolerance_widgets:
                    h_tolerance = self.tolerance_widgets["h"].value()
                    s_tolerance = self.tolerance_widgets["s"].value()
                    v_tolerance = self.tolerance_widgets["v"].value()
                else:
                    h_tolerance, s_tolerance, v_tolerance = 10, 30, 50  # 默认容差
                
                colors.append({
                    "h": h, "s": s, "v": v,
                    "h_tolerance": h_tolerance,
                    "s_tolerance": s_tolerance,
                    "v_tolerance": v_tolerance
                })
            
            return colors
            
        except ValueError:
            return []
        except Exception:
            return []

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

    def _get_cooldown_from_timing_settings(self, cooldown_type: str) -> int:
        """从时间间隔设置获取冷却时间值"""
        if (self.main_window and 
            hasattr(self.main_window, 'timing_settings') and 
            hasattr(self.main_window.timing_settings, 'timing_spinboxes')):
            timing_config = self.main_window.timing_settings.get_config()
            if cooldown_type == "hp":
                return timing_config.get("hp_cooldown", 5000)
            elif cooldown_type == "mp":
                return timing_config.get("mp_cooldown", 8000)
        # 如果无法获取，返回默认值
        return 5000 if cooldown_type == "hp" else 8000

    def _build_hp_config(self) -> Dict[str, Any]:
        """构建HP配置，支持矩形和圆形两种独立配置方式"""
        # 基础配置（冷却时间从时间间隔页面获取）
        hp_config = {
            "enabled": self.hp_widgets["enabled"].isChecked(),
            "key": self.hp_widgets["key"].text().strip(),
            "threshold": self.hp_widgets["threshold"].value(),
            "cooldown": self._get_cooldown_from_timing_settings("hp"),
        }

        # 添加容差配置
        if hasattr(self, 'tolerance_widgets'):
            hp_config.update({
                "tolerance_h": self.tolerance_widgets["h"].value(),
                "tolerance_s": self.tolerance_widgets["s"].value(),
                "tolerance_v": self.tolerance_widgets["v"].value(),
            })

        # 根据检测模式保存相应配置
        if self.hp_detection_mode == "circle" and self.hp_circle_config:
            # 使用圆形配置
            hp_config.update({
                "detection_mode": "circle",
                "center_x": self.hp_circle_config.get("hp", {}).get("center_x"),
                "center_y": self.hp_circle_config.get("hp", {}).get("center_y"),
                "radius": self.hp_circle_config.get("hp", {}).get("radius"),
            })
            print(f"[配置构建] HP使用圆形配置: 圆心({hp_config['center_x']},{hp_config['center_y']}), 半径{hp_config['radius']}")
        else:
            # 使用矩形配置，从单行文本框解析坐标
            coord_input = self.hp_widgets.get("coord_input")
            if coord_input:
                coord_text = coord_input.text().strip()
                try:
                    coords = [int(x.strip()) for x in coord_text.split(',')]
                    if len(coords) >= 4:
                        x1, y1, x2, y2 = coords[0], coords[1], coords[2], coords[3]
                    else:
                        # 默认坐标
                        x1, y1, x2, y2 = 136, 910, 213, 1004
                except:
                    # 解析失败，使用默认坐标
                    x1, y1, x2, y2 = 136, 910, 213, 1004
            else:
                # 没有找到坐标输入框，使用默认坐标
                x1, y1, x2, y2 = 136, 910, 213, 1004
                
            hp_config.update({
                "detection_mode": "rectangle",
                "region_x1": x1,
                "region_y1": y1,
                "region_x2": x2,
                "region_y2": y2,
            })
            print(f"[配置构建] HP使用矩形配置: ({x1},{y1}) -> ({x2},{y2})")

        # 从颜色配置输入框解析颜色列表
        # 使用默认HP颜色配置（红色+绿色）
        default_hp_colors = "157,75,29\n40,84,48"
        colors = self._parse_colors_to_list(default_hp_colors)
        hp_config["colors"] = colors

        return hp_config

    def _parse_colors_to_list(self, colors_text: str) -> list:
        """将颜色配置文本解析为颜色列表（纯颜色列表格式）"""
        colors = []
        try:
            # 按行分割，每行一个颜色
            lines = [line.strip() for line in colors_text.strip().split('\n') if line.strip()]
            
            # 从容差控件获取容差值
            h_tol = 10  # 默认值
            s_tol = 20
            v_tol = 20
            
            if hasattr(self, 'tolerance_widgets') and self.tolerance_widgets:
                h_tol = self.tolerance_widgets["h"].value()
                s_tol = self.tolerance_widgets["s"].value()
                v_tol = self.tolerance_widgets["v"].value()
            
            # 解析每行的颜色值
            for i, line in enumerate(lines, 1):
                color_values = [int(x.strip()) for x in line.split(",") if x.strip()]
                if len(color_values) == 3:
                    h, s, v = color_values
                    color = {
                        "name": f"Color{i}",
                        "target_h": h,
                        "target_s": s,
                        "target_v": v,
                        "tolerance_h": h_tol,
                        "tolerance_s": s_tol,
                        "tolerance_v": v_tol,
                    }
                    colors.append(color)
        except:
            pass
        
        # 如果解析失败，返回默认配置
        if not colors:
            # 使用默认容差值
            if hasattr(self, 'tolerance_widgets') and self.tolerance_widgets:
                h_tol = self.tolerance_widgets["h"].value()
                s_tol = self.tolerance_widgets["s"].value()
                v_tol = self.tolerance_widgets["v"].value()
            else:
                h_tol, s_tol, v_tol = 10, 20, 20
                
            colors = [
                {
                    "name": "Default",
                    "target_h": 314,
                    "target_s": 75,
                    "target_v": 29,
                    "tolerance_h": h_tol,
                    "tolerance_s": s_tol,
                    "tolerance_v": v_tol,
                }
            ]

        return colors

    def _build_mp_config(self) -> Dict[str, Any]:
        """构建MP配置，支持矩形和圆形两种独立配置方式"""
        # 基础配置（冷却时间从时间间隔页面获取）
        mp_config = {
            "enabled": self.mp_widgets["enabled"].isChecked(),
            "key": self.mp_widgets["key"].text().strip(),
            "threshold": self.mp_widgets["threshold"].value(),
            "cooldown": self._get_cooldown_from_timing_settings("mp"),
        }

        # 添加容差配置
        if hasattr(self, 'tolerance_widgets'):
            mp_config.update({
                "tolerance_h": self.tolerance_widgets["h"].value(),
                "tolerance_s": self.tolerance_widgets["s"].value(),
                "tolerance_v": self.tolerance_widgets["v"].value(),
            })

        # 根据检测模式保存相应配置
        if self.mp_detection_mode == "circle" and self.mp_circle_config:
            # 使用圆形配置
            mp_config.update({
                "detection_mode": "circle",
                "center_x": self.mp_circle_config.get("mp", {}).get("center_x"),
                "center_y": self.mp_circle_config.get("mp", {}).get("center_y"),
                "radius": self.mp_circle_config.get("mp", {}).get("radius"),
            })
            print(f"[配置构建] MP使用圆形配置: 圆心({mp_config['center_x']},{mp_config['center_y']}), 半径{mp_config['radius']}")
        else:
            # 使用矩形配置，从单行文本框解析坐标
            coord_input = self.mp_widgets.get("coord_input")
            if coord_input:
                coord_text = coord_input.text().strip()
                try:
                    coords = [int(x.strip()) for x in coord_text.split(',')]
                    if len(coords) >= 4:
                        x1, y1, x2, y2 = coords[0], coords[1], coords[2], coords[3]
                    else:
                        # 默认坐标
                        x1, y1, x2, y2 = 1552, 910, 1560, 1004
                except:
                    # 解析失败，使用默认坐标
                    x1, y1, x2, y2 = 1552, 910, 1560, 1004
            else:
                # 没有找到坐标输入框，使用默认坐标
                x1, y1, x2, y2 = 1552, 910, 1560, 1004
                
            mp_config.update({
                "detection_mode": "rectangle",
                "region_x1": x1,
                "region_y1": y1,
                "region_x2": x2,
                "region_y2": y2,
            })
            print(f"[配置构建] MP使用矩形配置: ({x1},{y1}) -> ({x2},{y2})")

        # 从颜色配置输入框解析颜色列表
        # 使用默认MP颜色配置（蓝色）
        default_mp_colors = "104,80,58"
        colors = self._parse_colors_to_list(default_mp_colors)
        mp_config["colors"] = colors

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

        # HP配置 - 支持圆形和矩形配置
        hp_config = res_config.get("hp_config", {})
        if self.hp_widgets:
            self.hp_widgets["enabled"].setChecked(hp_config.get("enabled", True))
            self.hp_widgets["key"].setText(hp_config.get("key", "1"))
            self.hp_widgets["threshold"].setValue(
                hp_config.get("threshold", 50)
            )  # 默认50%

            # 根据检测模式加载相应配置
            detection_mode = hp_config.get("detection_mode", "rectangle")
            center_x = hp_config.get("center_x")
            center_y = hp_config.get("center_y")
            radius = hp_config.get("radius")

            if detection_mode == "circle" and center_x is not None and center_y is not None and radius is not None:
                # 加载圆形配置
                self.hp_detection_mode = "circle"
                circle_data = {"center_x": center_x, "center_y": center_y, "radius": radius}
                self.hp_circle_config = {"hp": circle_data}
                self._update_detection_mode_display("hp", circle_data)
            else:
                # 如果没有有效坐标或不是圆形模式，切换回矩形模式
                self.hp_detection_mode = "rectangle"
                self._update_detection_mode_display("hp")

                # 加载矩形配置到单行文本框
                x1 = hp_config.get("region_x1", 136)  # 1080P血药区域
                y1 = hp_config.get("region_y1", 910)
                x2 = hp_config.get("region_x2", 213)
                y2 = hp_config.get("region_y2", 1004)
                
                coord_input = self.hp_widgets.get("coord_input")
                if coord_input:
                    coord_input.setText(f"{x1},{y1},{x2},{y2}")
                    
                print(f"[配置加载] HP矩形配置: ({x1},{y1}) -> ({x2},{y2})")

            # 加载颜色配置
            colors_text = self._colors_list_to_text(hp_config.get("colors", []))
            if not colors_text:
                # 如果没有colors配置，使用默认值
                colors_text = "10,20,20\n157,75,29\n40,84,48"  # HP默认：容差+红色+绿色

            # 注意：颜色配置现在在全局颜色工具区域管理，不再在单独的HP控件中
            # self.hp_widgets["colors_edit"].setPlainText(colors_text)
            self._parse_colors_input("hp", colors_text)

        # MP配置 - 支持圆形和矩形配置
        mp_config = res_config.get("mp_config", {})
        if self.mp_widgets:
            self.mp_widgets["enabled"].setChecked(mp_config.get("enabled", True))
            self.mp_widgets["key"].setText(mp_config.get("key", "2"))
            self.mp_widgets["threshold"].setValue(
                mp_config.get("threshold", 50)
            )  # 默认50%

            # 根据检测模式加载相应配置
            detection_mode = mp_config.get("detection_mode", "rectangle")
            center_x = mp_config.get("center_x")
            center_y = mp_config.get("center_y")
            radius = mp_config.get("radius")

            if detection_mode == "circle" and center_x is not None and center_y is not None and radius is not None:
                # 加载圆形配置
                self.mp_detection_mode = "circle"
                circle_data = {"center_x": center_x, "center_y": center_y, "radius": radius}
                self.mp_circle_config = {"mp": circle_data}
                self._update_detection_mode_display("mp", circle_data)
            else:
                # 如果没有有效坐标或不是圆形模式，切换回矩形模式
                self.mp_detection_mode = "rectangle"
                self._update_detection_mode_display("mp")

                # 加载矩形配置到单行文本框
                x1 = mp_config.get("region_x1", 1552)  # 1080P蓝药区域
                y1 = mp_config.get("region_y1", 910)
                x2 = mp_config.get("region_x2", 1560)
                y2 = mp_config.get("region_y2", 1004)
                
                coord_input = self.mp_widgets.get("coord_input")
                if coord_input:
                    coord_input.setText(f"{x1},{y1},{x2},{y2}")
                    
                print(f"[配置加载] MP矩形配置: ({x1},{y1}) -> ({x2},{y2})")

            # 加载颜色配置
            colors_text = self._colors_list_to_text(mp_config.get("colors", []))
            if not colors_text:
                # 如果没有colors配置，使用默认值
                colors_text = "5,5,5\n104,80,58"  # MP默认：容差+蓝色

            # 注意：颜色配置现在在全局颜色工具区域管理，不再在单独的MP控件中
            # self.mp_widgets["colors_edit"].setPlainText(colors_text)
            self._parse_colors_input("mp", colors_text)

        # 更新全局设置（检测间隔现在在时间间隔页面管理）
        check_interval = res_config.get("check_interval", 200)
        # 注意：检测间隔现在在时间间隔页面管理，不再在资源管理页面设置
        # if hasattr(self, "check_interval_spinbox"):
        #     self.check_interval_spinbox.setValue(check_interval)

        # 更新容差设置（从HP或MP配置中取第一个有效值，默认使用HP配置的容差）
        tolerance_h = hp_config.get("tolerance_h", 10)
        tolerance_s = hp_config.get("tolerance_s", 20) 
        tolerance_v = hp_config.get("tolerance_v", 20)
        
        if hasattr(self, 'tolerance_widgets') and self.tolerance_widgets:
            tolerance_input = self.tolerance_widgets.get("tolerance_input")
            if tolerance_input:
                tolerance_input.setText(f"{tolerance_h},{tolerance_s},{tolerance_v}")
                print(f"[配置加载] HSV容差配置: H={tolerance_h}, S={tolerance_s}, V={tolerance_v}")

    def _colors_list_to_text(self, colors_list: list) -> str:
        """将颜色列表转换为文本格式（纯颜色列表格式）"""
        if not colors_list:
            return ""

        # 构建颜色行
        color_lines = []
        for color in colors_list:
            color_line = f"{color.get('target_h', 0)},{color.get('target_s', 75)},{color.get('target_v', 29)}"
            color_lines.append(color_line)
        
        # 返回纯颜色列表，每行一个颜色
        return "\n".join(color_lines)



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
                current_text = colors_edit.toPlainText().strip()

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

                colors_edit.setPlainText(updated_text)

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

    def _start_auto_detect_orbs(self, prefix: str):
        """开始自动检测球体，使用状态标签进行反馈"""
        if not self.main_window:
            return

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        status_label = widgets.get("status_label")
        if not status_label:
            return

        # 1. 立即更新UI显示“正在检测...”
        status_label.setText("正在检测...")
        status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #007BFF;")
        QApplication.processEvents() # 强制UI刷新

        try:
            # 2. 调用后台检测逻辑
            if hasattr(self.main_window, 'macro_engine') and hasattr(self.main_window.macro_engine, 'resource_manager'):
                result = self.main_window.macro_engine.resource_manager.auto_detect_orbs(orb_type=prefix)

                if result and (prefix in result):
                    # 3. 检测成功
                    self._on_orbs_detected(prefix, result)
                    status_label.setText("✅ 检测成功！")
                    status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #28a745;")
                else:
                    # 4. 检测失败
                    status_label.setText("❌ 检测失败，请重试")
                    status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #DC3545;")
            else:
                status_label.setText("❌ 错误: 无法访问资源管理器")
                status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #DC3545;")

        except Exception as e:
            status_label.setText(f"❌ 检测出错: {str(e)[:30]}...")
            status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #DC3545;")

        # 5. 3秒后自动清除状态信息
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: status_label.setText(""))

    def _start_region_selection(self, prefix: str):
        """开始区域选择（选择区域并自动分析颜色）"""
        if not self.main_window:
            return

        # 使用QTimer延迟执行，确保界面完全隐藏
        from PySide6.QtCore import QTimer

        def show_dialog():
            # 创建区域选择对话框
            dialog = RegionSelectionDialog(None)  # 默认启用颜色分析
            dialog.region_selected.connect(
                lambda x1, y1, x2, y2: self._on_region_selected(prefix, x1, y1, x2, y2)
            )
            dialog.region_analyzed.connect(
                lambda x1, y1, x2, y2, analysis: self._on_region_analyzed(
                    prefix, x1, y1, x2, y2, analysis
                )
            )

            # 直接执行对话框，无需额外提示
            result = dialog.exec()

            # 恢复显示主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()

        # 完全隐藏主界面，就像截图工具一样
        self.main_window.hide()

        # 延迟200ms执行对话框显示，确保主窗口完全隐藏
        QTimer.singleShot(200, show_dialog)



    def _update_detection_mode_display(self, prefix: str, circle_config: Optional[Dict] = None):
        """更新检测模式显示，并附带坐标信息"""
        mode = self.hp_detection_mode if prefix == "hp" else self.mp_detection_mode
        label = self.hp_mode_label if prefix == "hp" else self.mp_mode_label

        if mode == "circle":
            if circle_config:
                cx = circle_config.get("center_x", "N/A")
                cy = circle_config.get("center_y", "N/A")
                r = circle_config.get("radius", "N/A")
                label.setText(f"🔵 当前模式：圆形检测 (圆心: {cx},{cy} | 半径: {r})")
            else:
                label.setText("🔵 当前模式：圆形检测 (无具体坐标)")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #28a745;")
        else:
            label.setText("⬛ 当前模式：矩形检测（手动选择区域）")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #17a2b8;")

    def _on_orbs_detected(self, prefix: str, detection_result: Dict[str, Dict[str, Any]]):
        """球体检测完成回调 - 设置为圆形检测模式"""
        orb_count = len(detection_result)
        print(f"[球体检测] 检测完成，共找到 {orb_count} 个球体")

        # 设置检测模式为圆形
        if prefix == "hp":
            self.hp_detection_mode = "circle"
            self.hp_circle_config = detection_result.copy()
        else:
            self.mp_detection_mode = "circle"
            self.mp_circle_config = detection_result.copy()

        # 更新UI显示，并传入检测到的坐标
        orb_data_for_prefix = detection_result.get(prefix)
        self._update_detection_mode_display(prefix, orb_data_for_prefix)

        for orb_key, orb_data in detection_result.items():
            center_x = orb_data["center_x"]
            center_y = orb_data["center_y"]
            radius = orb_data["radius"]
            print(f"[球体检测] {orb_key.upper()}球体: 圆心({center_x},{center_y}), 半径{radius}")
            
            # 如果检测结果包含颜色信息，添加到颜色列表
            if "color" in orb_data:
                color_info = orb_data["color"]
                if "h" in color_info and "s" in color_info and "v" in color_info:
                    h = color_info["h"]
                    s = color_info["s"]
                    v = color_info["v"]
                    # 使用建议的容差或默认值
                    h_tol = color_info.get("h_tolerance", 10)
                    s_tol = color_info.get("s_tolerance", 30)
                    v_tol = color_info.get("v_tolerance", 50)
                    
                    if hasattr(self, 'global_colors_edit'):
                        self._add_color_to_list(int(h), int(s), int(v), h_tol, s_tol, v_tol)
                        print(f"[球体检测] 添加{orb_key}颜色到列表: HSV({h},{s},{v}) 容差(±{h_tol},±{s_tol},±{v_tol})")

        print(f"[球体检测] {prefix.upper()}已设置为圆形检测模式")

    def _on_color_analysis_result(
        self, prefix: str, x1: int, y1: int, x2: int, y2: int, analysis: dict
    ):
        """颜色分析结果处理（作为辅助工具）"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        analysis_widget = widgets["analysis_result"]
        
        # 获取分析结果
        dominant_color = analysis.get("dominant_color", {})
        suggested_tolerances = analysis.get("suggested_tolerances", {})
        
        # 显示分析结果
        result_text = f"""🎨 <b>颜色分析结果</b> （区域: {x2-x1}x{y2-y1}）
<br><b>主色调 HSV:</b> H={dominant_color.get('h', 0)}, S={dominant_color.get('s', 0)}, V={dominant_color.get('v', 0)}
<br><b>建议容差:</b> H±{suggested_tolerances.get('h', 10)}, S±{suggested_tolerances.get('s', 20)}, V±{suggested_tolerances.get('v', 30)}
<br><small style="color: #888;">ℹ️ 可参考此值调整上方的容差配置</small>"""
        
        analysis_widget.setText(result_text)
        analysis_widget.setStyleSheet("color: #333; font-size: 10pt; padding: 8px; border: 1px solid #28a745; border-radius: 3px; background-color: #f8fff8;")
        
        print(f"🎨 颜色分析完成！")
        print(f"  区域: ({x1},{y1}) -> ({x2},{y2})")
        print(f"  主色调: HSV({dominant_color.get('h', 0)}, {dominant_color.get('s', 0)}, {dominant_color.get('v', 0)})")
        print(f"  建议容差: H±{suggested_tolerances.get('h', 10)}, S±{suggested_tolerances.get('s', 20)}, V±{suggested_tolerances.get('v', 30)}")

    def _on_region_selected(self, prefix: str, x1: int, y1: int, x2: int, y2: int):
        """区域选择完成回调"""
        # 设置检测模式为矩形
        if prefix == "hp":
            self.hp_detection_mode = "rectangle"
        else:
            self.mp_detection_mode = "rectangle"

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        
        # 设置坐标到单行文本框
        coord_input = widgets.get("coord_input")
        if coord_input:
            coord_input.setText(f"{x1},{y1},{x2},{y2}")
            print(f"[区域选择] {prefix.upper()}区域设置为: ({x1},{y1}) -> ({x2},{y2})")

        # 更新UI显示
        self._update_detection_mode_display(prefix)

        print(f"[区域更新] {prefix.upper()}已设置为矩形检测模式: ({x1},{y1}) -> ({x2},{y2})")

    def _on_region_analyzed(
        self, prefix: str, x1: int, y1: int, x2: int, y2: int, analysis: dict
    ):
        """智能颜色分析完成回调"""
        if not analysis or not analysis.get("analysis_success"):
            return

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 🎯 关键修复：更新检测区域坐标为用户最后选择的区域到单行文本框
        coord_input = widgets.get("coord_input")
        if coord_input:
            coord_input.setText(f"{x1},{y1},{x2},{y2}")
            print(f"[区域更新] {prefix.upper()}检测区域已更新为: ({x1},{y1}) -> ({x2},{y2})")

        # 获取分析结果
        mean_h, mean_s, mean_v = analysis["mean_hsv"]
        tolerance_h, tolerance_s, tolerance_v = analysis["tolerance"]
        total_pixels = analysis["total_pixels"]
        region_size = analysis["region_size"]

        # 使用分析结果中的容差值添加颜色到列表
        if hasattr(self, 'global_colors_edit'):
            self._add_color_to_list(int(mean_h), int(mean_s), int(mean_v), 
                                  int(tolerance_h), int(tolerance_s), int(tolerance_v))
            print(f"[选择区域] 颜色分析完成: HSV({mean_h},{mean_s},{mean_v}) 容差(±{tolerance_h},±{tolerance_s},±{tolerance_v})")

        # 自动解析并显示
        colors_text = self.global_colors_edit.toPlainText().strip()
        self._parse_colors_input(prefix, colors_text)

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
        print(f"✅ 已追加到颜色配置")
        print("=" * 50)
