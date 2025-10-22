#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""区域选择对话框"""

from PySide6.QtWidgets import QDialog, QApplication
from PySide6.QtCore import Qt, QRect, Signal as QSignal
from PySide6.QtGui import QPainter, QPen, QColor
from torchlight_assistant.utils.debug_log import LOG_INFO, LOG



class RegionSelectionDialog(QDialog):
    """区域选择对话框"""

    region_selected = QSignal(int, int, int, int)  # x1, y1, x2, y2
    region_analyzed = QSignal(
        int, int, int, int, dict
    )  # x1, y1, x2, y2, color_analysis

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

        # 存储选中的区域信息
        self._selected_region = None

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
                x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()

                LOG(f"[调试] 区域选择完成: ({x1},{y1}) -> ({x2},{y2})")

                # 立即发送区域选择信号
                self.region_selected.emit(x1, y1, x2, y2)

                # 存储区域信息，准备异步分析
                self._selected_region = (x1, y1, x2, y2)

                # 延迟执行分析，避免阻塞UI
                from PySide6.QtCore import QTimer

                QTimer.singleShot(100, self._perform_color_analysis_and_close)
            else:
                self.accept()

    def _perform_color_analysis_and_close(self):
        """执行颜色分析然后关闭对话框"""
        if not self._selected_region:
            self.accept()
            return

        x1, y1, x2, y2 = self._selected_region
        LOG(f"[调试] 开始颜色分析: ({x1},{y1}) -> ({x2},{y2})")

        try:
            # 执行颜色分析
            color_analysis = self._analyze_region_colors(x1, y1, x2, y2)

            # 发送颜色分析信号
            if color_analysis:
                LOG(f"[调试] 发送颜色分析结果")
                self.region_analyzed.emit(x1, y1, x2, y2, color_analysis)
            else:
                LOG(f"[调试] 颜色分析失败")

        except Exception as e:
            LOG(f"[调试] 颜色分析异常: {e}")
            import traceback

            traceback.print_exc()

        # 最后关闭对话框
        self.accept()

    def _analyze_region_colors(self, x1: int, y1: int, x2: int, y2: int) -> dict:
        """智能分析区域颜色，计算平均HSV和容差（优化版）"""
        try:
            import cv2
            import numpy as np

            LOG(f"[调试] 开始分析区域: ({x1},{y1}) -> ({x2},{y2})")

            # 从截图中提取区域
            screenshot_array = self._pixmap_to_array(self.screenshot)
            if screenshot_array is None:
                LOG(f"[调试] 截图转换失败")
                return None

            LOG(f"[调试] 截图数组形状: {screenshot_array.shape}")

            # 确保坐标在有效范围内
            height, width = screenshot_array.shape[:2]
            x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
            y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))

            if x2 <= x1 or y2 <= y1:
                return None

            # 提取区域图像
            region = screenshot_array[y1:y2, x1:x2]

            # 转换为HSV
            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

            # 将三维数组重塑为像素列表 (N, 3)
            pixels = hsv_region.reshape(-1, 3)

            # 过滤掉低饱和度和低亮度的像素（更可靠的噪点过滤）
            # S > 25 and V > 25
            filtered_pixels = pixels[(pixels[:, 1] > 25) & (pixels[:, 2] > 25)]

            LOG(f"[调试] 总像素: {len(pixels)}, 过滤后: {len(filtered_pixels)}")

            if len(filtered_pixels) < 10:  # 如果有效像素太少，使用全部像素
                filtered_pixels = pixels
                LOG(f"[调试] 有效像素太少，使用全部像素")

            if len(filtered_pixels) == 0:
                LOG(f"[调试] 没有有效像素")
                return None

            # --- 正确计算平均值 ---
            # S 和 V 可以直接求平均
            mean_s = int(np.mean(filtered_pixels[:, 1]))
            mean_v = int(np.mean(filtered_pixels[:, 2]))

            # H (色相) 需要使用向量法计算平均值
            # 1. 将 H (0-179) 转换为角度 (0-358)
            h_angles = filtered_pixels[:, 0] * 2.0
            # 2. 转换为弧度
            h_rads = np.deg2rad(h_angles)
            # 3. 计算平均向量
            mean_x = np.mean(np.cos(h_rads))
            mean_y = np.mean(np.sin(h_rads))
            # 4. 将平均向量转回角度
            mean_rad = np.arctan2(mean_y, mean_x)
            mean_angle = np.rad2deg(mean_rad)
            if mean_angle < 0:
                mean_angle += 360
            # 5. 转回 OpenCV 的 H 值 (0-179)
            mean_h = int(round(mean_angle / 2.0))

            # --- 容差计算 (您的实现已经很棒了) ---
            std_h = np.std(filtered_pixels[:, 0])
            std_s = np.std(filtered_pixels[:, 1])
            std_v = np.std(filtered_pixels[:, 2])

            # 智能容差计算（2倍标准差覆盖约95%的像素）
            # 对H使用一个更简单的近似标准差计算，因为它也是环形的
            # 实践中直接用np.std通常也够用
            tolerance_h = max(5, min(25, int(std_h * 2)))  # H容差限制在5-25
            tolerance_s = max(15, min(70, int(std_s * 2)))  # S和V容差范围可以适当放宽
            tolerance_v = max(15, min(70, int(std_v * 2)))

            # 计算像素统计信息
            total_pixels = region.shape[0] * region.shape[1]

            result = {
                "mean_hsv": (mean_h, mean_s, mean_v),
                "tolerance": (tolerance_h, tolerance_s, tolerance_v),
                "std_hsv": (std_h, std_s, std_v),
                "total_pixels": total_pixels,
                "region_size": (x2 - x1, y2 - y1),
                "analysis_success": True,
            }

            LOG_INFO(
                f"[调试] 分析结果: 平均HSV({mean_h},{mean_s},{mean_v}), 容差({tolerance_h},{tolerance_s},{tolerance_v})"
            )

            return result

        except Exception as e:
            LOG_INFO(f"颜色分析失败: {e}")
            return None

    def _pixmap_to_array(self, pixmap):
        """将QPixmap转换为numpy数组"""
        try:
            import cv2
            import numpy as np

            LOG(f"[调试] 开始转换QPixmap, 大小: {pixmap.width()}x{pixmap.height()}")

            # 转换为QImage
            image = pixmap.toImage()

            # 确保图像格式正确
            from PySide6.QtGui import QImage

            if image.format() != QImage.Format_ARGB32:
                image = image.convertToFormat(QImage.Format_ARGB32)
                LOG(f"[调试] 转换图像格式为ARGB32")

            # 转换为numpy数组
            width = image.width()
            height = image.height()

            LOG(f"[调试] 图像尺寸: {width}x{height}")

            # 获取图像数据
            ptr = image.bits()
            # PySide6返回memoryview对象，直接使用
            arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 4))

            LOG(f"[调试] numpy数组形状: {arr.shape}")

            # PySide6/Qt的ARGB32实际上是BGRA字节顺序
            # 直接提取BGR通道，忽略Alpha通道
            bgr_array = arr[:, :, [0, 1, 2]]  # B, G, R通道

            LOG(f"[调试] BGR数组形状: {bgr_array.shape}")

            return bgr_array

        except Exception as e:
            LOG(f"[调试] 图像转换失败: {e}")
            import traceback

            traceback.print_exc()
            return None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
        super().keyPressEvent(event)
