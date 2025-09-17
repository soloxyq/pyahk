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

        # 颜色拾取
        pick_layout = QHBoxLayout()
        pick_layout.addWidget(QLabel("目标颜色:"))
        color_display = QLabel("█")
        color_display.setStyleSheet(
            f"""
            background-color: {color};
            color: {color};
            font-size: 16px;
            font-weight: bold;
            padding: 2px 8px;
            border: 1px solid #ccc;
            border-radius: 3px;
        """
        )
        pick_btn = QPushButton("🎨 拾取颜色")
        pick_btn.setMaximumWidth(80)
        pick_btn.clicked.connect(lambda: self._start_color_picking(prefix))
        pick_layout.addWidget(color_display)
        pick_layout.addWidget(pick_btn)
        pick_layout.addStretch()
        color_layout.addLayout(pick_layout)

        # HSV值设置
        hsv_layout = QGridLayout()
        hsv_layout.addWidget(QLabel("色相 (H):"), 0, 0)
        h_spinbox = ConfigSpinBox()
        h_spinbox.setRange(0, 359)
        # 设置实际测量的HSV值
        if prefix == "hp":
            h_spinbox.setValue(314)  # 血药色相
        else:
            h_spinbox.setValue(208)  # 蓝药色相
        hsv_layout.addWidget(h_spinbox, 0, 1)

        hsv_layout.addWidget(QLabel("饱和度 (S):"), 0, 2)
        s_spinbox = ConfigSpinBox()
        s_spinbox.setRange(0, 255)
        if prefix == "hp":
            s_spinbox.setValue(75)  # 血药饱和度
        else:
            s_spinbox.setValue(80)  # 蓝药饱和度
        hsv_layout.addWidget(s_spinbox, 0, 3)

        hsv_layout.addWidget(QLabel("明度 (V):"), 1, 0)
        v_spinbox = ConfigSpinBox()
        v_spinbox.setRange(0, 255)
        if prefix == "hp":
            v_spinbox.setValue(29)  # 血药明度
        else:
            v_spinbox.setValue(58)  # 蓝药明度
        hsv_layout.addWidget(v_spinbox, 1, 1)

        color_layout.addLayout(hsv_layout)

        # 容差设置
        tolerance_layout = QGridLayout()
        tolerance_layout.addWidget(QLabel("H容差:"), 0, 0)
        h_tolerance_spinbox = ConfigSpinBox()
        h_tolerance_spinbox.setRange(0, 50)
        # 设置实际测量的容差值
        if prefix == "hp":
            h_tolerance_spinbox.setValue(10)  # 血药H容差
        else:
            h_tolerance_spinbox.setValue(7)   # 蓝药H容差
        tolerance_layout.addWidget(h_tolerance_spinbox, 0, 1)

        tolerance_layout.addWidget(QLabel("S容差:"), 0, 2)
        s_tolerance_spinbox = ConfigSpinBox()
        s_tolerance_spinbox.setRange(0, 100)
        if prefix == "hp":
            s_tolerance_spinbox.setValue(20)  # 血药S容差
        else:
            s_tolerance_spinbox.setValue(5)   # 蓝药S容差
        tolerance_layout.addWidget(s_tolerance_spinbox, 0, 3)

        tolerance_layout.addWidget(QLabel("V容差:"), 1, 0)
        v_tolerance_spinbox = ConfigSpinBox()
        v_tolerance_spinbox.setRange(0, 100)
        if prefix == "hp":
            v_tolerance_spinbox.setValue(20)  # 血药V容差
        else:
            v_tolerance_spinbox.setValue(5)   # 蓝药V容差
        tolerance_layout.addWidget(v_tolerance_spinbox, 1, 1)

        color_layout.addLayout(tolerance_layout)
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
            "color_display": color_display,
            "pick_color_btn": pick_btn,
            "target_h": h_spinbox,
            "target_s": s_spinbox,
            "target_v": v_spinbox,
            "tolerance_h": h_tolerance_spinbox,
            "tolerance_s": s_tolerance_spinbox,
            "tolerance_v": v_tolerance_spinbox,
        }

        # 连接HSV值变化事件，实时更新颜色显示
        h_spinbox.valueChanged.connect(
            lambda: self._update_color_display_from_hsv(prefix)
        )
        s_spinbox.valueChanged.connect(
            lambda: self._update_color_display_from_hsv(prefix)
        )
        v_spinbox.valueChanged.connect(
            lambda: self._update_color_display_from_hsv(prefix)
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

    def get_config(self) -> Dict[str, Any]:
        """获取配置（匹配ResourceManager期望的格式）"""
        return {
            "resource_management": {
                "hp_config": {
                    "enabled": self.hp_widgets["enabled"].isChecked(),
                    "key": self.hp_widgets["key"].text().strip(),
                    "threshold": self.hp_widgets["threshold"].value(),
                    "cooldown": self.hp_widgets["cooldown"].value(),
                    "region_x1": self.hp_widgets["x1"].value(),
                    "region_y1": self.hp_widgets["y1"].value(),
                    "region_x2": self.hp_widgets["x2"].value(),
                    "region_y2": self.hp_widgets["y2"].value(),
                    "target_h": self.hp_widgets["target_h"].value(),
                    "target_s": self.hp_widgets["target_s"].value(),
                    "target_v": self.hp_widgets["target_v"].value(),
                    "tolerance_h": self.hp_widgets["tolerance_h"].value(),
                    "tolerance_s": self.hp_widgets["tolerance_s"].value(),
                    "tolerance_v": self.hp_widgets["tolerance_v"].value(),
                },
                "mp_config": {
                    "enabled": self.mp_widgets["enabled"].isChecked(),
                    "key": self.mp_widgets["key"].text().strip(),
                    "threshold": self.mp_widgets["threshold"].value(),
                    "cooldown": self.mp_widgets["cooldown"].value(),
                    "region_x1": self.mp_widgets["x1"].value(),
                    "region_y1": self.mp_widgets["y1"].value(),
                    "region_x2": self.mp_widgets["x2"].value(),
                    "region_y2": self.mp_widgets["y2"].value(),
                    "target_h": self.mp_widgets["target_h"].value(),
                    "target_s": self.mp_widgets["target_s"].value(),
                    "target_v": self.mp_widgets["target_v"].value(),
                    "tolerance_h": self.mp_widgets["tolerance_h"].value(),
                    "tolerance_s": self.mp_widgets["tolerance_s"].value(),
                    "tolerance_v": self.mp_widgets["tolerance_v"].value(),
                },
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
            self.hp_widgets["threshold"].setValue(hp_config.get("threshold", 50))  # 默认50%
            self.hp_widgets["cooldown"].setValue(hp_config.get("cooldown", 5000))
            self.hp_widgets["x1"].setValue(hp_config.get("region_x1", 136))  # 1080P血药区域
            self.hp_widgets["y1"].setValue(hp_config.get("region_y1", 910))
            self.hp_widgets["x2"].setValue(hp_config.get("region_x2", 213))
            self.hp_widgets["y2"].setValue(hp_config.get("region_y2", 1004))
            self.hp_widgets["target_h"].setValue(hp_config.get("target_h", 314))  # 血药HSV
            self.hp_widgets["target_s"].setValue(hp_config.get("target_s", 75))
            self.hp_widgets["target_v"].setValue(hp_config.get("target_v", 29))
            self.hp_widgets["tolerance_h"].setValue(hp_config.get("tolerance_h", 10))  # 血药容差
            self.hp_widgets["tolerance_s"].setValue(hp_config.get("tolerance_s", 20))
            self.hp_widgets["tolerance_v"].setValue(hp_config.get("tolerance_v", 20))

            # 更新颜色显示
            self._update_color_display_from_hsv("hp")

        # MP配置 - 使用1080P默认值
        mp_config = res_config.get("mp_config", {})
        if self.mp_widgets:
            self.mp_widgets["enabled"].setChecked(mp_config.get("enabled", True))
            self.mp_widgets["key"].setText(mp_config.get("key", "2"))
            self.mp_widgets["threshold"].setValue(mp_config.get("threshold", 50))  # 默认50%
            self.mp_widgets["cooldown"].setValue(mp_config.get("cooldown", 8000))
            self.mp_widgets["x1"].setValue(mp_config.get("region_x1", 1552))  # 1080P蓝药区域
            self.mp_widgets["y1"].setValue(mp_config.get("region_y1", 910))
            self.mp_widgets["x2"].setValue(mp_config.get("region_x2", 1560))
            self.mp_widgets["y2"].setValue(mp_config.get("region_y2", 1004))
            self.mp_widgets["target_h"].setValue(mp_config.get("target_h", 208))  # 蓝药HSV
            self.mp_widgets["target_s"].setValue(mp_config.get("target_s", 80))
            self.mp_widgets["target_v"].setValue(mp_config.get("target_v", 58))
            self.mp_widgets["tolerance_h"].setValue(mp_config.get("tolerance_h", 7))  # 蓝药容差
            self.mp_widgets["tolerance_s"].setValue(mp_config.get("tolerance_s", 5))
            self.mp_widgets["tolerance_v"].setValue(mp_config.get("tolerance_v", 5))

            # 更新颜色显示
            self._update_color_display_from_hsv("mp")

        # 更新全局设置
        check_interval = res_config.get("check_interval", 200)
        if hasattr(self, 'check_interval_spinbox'):
            self.check_interval_spinbox.setValue(check_interval)

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

    def _start_color_picking(self, prefix: str):
        """开始颜色拾取"""
        if not self.main_window:
            return

        # 使用QTimer延迟执行，确保界面完全隐藏
        from PySide6.QtCore import QTimer

        def show_dialog():
            # 创建颜色拾取对话框
            dialog = ColorPickingDialog(None)  # 不设置父窗口，避免焦点问题
            dialog.color_picked.connect(
                lambda h, s, v: self._on_color_picked(prefix, h, s, v)
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

    def _on_color_picked(self, prefix: str, h: int, s: int, v: int):
        """颜色拾取完成回调"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        # 更新HSV控件值
        widgets["target_h"].setValue(h)
        widgets["target_s"].setValue(s)
        widgets["target_v"].setValue(v)

        # 保存到内部存储
        if prefix == "hp":
            self.hp_hsv_values = {"h": h, "s": s, "v": v}
        else:
            self.mp_hsv_values = {"h": h, "s": s, "v": v}

        # 更新颜色显示
        self._update_color_display_from_hsv(prefix)

    def _update_color_display_from_hsv(self, prefix: str):
        """根据HSV值更新颜色显示"""
        widgets = self.hp_widgets if prefix == "hp" else self.mp_widgets

        h = widgets["target_h"].value()
        s = widgets["target_s"].value()
        v = widgets["target_v"].value()

        # 将HSV转换为RGB用于显示
        rgb_color = self._hsv_to_rgb(h, s, v)
        color_hex = f"#{rgb_color[0]:02x}{rgb_color[1]:02x}{rgb_color[2]:02x}"

        # 更新颜色显示
        widgets["color_display"].setStyleSheet(
            f"""
            background-color: {color_hex};
            color: {color_hex};
            font-size: 16px;
            font-weight: bold;
            padding: 2px 8px;
            border: 1px solid #ccc;
            border-radius: 3px;
        """
        )

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
                self.color_picked.emit(h, s, v)

            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
