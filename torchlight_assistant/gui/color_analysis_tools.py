#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""颜色分析工具 - 提取颜色分析和取色相关功能"""

import cv2
import numpy as np
from typing import Optional, Callable, Tuple
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QTextEdit, QLabel


class ColorAnalysisTools:
    """颜色分析工具类 - 封装取色和颜色分析功能"""

    def __init__(self, main_window=None):
        self.main_window = main_window

    def start_single_color_picking(
        self, callback: Optional[Callable[[int, int, int], None]] = None
    ):
        """开始单点取色"""
        if not self.main_window:
            return

        try:
            from .color_picker_dialog import ColorPickingDialog

            def on_color_picked(r, g, b):
                # 转换为HSV
                h, s, v = self.rgb_to_hsv(r, g, b)

                if callback:
                    callback(int(h), int(s), int(v))

                print(f"[单点取色] 获取颜色: RGB({r},{g},{b}) -> HSV({h},{s},{v})")

            def show_picker():
                picker = ColorPickingDialog()
                picker.color_picked.connect(on_color_picked)
                picker.exec()

                # 恢复显示主界面
                if self.main_window:
                    self.main_window.show()
                    self.main_window.raise_()
                    self.main_window.activateWindow()

            # 隐藏主窗口
            self.main_window.hide()

            # 使用QTimer延迟执行，确保界面完全隐藏
            QTimer.singleShot(100, show_picker)

        except Exception:
            # 发生错误时也要恢复主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()

    def start_region_color_analysis(
        self, callback: Optional[Callable[[int, int, int, int, dict], None]] = None
    ):
        """开始区域颜色分析"""
        if not self.main_window:
            return

        # 隐藏主窗口
        self.main_window.hide()

        def show_dialog():
            from .region_selection_dialog import RegionSelectionDialog

            dialog = RegionSelectionDialog(None)

            def on_region_analyzed(x1, y1, x2, y2, analysis):
                print(f"[区域取色调试] 收到分析结果: {analysis}")

                if analysis and "mean_hsv" in analysis:
                    h, s, v = analysis["mean_hsv"]

                    if callback:
                        callback(int(h), int(s), int(v), x1, y1, x2, y2, analysis)

                    if "tolerance" in analysis:
                        h_tol, s_tol, v_tol = analysis["tolerance"]
                        print(
                            f"[区域取色] 获取平均颜色: HSV({h},{s},{v})，分析建议容差: ±({h_tol},{s_tol},{v_tol})"
                        )
                    else:
                        print(f"[区域取色] 获取平均颜色: HSV({h},{s},{v})")
                else:
                    print("[区域取色警告] 分析结果中没有找到mean_hsv字段")

            dialog.region_analyzed.connect(on_region_analyzed)

            # 执行对话框
            dialog.exec()

            # 恢复显示主界面
            if self.main_window:
                self.main_window.show()
                self.main_window.raise_()
                self.main_window.activateWindow()

        QTimer.singleShot(100, show_dialog)

    @staticmethod
    def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[int, int, int]:
        """将RGB转换为OpenCV HSV格式"""
        rgb_array = np.array([[[r, g, b]]], dtype=np.uint8)
        hsv_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2HSV)
        h, s, v = hsv_array[0][0]
        return int(h), int(s), int(v)

    @staticmethod
    def hsv_to_rgb(h: int, s: int, v: int) -> Tuple[int, int, int]:
        """将OpenCV HSV颜色转换为RGB"""
        hsv_array = np.array([[[h, s, v]]], dtype=np.uint8)
        rgb_array = cv2.cvtColor(hsv_array, cv2.COLOR_HSV2RGB)
        r, g, b = rgb_array[0][0]
        return int(r), int(g), int(b)

    @staticmethod
    def get_contrast_color(r: int, g: int, b: int) -> str:
        """根据背景色亮度返回合适的文字颜色"""
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#ffffff" if luminance < 0.5 else "#000000"


class ColorListManager:
    """颜色列表管理器 - 管理颜色列表的FIFO逻辑"""

    def __init__(
        self, colors_edit: QTextEdit, colors_result: QLabel, max_colors: int = 2
    ):
        self.colors_edit = colors_edit
        self.colors_result = colors_result
        self.max_colors = max_colors

    def add_color_to_list(self, h: int, s: int, v: int):
        """将HSV颜色值添加到颜色列表，使用FIFO队列逻辑"""
        current_text = self.colors_edit.toPlainText().strip()
        new_color = f"{h},{s},{v}"  # 只存储HSV值

        # 解析现有颜色
        if current_text:
            existing_colors = [
                line.strip() for line in current_text.split("\n") if line.strip()
            ]
        else:
            existing_colors = []

        # 添加新颜色到列表末尾
        existing_colors.append(new_color)

        # FIFO限制：如果超过最大数量，移除最旧的
        if len(existing_colors) > self.max_colors:
            removed_color = existing_colors.pop(0)
            print(f"[颜色管理] 移除最旧颜色: {removed_color}")

        # 更新文本框
        updated_text = "\n".join(existing_colors)
        self.colors_edit.setPlainText(updated_text)

        print(
            f"[颜色添加] 添加颜色到列表: HSV({h},{s},{v}) | 当前总数: {len(existing_colors)}"
        )

    def parse_colors(self):
        """解析颜色列表并显示带颜色背景的结果"""
        try:
            colors_text = self.colors_edit.toPlainText().strip()

            if not colors_text:
                self.colors_result.setText("请输入颜色配置")
                return

            lines = [line.strip() for line in colors_text.split("\n") if line.strip()]

            if not lines:
                self.colors_result.setText("请输入有效的颜色配置")
                return

            # 构建HTML格式的结果文本
            html_parts = [
                f"<div style='margin-bottom: 8px; font-weight: bold;'>✅ 解析成功：{len(lines)}种颜色</div>"
            ]

            for i, line in enumerate(lines):
                try:
                    # 解析单行颜色值 H,S,V
                    values = [int(x.strip()) for x in line.split(",") if x.strip()]

                    if len(values) != 3:
                        self.colors_result.setText(
                            f"❌ 第{i+1}行格式错误：必须为3个值 (H,S,V)"
                        )
                        return

                    h, s, v = values

                    # 验证OpenCV HSV范围
                    if not (0 <= h <= 179):
                        self.colors_result.setText(
                            f"❌ 第{i+1}行H值({h})超出OpenCV范围(0-179)"
                        )
                        return
                    if not (0 <= s <= 255):
                        self.colors_result.setText(
                            f"❌ 第{i+1}行S值({s})超出范围(0-255)"
                        )
                        return
                    if not (0 <= v <= 255):
                        self.colors_result.setText(
                            f"❌ 第{i+1}行V值({v})超出范围(0-255)"
                        )
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
                        颜色{i+1}: HSV({h},{s},{v}) → RGB({r},{g},{b})
                    </div>
                    """
                    html_parts.append(color_html)

                except ValueError:
                    self.colors_result.setText(f"❌ 第{i+1}行数值格式错误：{line}")
                    return

            result_html = "".join(html_parts)
            self.colors_result.setText(result_html)

        except Exception:
            self.colors_result.setText("❌ 解析错误")
