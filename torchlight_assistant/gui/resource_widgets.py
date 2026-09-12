#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资源管理相关UI组件 - 重构版本"""

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
from copy import deepcopy
from typing import Dict, Any, Optional
from ..utils.debug_log import LOG_INFO, LOG

from .custom_widgets import (
    ConfigSpinBox,
    ConfigLineEdit,
    ConfigComboBox,
    ConfigCheckBox,
)
from .color_picker_dialog import ColorPickingDialog
from .region_selection_dialog import RegionSelectionDialog
from .resource_config_manager import ResourceConfigManager
from .color_analysis_tools import ColorAnalysisTools, ColorListManager


class ResourceManagementWidget(QWidget):
    """POE智能药剂助手 - 配置HP/MP技能"""

    def __init__(self):
        super().__init__()
        self.hp_widgets = {}
        self.mp_widgets = {}
        self.main_window = None  # 引用主窗口，用于隐藏/显示
        self._resource_config_snapshot: Dict[str, Any] = {}

        # 检测模式跟踪
        self.hp_detection_mode = "rectangle"  # "rectangle" 或 "circle"
        self.mp_detection_mode = "rectangle"  # "rectangle" 或 "circle"

        # 圆形配置存储
        self.hp_circle_config = {}
        self.mp_circle_config = {}

        # 初化工具类
        self.color_analysis_tools = ColorAnalysisTools()
        self.color_list_manager = None  # 在_setup_ui中初化

        self._setup_ui()

    def _auto_parse_initial_colors(self):
        """自动解析初始颜色配置并显示背景条"""
        try:
            # 确保global_colors_edit已经创建并有内容
            if hasattr(self, 'global_colors_edit'):
                colors_text = self.global_colors_edit.toPlainText().strip()
                if colors_text:
                    # 调用解析函数显示背景条
                    self._parse_global_colors()
                    LOG_INFO("[UI初始化] 自动解析默认颜色配置并显示背景条")
                else:
                    LOG_INFO("[UI初始化] 警告：颜色配置文本框为空")
            else:
                LOG_INFO("[UI初始化] 警告：global_colors_edit未找到")
        except Exception as e:
            LOG_INFO(f"[UI初始化] 自动解析颜色配置失败: {str(e)}")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # 移除标题和说明文本，保持界面简洁

        # 药剂配置区域
        flask_group = QGroupBox("「通用」药剂技能配置 (Flask Skills,两种模式都生效)")
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
        
        # 🚀 UI初始化完成后自动解析颜色配置，显示默认背景条
        # 使用QTimer延迟执行，确保所有UI组件都已创建完成
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._auto_parse_initial_colors)

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
        # 缺失配置必须 fail-closed。update_from_config 会显式加载已有值。
        enabled_checkbox.setChecked(False)
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
        key_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

        # 检测模式选择
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(6)
        mode_layout.addWidget(QLabel("检测模式:"))

        mode_combo = ConfigComboBox()
        mode_combo.addItem("矩形对比 (Rectangle)", "rectangle")
        mode_combo.addItem("圆形对比 (Circle)", "circle")
        mode_combo.addItem("数字匹配 (Text OCR)", "text_ocr")
        mode_combo.setCurrentIndex(0)  # 默认矩形模式
        mode_combo.currentIndexChanged.connect(
            lambda: self._on_detection_mode_changed(prefix)
        )
        mode_layout.addWidget(mode_combo)
        mode_layout.addStretch()

        region_layout.addLayout(mode_layout)

        # OCR引擎选择（仅在text_ocr模式显示）
        ocr_engine_layout = QHBoxLayout()
        ocr_engine_layout.setSpacing(6)
        ocr_engine_layout.addWidget(QLabel("OCR引擎:"))

        ocr_engine_combo = ConfigComboBox()
        ocr_engine_combo.addItem("PaddleOCR (推荐/最快)", "paddle")
        ocr_engine_combo.addItem("模板匹配", "template")
        ocr_engine_combo.addItem("Keras模型 (高准确率)", "keras")
        ocr_engine_combo.addItem("Tesseract", "tesseract")
        ocr_engine_combo.setCurrentIndex(0)  # 默认 PaddleOCR
        ocr_engine_combo.setToolTip(
            "PaddleOCR: 直接读'当前/最大'数字(PP-OCRv6 rec-only), 免疫中毒变色; 按F8锁定数字框位置。\n"
            "默认 CPU(~21ms, 不抢游戏显卡); 配置里设 ocr_device=gpu 可~6ms。模型可配 ocr_model(small/medium)。\n"
            "模板匹配: 最快(~7ms), 无额外依赖\nKeras: 最高准确率(99%), 需要TensorFlow\nTesseract: 通用性强, 需要Tesseract"
        )
        ocr_engine_layout.addWidget(ocr_engine_combo)
        ocr_engine_layout.addStretch()

        # 默认隐藏，只在text_ocr模式显示
        ocr_engine_label = ocr_engine_layout.itemAt(0).widget()
        ocr_engine_label.setVisible(False)
        ocr_engine_combo.setVisible(False)

        region_layout.addLayout(ocr_engine_layout)

        # 第一行：坐标输入 - 单行文本框
        coords_layout = QHBoxLayout()
        coords_layout.setSpacing(6)

        coords_layout.addWidget(QLabel("坐标:"))

        # 创建单个坐标输入框
        coord_input = QLineEdit()
        coord_input.setPlaceholderText(
            "矩形: x1,y1,x2,y2 | 圆形: x,y,r | 文本: x1,y1,x2,y2"
        )
        coord_input.setStyleSheet("QLineEdit { padding: 5px; }")

        # 从默认值设置初始坐标
        if prefix == "hp":
            coord_input.setText("136,910,213,1004")
        else:
            coord_input.setText("1552,910,1560,1004")

        coords_layout.addWidget(coord_input)
        coords_layout.addStretch()

        region_layout.addLayout(coords_layout)

        # 第二行：容差设置 - 单行文本框（仅在非 text_ocr 模式显示）
        tolerance_layout = QHBoxLayout()
        tolerance_layout.setSpacing(6)

        tolerance_label = QLabel("容差HSV:")
        tolerance_layout.addWidget(tolerance_label)

        # 创建单个容差输入框
        tolerance_input = QLineEdit()
        tolerance_input.setPlaceholderText("h,s,v")
        tolerance_input.setText("10,30,50")
        tolerance_input.setStyleSheet("QLineEdit { padding: 5px; }")

        tolerance_layout.addWidget(tolerance_input)
        tolerance_layout.addStretch()

        region_layout.addLayout(tolerance_layout)

        # 保存控件引用（包括容差标签和输入框）
        setattr(self, f"{prefix}_coord_input", coord_input)
        setattr(self, f"{prefix}_tolerance_label", tolerance_label)
        setattr(self, f"{prefix}_tolerance_input", tolerance_input)
        setattr(self, f"{prefix}_tolerance_layout", tolerance_layout)

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
        select_btn.clicked.connect(lambda: self._start_region_selection_for_coords(prefix))
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

        # Text OCR测试按钮（初始隐藏，仅在text_ocr模式显示）
        test_ocr_btn = QPushButton("🧪 测试识别")
        test_ocr_btn.setStyleSheet(
            """
            QPushButton {
                font-size: 11px;
                padding: 6px 12px;
                background-color: #ffc107;
                color: #000;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #e0a800;
            }
        """
        )
        test_ocr_btn.clicked.connect(lambda: self._test_text_ocr(prefix))
        test_ocr_btn.setVisible(False)  # 默认隐藏
        buttons_layout.addWidget(test_ocr_btn)

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
            "mode_combo": mode_combo,
            "ocr_engine_combo": ocr_engine_combo,
            "ocr_engine_label": ocr_engine_label,
            "select_region_btn": select_btn,
            "detect_orbs_btn": detect_btn,
            "test_ocr_btn": test_ocr_btn,
            "status_label": status_label,
        }

        if prefix == "hp":
            self.hp_widgets = widgets
        else:
            self.mp_widgets = widgets

        # 初始化按钮显示状态（默认rectangle模式）
        current_mode = (
            self.hp_detection_mode if prefix == "hp" else self.mp_detection_mode
        )
        if current_mode == "rectangle":
            select_btn.setVisible(True)
            detect_btn.setVisible(False)
            test_ocr_btn.setVisible(False)

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

        # 取色结果仅用于辅助调节各资源的 tolerance_*；不是独立检测规则。
        colors_group = QGroupBox("取色参考（不参与运行时判定）")
        colors_group.setStyleSheet("QGroupBox { font-weight: bold; color: #666; }")
        colors_layout = QVBoxLayout(colors_group)
        colors_layout.setContentsMargins(8, 12, 8, 8)

        # 颜色配置说明
        colors_info = QLabel(
            "工具用途: 临时显示取色结果（每行 H,S,V）；真正运行参数是上方各资源的容差"
        )
        colors_info.setStyleSheet("color: #666; font-size: 10pt; font-style: italic;")
        colors_layout.addWidget(colors_info)

        # 颜色配置输入框
        colors_input_layout = QHBoxLayout()
        colors_input_layout.addWidget(QLabel("颜色列表:"))

        self.global_colors_edit = QTextEdit()
        self.global_colors_edit.setPlaceholderText(
            "格式：\n每行一个颜色值(H,S,V)\n\n例如:\n157,75,29\n40,84,48"
        )
        self.global_colors_edit.setPlainText(
            "157,75,29\n40,84,48"
        )
        self.global_colors_edit.setMinimumWidth(300)
        self.global_colors_edit.setMaximumHeight(50)  # 缩小高度，适应最多2行颜色
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
        # 使用枚举类以兼容PySide6类型提示
        self.global_colors_result.setTextFormat(Qt.TextFormat.RichText)
        colors_layout.addWidget(self.global_colors_result)

        # 初始化ColorListManager
        self.color_list_manager = ColorListManager(
            self.global_colors_edit, 
            self.global_colors_result,
            max_colors=2
        )

        # 连接颜色配置变化事件
        self.global_colors_edit.textChanged.connect(self.color_list_manager.parse_colors)

        tools_layout.addWidget(colors_group)
        main_layout.addWidget(tools_group)

        # 初始化颜色分析工具的主窗口引用
        self.color_analysis_tools.main_window = self.main_window

        # 创建容器Widget
        container = QWidget()
        container.setLayout(main_layout)
        return container

    def _get_current_tolerance(self, prefix: str = None):
        """获取HP/MP容差设置"""
        if not prefix:
            return [10, 30, 50]  # 默认容差
            
        try:
            tolerance_input = getattr(self, f"{prefix}_tolerance_input", None)
            if tolerance_input:
                tolerance_text = tolerance_input.text().strip()
                if tolerance_text:
                    values = [int(x.strip()) for x in tolerance_text.split(",") if x.strip()]
                    if len(values) == 3:
                        return values
            return [10, 30, 50]  # 默认容差
        except:
            return [10, 30, 50]

    def _add_color_to_list(self, h, s, v, h_tol=None, s_tol=None, v_tol=None):
        """将HSV颜色值添加到颜色列表 - 使用ColorListManager"""
        if self.color_list_manager:
            self.color_list_manager.add_color_to_list(h, s, v)

    def _parse_global_colors(self):
        """解析全局颜色配置工具中的颜色 - 使用ColorListManager"""
        if self.color_list_manager:
            self.color_list_manager.parse_colors()

    def _start_color_analysis(self):
        """开始颜色分析（与_start_region_color_analysis功能重复，已废弃）"""
        # 🚀 这个函数已经被_start_region_color_analysis取代
        LOG_INFO("[警告] _start_color_analysis已废弃，请使用_start_region_color_analysis")
        self._start_region_color_analysis()

    # 🚀 已删除过时的_handle_region_analysis函数，现在统一使用_add_color_to_list方法

    def _start_single_color_picking(self):
        """开始单点取色 - 使用ColorAnalysisTools"""
        def on_color_picked(h, s, v):
            self._add_color_to_list(h, s, v)
            
        self.color_analysis_tools.start_single_color_picking(on_color_picked)

    def _start_region_color_analysis(self):
        """开始区域取色 - 使用ColorAnalysisTools"""
        def on_region_analyzed(h, s, v, x1, y1, x2, y2, analysis):
            self._add_color_to_list(h, s, v)
            
        self.color_analysis_tools.start_region_color_analysis(on_region_analyzed)

    def _start_region_selection_for_coords(self, prefix: str):
        """开始区域选择，按当前检测模式更新坐标。"""
        if not self.main_window:
            return

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        mode_combo = widgets.get("mode_combo")
        selected_mode = mode_combo.currentData() if mode_combo else "rectangle"

        # 隐藏主窗口
        self.main_window.hide()

        def show_dialog():
            from .region_selection_dialog import RegionSelectionDialog
            
            # Text OCR 不需要 HSV 分析；框选后会用 PaddleOCR 自动收紧文本框。
            dialog = RegionSelectionDialog(analyze_colors=(selected_mode != "text_ocr"))
            
            def on_region_selected(x1, y1, x2, y2):
                self._on_region_selected(prefix, x1, y1, x2, y2)
                if selected_mode == "text_ocr":
                    screenshot_array = dialog.get_screenshot_array()
                    desktop_origin = dialog._desktop_origin
                    from PySide6.QtCore import QTimer

                    QTimer.singleShot(
                        0,
                        lambda: self._calibrate_ocr_region_from_selection(
                            prefix, screenshot_array, (x1, y1, x2, y2),
                            desktop_origin,
                        ),
                    )
            
            def on_region_analyzed(x1, y1, x2, y2, analysis):
                self._on_region_analyzed(prefix, x1, y1, x2, y2, analysis)
                
            dialog.region_selected.connect(on_region_selected)
            # Text OCR 只需要精确文本框坐标；不要把数字区域当 HSV 颜色样本写入资源颜色配置。
            if selected_mode != "text_ocr":
                dialog.region_analyzed.connect(on_region_analyzed)
            
            # 执行对话框
            dialog.exec()
            
            # 恢复显示主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()
        
        # 延迟100ms执行，确保窗口完全隐藏
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, show_dialog)

    def _get_ocr_runtime_options(self, prefix: str) -> tuple:
        """从当前配置读取 OCR 模型、设备和置信度阈值；未保存的新配置使用默认值。"""
        model_name = "PP-OCRv6_small_rec"
        device = "cpu"
        min_score = 0.70
        try:
            if self.main_window and hasattr(self.main_window, "_global_config"):
                global_config = self.main_window._global_config
                raw_resource_management = (
                    global_config.get("resource_management", {})
                    if isinstance(global_config, dict)
                    else {}
                )
                resource_management = (
                    raw_resource_management
                    if isinstance(raw_resource_management, dict)
                    else {}
                )
                raw_resource_config = resource_management.get(
                    f"{prefix}_config", {}
                )
                resource_config = (
                    raw_resource_config
                    if isinstance(raw_resource_config, dict)
                    else {}
                )
                model_name = str(resource_config.get("ocr_model", model_name) or model_name)
                device = str(resource_config.get("ocr_device", device) or device)
                candidate_score = float(
                    resource_config.get("match_threshold", min_score)
                )
                if 0.0 <= candidate_score <= 1.0:
                    min_score = candidate_score
        except Exception as e:
            LOG_INFO(f"[OCR框选] 读取OCR配置失败，使用默认值: {e}")
        return model_name, device, min_score

    def _get_current_tesseract_config(self) -> Dict[str, Any]:
        """读取当前已加载/正在编辑配置，绝不回退去读取 default.json。"""
        try:
            if self.main_window and hasattr(self.main_window, "_global_config"):
                config = self.main_window._global_config.get("tesseract_ocr", {})
                return dict(config) if isinstance(config, dict) else {}
        except Exception as e:
            LOG_INFO(f"[Tesseract测试] 读取当前配置失败，使用内置默认值: {e}")
        return {}

    def _calibrate_ocr_region_from_selection(
        self, prefix: str, screenshot_array, rough_box, desktop_origin=(0, 0)
    ):
        """用 full OCR 将用户粗框自动收紧到合法的 当前/最大 文本行。"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        coord_input = widgets.get("coord_input")
        status_label = widgets.get("status_label")
        ocr_combo = widgets.get("ocr_engine_combo")

        if ocr_combo and ocr_combo.currentData() != "paddle":
            if status_label:
                status_label.setText("OCR区域已更新；自动收紧仅支持 PaddleOCR")
                status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #666;")
            return

        if status_label:
            status_label.setText("PaddleOCR 正在定位数字行...")
            status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #856404;")
            QApplication.processEvents()

        if screenshot_array is None:
            if status_label:
                status_label.setText("OCR定位失败：无法读取框选截图，已保留粗框")
                status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #dc3545;")
            return

        _model_name, device, min_score = self._get_ocr_runtime_options(prefix)
        # 框选信号是虚拟桌面坐标，OCR 裁剪使用单屏截图的局部坐标。
        origin_x, origin_y = desktop_origin
        x1, y1, x2, y2 = rough_box
        local_box = (x1 - origin_x, y1 - origin_y, x2 - origin_x, y2 - origin_y)
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            from ..utils.paddle_ocr_manager import get_paddle_ocr_manager

            mgr = get_paddle_ocr_manager()
            result = mgr.locate_number_box(
                screenshot_array,
                local_box,
                device=device,
                min_score=min_score,
                padding=3,
            )
        except Exception as e:
            LOG_INFO(f"[OCR框选] PaddleOCR定位异常: {e}")
            result = None
        finally:
            QApplication.restoreOverrideCursor()

        if result:
            x1, y1, x2, y2 = result["box"]
            x1, x2 = x1 + origin_x, x2 + origin_x
            y1, y2 = y1 + origin_y, y2 + origin_y
            if coord_input:
                coord_input.setText(f"{x1},{y1},{x2},{y2}")
            if status_label:
                status_label.setText(
                    f"OCR识别: {result['current']}/{result['maximum']} = "
                    f"{result['percentage']:.1f}%  score={result['score']:.2f}"
                )
                status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #28a745;")
            LOG_INFO(
                f"[OCR框选] {prefix.upper()} 已自动收紧到 ({x1},{y1},{x2},{y2}) "
                f"识别={result['text']} pct={result['percentage']:.1f}%"
            )
        else:
            if status_label:
                status_label.setText("OCR未定位到有效 当前/最大，已保留粗框")
                status_label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #dc3545;")
            LOG_INFO(f"[OCR框选] {prefix.upper()} 未找到合法 当前/最大，保留粗框 {rough_box}")

    def _parse_colors_input(self, prefix: str, colors_text: str):
        """解析颜色配置输入并显示带实际颜色的结果"""
        # 获取对应的结果显示控件
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        if not widgets or "colors_result" not in widgets:
            return

        result_label = widgets["colors_result"]

        try:
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

            # 构建 HTML格式的结果文本
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
                r, g, b = ColorAnalysisTools.hsv_to_rgb(h, s, v)
                bg_color = f"rgb({r},{g},{b})"
                text_color = ColorAnalysisTools.get_contrast_color(r, g, b)

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

        except ValueError as e:
            result_label.setText("❌ 格式错误：请输入数字，用逗号分隔")
        except (AttributeError, KeyError) as e:
            result_label.setText(f"❌ 配置错误：{str(e)}")
        except Exception as e:
            result_label.setText(f"❌ 解析错误：{str(e)}")

    def _get_cooldown_from_timing_settings(self, cooldown_type: str) -> int:
        """从时间间隔设置获取冷却时间值"""
        if (
            self.main_window
            and hasattr(self.main_window, "timing_settings")
            and hasattr(self.main_window.timing_settings, "timing_spinboxes")
        ):
            timing_config = self.main_window.timing_settings.get_config()
            if cooldown_type == "hp":
                return timing_config.get("hp_cooldown", 5000)
            elif cooldown_type == "mp":
                return timing_config.get("mp_cooldown", 8000)
        # 如果无法获取，返回默认值
        return 5000 if cooldown_type == "hp" else 8000

    def _get_existing_resource_config(self, prefix: str) -> Dict[str, Any]:
        """取当前已加载的该资源配置,作为构建的基底。

        UI 只覆盖它认识的字段;ocr_model / ocr_device 这类没有控件的用户设置
        必须原样带过去,否则保存或 F8 同步会把它们静默抹回默认值。
        """
        value = self._resource_config_snapshot.get(f"{prefix}_config", {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def _build_hp_config(self) -> Dict[str, Any]:
        """构建HP配置 - 使用ResourceConfigManager"""
        timing_manager = getattr(self.main_window, 'timing_settings', None)
        return ResourceConfigManager.build_resource_config(
            "hp", self.hp_widgets, self.hp_detection_mode, self.hp_circle_config, timing_manager,
            existing_config=self._get_existing_resource_config("hp"),
        )


    def _build_mp_config(self) -> Dict[str, Any]:
        """构建MP配置 - 使用ResourceConfigManager"""
        timing_manager = getattr(self.main_window, 'timing_settings', None)
        return ResourceConfigManager.build_resource_config(
            "mp", self.mp_widgets, self.mp_detection_mode, self.mp_circle_config, timing_manager,
            existing_config=self._get_existing_resource_config("mp"),
        )

    def get_config(self) -> Dict[str, Any]:
        """获取配置（匹配ResourceManager期望的格式）"""
        resource_config = deepcopy(self._resource_config_snapshot)
        resource_config.update(
            {
                "hp_config": self._build_hp_config(),
                "mp_config": self._build_mp_config(),
                "check_interval": resource_config.get("check_interval", 200),
            }
        )
        return {
            "resource_management": resource_config
        }

    def update_from_config(self, config: Dict[str, Any]):
        """从配置更新UI - 使用ResourceConfigManager统一处理"""
        raw_res_config = (
            config.get("resource_management", {}) if isinstance(config, dict) else {}
        )
        res_config = raw_res_config if isinstance(raw_res_config, dict) else {}
        self._resource_config_snapshot = deepcopy(res_config)
        
        # HP配置更新
        raw_hp_config = res_config.get("hp_config", {})
        hp_config = raw_hp_config if isinstance(raw_hp_config, dict) else {}
        if self.hp_widgets:
            ResourceConfigManager.update_widget_from_config(
                self.hp_widgets,
                hp_config,
                "hp_detection_mode",
                "hp_circle_config", 
                "hp",
                self
            )
            # 更新检测模式显示
            self._update_detection_mode_display("hp")
            
            # 处理容差显示逻辑
            detection_mode = hp_config.get("detection_mode", "rectangle")
            self._toggle_tolerance_visibility("hp", detection_mode != "text_ocr")
            
        # MP配置更新  
        raw_mp_config = res_config.get("mp_config", {})
        mp_config = raw_mp_config if isinstance(raw_mp_config, dict) else {}
        if self.mp_widgets:
            ResourceConfigManager.update_widget_from_config(
                self.mp_widgets,
                mp_config,
                "mp_detection_mode",
                "mp_circle_config",
                "mp", 
                self
            )
            # 更新检测模式显示
            self._update_detection_mode_display("mp")
            
            # 处理容差显示逻辑
            detection_mode = mp_config.get("detection_mode", "rectangle")
            self._toggle_tolerance_visibility("mp", detection_mode != "text_ocr")



    def set_main_window(self, main_window):
        """设置主窗口引用，用于隐藏/显示界面"""
        self.main_window = main_window
        # 更新ColorAnalysisTools实例的主窗口引用
        if hasattr(self, 'color_analysis_tools') and self.color_analysis_tools:
            self.color_analysis_tools.main_window = main_window

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
        status_label.setStyleSheet(
            "font-size: 10pt; font-weight: bold; color: #007BFF;"
        )
        QApplication.processEvents()  # 强制UI刷新

        try:
            # 2. 调用后台检测逻辑
            if hasattr(self.main_window, "macro_engine") and hasattr(
                self.main_window.macro_engine, "resource_manager"
            ):
                result = (
                    self.main_window.macro_engine.resource_manager.auto_detect_orbs(
                        orb_type=prefix
                    )
                )

                if result and (prefix in result):
                    # 3. 检测成功
                    self._on_orbs_detected(prefix, result)
                    status_label.setText("✅ 检测成功！")
                    status_label.setStyleSheet(
                        "font-size: 10pt; font-weight: bold; color: #28a745;"
                    )
                else:
                    # 4. 检测失败
                    status_label.setText("❌ 检测失败，请重试")
                    status_label.setStyleSheet(
                        "font-size: 10pt; font-weight: bold; color: #DC3545;"
                    )
            else:
                status_label.setText("❌ 错误: 无法访问资源管理器")
                status_label.setStyleSheet(
                    "font-size: 10pt; font-weight: bold; color: #DC3545;"
                )

        except Exception as e:
            status_label.setText(f"❌ 检测出错: {str(e)[:30]}...")
            status_label.setStyleSheet(
                "font-size: 10pt; font-weight: bold; color: #DC3545;"
            )

        # 5. 3秒后自动清除状态信息
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, lambda: status_label.setText(""))


    def _toggle_tolerance_visibility(self, prefix: str, visible: bool):
        """根据检测模式显示/隐藏容差控件"""
        tolerance_label = getattr(self, f"{prefix}_tolerance_label", None)
        tolerance_input = getattr(self, f"{prefix}_tolerance_input", None)

        if tolerance_label and tolerance_input:
            if visible:
                tolerance_label.show()
                tolerance_input.show()
            else:
                tolerance_label.hide()
                tolerance_input.hide()

    def _get_coords_from_config(self, prefix: str, mode: str) -> Optional[str]:
        """从配置中获取指定模式的坐标

        Args:
            prefix: 资源前缀 ("hp" 或 "mp")
            mode: 检测模式 ("rectangle", "circle", "text_ocr")

        Returns:
            坐标字符串,如果配置中不存在则返回None
            - rectangle/text_ocr: "x1,y1,x2,y2"
            - circle: "x,y,r"
        """
        if not self.main_window or not hasattr(self.main_window, "_global_config"):
            return None

        config = self.main_window._global_config
        if not isinstance(config, dict):
            return None
        raw_res_config = config.get("resource_management", {})
        res_config = raw_res_config if isinstance(raw_res_config, dict) else {}
        raw_resource_config = res_config.get(f"{prefix}_config", {})
        resource_config = (
            raw_resource_config if isinstance(raw_resource_config, dict) else {}
        )

        if mode == "circle":
            # 圆形模式: center_x, center_y, radius
            center_x = resource_config.get("center_x")
            center_y = resource_config.get("center_y")
            radius = resource_config.get("radius")
            if center_x is not None and center_y is not None and radius is not None:
                return f"{center_x},{center_y},{radius}"
        elif mode == "text_ocr":
            # Text OCR模式: text_x1, text_y1, text_x2, text_y2
            text_x1 = resource_config.get("text_x1")
            text_y1 = resource_config.get("text_y1")
            text_x2 = resource_config.get("text_x2")
            text_y2 = resource_config.get("text_y2")
            if all(
                [
                    text_x1 is not None,
                    text_y1 is not None,
                    text_x2 is not None,
                    text_y2 is not None,
                ]
            ):
                return f"{text_x1},{text_y1},{text_x2},{text_y2}"
        else:  # rectangle
            # 矩形模式: region_x1, region_y1, region_x2, region_y2
            region_x1 = resource_config.get("region_x1")
            region_y1 = resource_config.get("region_y1")
            region_x2 = resource_config.get("region_x2")
            region_y2 = resource_config.get("region_y2")
            if all(
                [
                    region_x1 is not None,
                    region_y1 is not None,
                    region_x2 is not None,
                    region_y2 is not None,
                ]
            ):
                return f"{region_x1},{region_y1},{region_x2},{region_y2}"

        return None

    def _on_detection_mode_changed(self, prefix: str):
        """检测模式切换回调"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        mode_combo = widgets.get("mode_combo")
        if not mode_combo:
            return

        selected_mode = mode_combo.currentData()

        if prefix == "hp":
            self.hp_detection_mode = selected_mode
        else:
            self.mp_detection_mode = selected_mode

        # 更新显示
        self._update_detection_mode_display(prefix)

        # 根据模式显示/隐藏容差控件
        # text_ocr模式不需要容差，其他模式需要
        show_tolerance = selected_mode != "text_ocr"
        self._toggle_tolerance_visibility(prefix, show_tolerance)

        # 根据模式控制按钮显示/隐藏
        select_btn = widgets.get("select_region_btn")  # 选择区域按钮
        detect_btn = widgets.get("detect_orbs_btn")  # Detect Orbs按钮
        test_ocr_btn = widgets.get("test_ocr_btn")  # 测试识别按钮

        if selected_mode == "rectangle":
            # 矩形对比：显示 选择区域
            if select_btn:
                select_btn.setVisible(True)
                select_btn.setText("📦 选择区域")
                select_btn.setToolTip("框选资源颜色检测区域")
            if detect_btn:
                detect_btn.setVisible(False)
            if test_ocr_btn:
                test_ocr_btn.setVisible(False)

        elif selected_mode == "circle":
            # 圆形对比：显示 Detect Orbs
            if select_btn:
                select_btn.setVisible(False)
            if detect_btn:
                detect_btn.setVisible(True)
            if test_ocr_btn:
                test_ocr_btn.setVisible(False)

        elif selected_mode == "text_ocr":
            # 数字对比：显示 选择区域 和 测试识别
            if select_btn:
                select_btn.setVisible(True)
                select_btn.setText("📦 选择OCR区域")
                select_btn.setToolTip("粗框 HP/MP 数字文本区域即可；PaddleOCR 会自动收紧到 当前/最大 那一行")
            if detect_btn:
                detect_btn.setVisible(False)
            if test_ocr_btn:
                test_ocr_btn.setVisible(True)

        # 根据模式更新坐标输入框的提示和默认值
        coord_input = widgets.get("coord_input")
        if coord_input:
            # 尝试从配置中获取对应模式的坐标
            coords_from_config = self._get_coords_from_config(prefix, selected_mode)

            # 优先使用配置坐标，如果没有则使用默认坐标
            coords_to_use = None

            if selected_mode == "circle":
                coord_input.setPlaceholderText("x,y,r (圆心X,圆心Y,半径)")
                if coords_from_config:
                    coords_to_use = coords_from_config
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} 圆形模式使用配置坐标: {coords_from_config}"
                    )
                else:
                    coords_to_use = "174,957,47" if prefix == "hp" else "1738,957,47"
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} 圆形模式使用默认坐标: {coords_to_use}"
                    )

            elif selected_mode == "text_ocr":
                coord_input.setPlaceholderText("x1,y1,x2,y2 (文本区域)")
                if coords_from_config:
                    coords_to_use = coords_from_config
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} Text OCR模式使用配置坐标: {coords_from_config}"
                    )
                else:
                    coords_to_use = (
                        "97,814,218,835" if prefix == "hp" else "1767,814,1894,835"
                    )
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} Text OCR模式使用默认坐标: {coords_to_use}"
                    )

            else:  # rectangle
                coord_input.setPlaceholderText("x1,y1,x2,y2 (矩形区域)")
                if coords_from_config:
                    coords_to_use = coords_from_config
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} 矩形模式使用配置坐标: {coords_from_config}"
                    )
                else:
                    coords_to_use = (
                        "136,910,213,1004" if prefix == "hp" else "1552,910,1560,1004"
                    )
                    LOG_INFO(
                        f"[坐标更新] {prefix.upper()} 矩形模式使用默认坐标: {coords_to_use}"
                    )

            # 更新坐标输入框
            if coords_to_use:
                coord_input.setText(coords_to_use)

        LOG_INFO(f"[检测模式] {prefix.upper()} 切换到 {selected_mode} 模式")

        # 根据模式显示/隐藏容差控件
        tolerance_label = getattr(self, f"{prefix}_tolerance_label", None)
        tolerance_input = getattr(self, f"{prefix}_tolerance_input", None)

        if tolerance_label and tolerance_input:
            if selected_mode == "text_ocr":
                # Text OCR 模式：隐藏容差控件
                tolerance_label.hide()
                tolerance_input.hide()
            else:
                # 其他模式：显示容差控件
                tolerance_label.show()
                tolerance_input.show()

    def _update_detection_mode_display(
        self, prefix: str, circle_config: Optional[Dict] = None
    ):
        """更新检测模式显示，并附带坐标信息"""
        mode = self.hp_detection_mode if prefix == "hp" else self.mp_detection_mode
        label = self.hp_mode_label if prefix == "hp" else self.mp_mode_label
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        if mode == "circle":
            if circle_config:
                cx = circle_config.get("center_x", "N/A")
                cy = circle_config.get("center_y", "N/A")
                r = circle_config.get("radius", "N/A")
                label.setText(f"🔵 当前模式：圆形检测 (圆心: {cx},{cy} | 半径: {r})")
            else:
                label.setText("🔵 当前模式：圆形检测 (无具体坐标)")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #28a745;")
            # 隐藏OCR引擎选择
            if "ocr_engine_combo" in widgets:
                widgets["ocr_engine_combo"].setVisible(False)
                widgets["ocr_engine_label"].setVisible(False)
        elif mode == "text_ocr":
            label.setText("🔤 当前模式：数字文本识别 (OCR)")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #ffc107;")
            # 显示OCR引擎选择
            if "ocr_engine_combo" in widgets:
                widgets["ocr_engine_combo"].setVisible(True)
                widgets["ocr_engine_label"].setVisible(True)
        elif mode == "rectangle":
            label.setText("⬛ 当前模式：矩形检测（手动选择区域）")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #17a2b8;")
            # 隐藏OCR引擎选择
            if "ocr_engine_combo" in widgets:
                widgets["ocr_engine_combo"].setVisible(False)
                widgets["ocr_engine_label"].setVisible(False)
        else:
            label.setText(f"⚠️ 当前模式不受支持：{mode!s}（运行时已禁用）")
            label.setStyleSheet("font-size: 10pt; font-weight: bold; color: #dc3545;")
            if "ocr_engine_combo" in widgets:
                widgets["ocr_engine_combo"].setVisible(False)
                widgets["ocr_engine_label"].setVisible(False)

    def _on_orbs_detected(
        self, prefix: str, detection_result: Dict[str, Dict[str, Any]]
    ):
        """球体检测完成回调 - 仅更新坐标和颜色"""
        orb_count = len(detection_result)
        LOG_INFO(f"[球体检测] 检测完成，共找到 {orb_count} 个球体")

        # 保存检测结果供后续使用
        # 🔧 自动检测只更新圆心/半径,要保留已设的 half(半圆方向),否则保存时丢失
        if prefix == "hp":
            old_half = (self.hp_circle_config or {}).get("hp", {}).get("half")
            self.hp_circle_config = detection_result.copy()
            if old_half and "hp" in self.hp_circle_config:
                self.hp_circle_config["hp"] = {**self.hp_circle_config["hp"], "half": old_half}
        else:
            old_half = (self.mp_circle_config or {}).get("mp", {}).get("half")
            self.mp_circle_config = detection_result.copy()
            if old_half and "mp" in self.mp_circle_config:
                self.mp_circle_config["mp"] = {**self.mp_circle_config["mp"], "half": old_half}

        # 获取widget并更新坐标
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 更新坐标输入框
        orb_data_for_prefix = detection_result.get(prefix)
        if orb_data_for_prefix:
            center_x = orb_data_for_prefix["center_x"]
            center_y = orb_data_for_prefix["center_y"]
            radius = orb_data_for_prefix["radius"]

            coord_input = widgets.get("coord_input")
            if coord_input:
                coord_input.setText(f"{center_x},{center_y},{radius}")
                LOG_INFO(
                    f"[球体检测] {prefix.upper()}坐标已更新: {center_x},{center_y},{radius}"
                )

        for orb_key, orb_data in detection_result.items():
            center_x = orb_data["center_x"]
            center_y = orb_data["center_y"]
            radius = orb_data["radius"]
            LOG_INFO(
                f"[球体检测] {orb_key.upper()}球体: 圆心({center_x},{center_y}), 半径{radius}"
            )

            # 如果检测结果包含颜色信息，同时更新颜色列表和容差输入框
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

                    # 更新对应的HP/MP容差输入框
                    if orb_key in ["hp", "mp"]:
                        tolerance_input = getattr(self, f"{orb_key}_tolerance_input", None)
                        if tolerance_input:
                            tolerance_input.setText(f"{h_tol},{s_tol},{v_tol}")
                            LOG_INFO(
                                f"[球体检测] {orb_key.upper()}容差框已更新: {h_tol},{s_tol},{v_tol}"
                            )

                    # 添加颜色到颜色列表
                    if hasattr(self, "global_colors_edit"):
                        self._add_color_to_list(
                            int(h), int(s), int(v)
                        )
                        LOG_INFO(
                            f"[球体检测] 添加{orb_key}颜色到列表: HSV({h},{s},{v})，容差已更新到输入框: ±({h_tol},{s_tol},{v_tol})"
                        )

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
        analysis_widget.setStyleSheet(
            "color: #333; font-size: 10pt; padding: 8px; border: 1px solid #28a745; border-radius: 3px; background-color: #f8fff8;"
        )

        LOG_INFO(f"🎨 颜色分析完成！")
        LOG_INFO(f"  区域: ({x1},{y1}) -> ({x2},{y2})")
        LOG_INFO(
            f"  主色调: HSV({dominant_color.get('h', 0)}, {dominant_color.get('s', 0)}, {dominant_color.get('v', 0)})"
        )
        LOG_INFO(
            f"  建议容差: H±{suggested_tolerances.get('h', 10)}, S±{suggested_tolerances.get('s', 20)}, V±{suggested_tolerances.get('v', 30)}"
        )

    def _on_region_selected(self, prefix: str, x1: int, y1: int, x2: int, y2: int):
        """区域选择完成回调 - 仅更新坐标，不改变模式"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 设置坐标到单行文本框
        coord_input = widgets.get("coord_input")
        if coord_input:
            coord_input.setText(f"{x1},{y1},{x2},{y2}")
            LOG_INFO(
                f"[区域选择] {prefix.upper()}区域坐标已更新: ({x1},{y1}) -> ({x2},{y2})"
            )

    def _on_region_analyzed(
        self, prefix: str, x1: int, y1: int, x2: int, y2: int, analysis: dict
    ):
        """智能颜色分析完成回调 - 更新坐标和颜色"""
        if not analysis or not analysis.get("analysis_success"):
            return

        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 更新检测区域坐标为用户最后选择的区域
        coord_input = widgets.get("coord_input")
        if coord_input:
            coord_input.setText(f"{x1},{y1},{x2},{y2}")
            LOG_INFO(
                f"[区域更新] {prefix.upper()}区域坐标已更新: ({x1},{y1}) -> ({x2},{y2})"
            )

        # 获取分析结果
        mean_h, mean_s, mean_v = analysis["mean_hsv"]
        tolerance_h, tolerance_s, tolerance_v = analysis["tolerance"]
        total_pixels = analysis["total_pixels"]
        region_size = analysis["region_size"]

        # 🚀 更新对应的HP/MP容差输入框
        tolerance_input = getattr(self, f"{prefix}_tolerance_input", None)
        if tolerance_input:
            tolerance_input.setText(f"{tolerance_h},{tolerance_s},{tolerance_v}")
            LOG_INFO(
                f"[选择区域] {prefix.upper()}容差框已更新: {tolerance_h},{tolerance_s},{tolerance_v}"
            )

        # 添加颜色到列表，容差已经在HP/MP独立管理
        if hasattr(self, "global_colors_edit"):
            self._add_color_to_list(
                int(mean_h),
                int(mean_s),
                int(mean_v)
            )
            LOG_INFO(
                f"[选择区域] 颜色分析完成: HSV({mean_h},{mean_s},{mean_v})，容差已更新到{prefix.upper()}输入框: ±({tolerance_h},{tolerance_s},{tolerance_v})"
            )

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
        LOG_INFO("=" * 50)
        LOG_INFO("🎯 智能颜色分析完成！")
        LOG_INFO(f"📊 区域大小: {region_size[0]}×{region_size[1]} 像素")
        LOG_INFO(f"📊 总像素数: {total_pixels:,} 个")
        LOG_INFO(f"🎨 平均颜色: HSV({mean_h}, {mean_s}, {mean_v})")
        LOG_INFO(f"⚙️  智能容差: ±({tolerance_h}, {tolerance_s}, {tolerance_v})")
        LOG_INFO("✅ 已追加到临时取色参考列表")
        LOG_INFO("=" * 50)

    def _test_text_ocr(self, prefix: str):
        """测试Text OCR识别功能"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from PySide6.QtCore import QTimer
        import cv2
        import os
        import time

        # 获取当前配置的坐标和OCR引擎
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets
        coord_input = widgets.get("coord_input")
        ocr_engine_combo = widgets.get("ocr_engine_combo")

        if not coord_input:
            QMessageBox.warning(self, "错误", "无法获取坐标配置")
            return

        # 获取选择的OCR引擎
        ocr_engine = "template"  # 默认
        if ocr_engine_combo:
            ocr_engine = ocr_engine_combo.currentData() or "template"

        # 引擎名称映射
        engine_names = {
            "paddle": "PaddleOCR",
            "template": "模板匹配",
            "keras": "Keras模型",
            "tesseract": "Tesseract",
        }
        engine_name = engine_names.get(ocr_engine, ocr_engine)

        # 解析坐标
        coord_text = coord_input.text().strip()
        try:
            coords = [int(x.strip()) for x in coord_text.split(",")]
            if len(coords) != 4:
                QMessageBox.warning(
                    self,
                    "坐标格式错误",
                    f"请输入4个坐标值 (x1,y1,x2,y2)\n当前输入: {coord_text}",
                )
                return
            x1, y1, x2, y2 = coords
        except:
            QMessageBox.warning(
                self,
                "坐标解析失败",
                f"无法解析坐标，请检查格式\n当前输入: {coord_text}",
            )
            return

        # 选择测试图片
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择游戏截图进行测试",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)",
        )

        if not file_path or not os.path.exists(file_path):
            return

        try:
            # 读取图片
            img = cv2.imread(file_path)
            if img is None:
                QMessageBox.warning(self, "读取失败", f"无法读取图片: {file_path}")
                return

            # 检查坐标是否在图片范围内
            h, w = img.shape[:2]
            if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
                QMessageBox.warning(
                    self,
                    "坐标超出范围",
                    f"坐标 ({x1},{y1},{x2},{y2}) 超出图片范围\n" f"图片尺寸: {w}x{h}",
                )
                return

            # 裁剪ROI
            roi = img[y1:y2, x1:x2]

            # 根据选择的引擎进行识别
            text = None
            percentage = -1
            recognition_time = 0

            if ocr_engine in ("template", "keras"):
                # 使用deepai引擎
                try:
                    from deepai import get_recognizer

                    start_time = time.time()
                    recognizer = get_recognizer(ocr_engine)

                    if recognizer is None:
                        QMessageBox.warning(
                            self,
                            "引擎初始化失败",
                            f"{engine_name}引擎初始化失败\n"
                            f"请确保已运行训练流程生成模型/模板",
                        )
                        return

                    current, maximum = recognizer.recognize_and_parse(roi)
                    recognition_time = (time.time() - start_time) * 1000

                    if current is not None and maximum is not None and maximum > 0:
                        text = f"{current}/{maximum}"
                        percentage = (current / maximum) * 100.0

                except Exception as e:
                    QMessageBox.warning(
                        self, "识别失败", f"{engine_name}引擎识别失败\n错误: {str(e)}"
                    )
                    return
            elif ocr_engine == "paddle":
                # 使用 PaddleOCR rec-only (PP-OCRv6)，与运行时同一条路径
                try:
                    from ..utils.paddle_ocr_manager import get_paddle_ocr_manager

                    mgr = get_paddle_ocr_manager()
                    # 必须用与运行时**同一套**模型/设备/阈值:写死 small@cpu 的话,
                    # 配了 medium 的用户点一次"测试"就会与调度线程互相驱逐模型槽位
                    model_name, device, min_score = self._get_ocr_runtime_options(prefix)
                    start_time = time.time()
                    cur, mx, pct = mgr.recognize_and_parse(roi, model_name, device, min_score)
                    recognition_time = (time.time() - start_time) * 1000
                    if pct is not None:
                        text = f"{cur}/{mx}"
                        percentage = pct
                except Exception as e:
                    QMessageBox.warning(
                        self, "识别失败", f"PaddleOCR引擎识别失败\n错误: {str(e)}"
                    )
                    return
            else:
                # 使用Tesseract引擎
                try:
                    from ..utils.tesseract_ocr_manager import get_tesseract_ocr_manager

                    # 与当前运行配置使用同一来源；签名变化时工厂会安全重建。
                    tesseract_config = self._get_current_tesseract_config()
                    ocr_manager = get_tesseract_ocr_manager(tesseract_config)

                    # 执行识别
                    start_time = time.time()
                    region = (x1, y1, x2, y2)
                    text, percentage = ocr_manager.recognize_and_parse(
                        img, region, debug=True
                    )
                    recognition_time = (time.time() - start_time) * 1000

                except Exception as e:
                    QMessageBox.warning(
                        self, "识别失败", f"Tesseract引擎识别失败\n错误: {str(e)}"
                    )
                    return

            # 显示结果
            if text and percentage >= 0:
                result_msg = f"""✅ 识别成功！

📊 测试配置:
• 资源类型: {prefix.upper()}
• OCR引擎: {engine_name}
• 测试图片: {os.path.basename(file_path)}
• 识别区域: ({x1},{y1}) → ({x2},{y2})

🎯 识别结果:
• 识别文本: {text}
• 资源百分比: {percentage:.1f}%
• 识别耗时: {recognition_time:.1f} ms

💡 提示:
识别成功！可以正常使用 {engine_name} 引擎。
如果实际游戏中识别失败，请检查:
1. 游戏分辨率是否与测试图片一致
2. 坐标是否准确框选了数字区域
3. 是否已运行训练流程（模板匹配/Keras需要）"""

                QMessageBox.information(
                    self, f"Text OCR 测试成功 ({engine_name})", result_msg
                )

                LOG_INFO("=" * 60)
                LOG_INFO(f"[Text OCR测试] {prefix.upper()} 识别成功")
                LOG_INFO(f"  引擎: {engine_name}")
                LOG_INFO(f"  文本: {text}")
                LOG_INFO(f"  百分比: {percentage:.1f}%")
                LOG_INFO(f"  耗时: {recognition_time:.1f} ms")
                LOG_INFO("=" * 60)
            else:
                result_msg = f"""❌ 识别失败

📊 测试配置:
• 资源类型: {prefix.upper()}
• OCR引擎: {engine_name}
• 测试图片: {os.path.basename(file_path)}
• 识别区域: ({x1},{y1}) → ({x2},{y2})

🔍 可能原因:
1. 坐标区域没有包含数字文本
2. 图片分辨率与预期不符
3. 数字字体不清晰或被遮挡

💡 建议:
1. 使用"选择区域"按钮重新框选数字区域
2. 确保区域完整包含HP/MP数字（如 540/540）
3. 检查Tesseract是否正确安装"""

                QMessageBox.warning(self, "Text OCR 测试失败", result_msg)

                LOG_INFO("=" * 60)
                LOG_INFO(f"[Text OCR测试] {prefix.upper()} 识别失败")
                LOG_INFO(f"  区域: ({x1},{y1}) → ({x2},{y2})")
                LOG_INFO("=" * 60)

        except Exception as e:
            import traceback

            error_trace = traceback.format_exc()
            QMessageBox.critical(
                self, "测试出错", f"Text OCR测试过程中出错:\n{str(e)}\n\n{error_trace}"
            )
            LOG_INFO(f"[Text OCR测试] 错误: {e}")
            LOG_INFO(error_trace)
