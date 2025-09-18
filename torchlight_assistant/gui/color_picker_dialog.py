#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""颜色拾取对话框"""

from PySide6.QtWidgets import QDialog, QApplication
from PySide6.QtCore import Qt, QRect, Signal as QSignal
from PySide6.QtGui import QPainter, QPen, QColor, QCursor


class ColorPickingDialog(QDialog):
    """颜色拾取对话框"""

    color_picked = QSignal(int, int, int)  # r, g, b

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

    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 更新十字线和放大镜位置"""
        # 触发重绘，更新十字线和放大镜位置
        self.update()

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

                # 直接获取RGB值，避免HSV转换的精度损失
                r, g, b, _ = color.getRgb()

                self.color_picked.emit(r, g, b)

            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()