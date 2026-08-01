#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""颜色拾取对话框"""

from PySide6.QtWidgets import QDialog, QApplication
from PySide6.QtCore import Qt, QRect, Signal as QSignal
from PySide6.QtGui import QPainter, QPen, QColor, QCursor

from torchlight_assistant.utils.debug_log import LOG_ERROR


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

        # 获取屏幕截图(grabWindow 返回**物理像素**尺寸的 pixmap)
        screen = QApplication.primaryScreen()
        self.screenshot = screen.grabWindow(0)

        # ⚠️ DPI:鼠标坐标是逻辑像素,而 QImage.pixelColor() 按**物理像素**索引
        # (与 devicePixelRatio 元数据无关)。150% 缩放下直接拿逻辑坐标采样,
        # 取到的是目标点左上方 1/1.5 处的颜色 —— 而且旧的越界检查拿逻辑坐标去比
        # 更大的物理 width(),恒为真,越界从来没被发现,最终把错颜色写进 HP/MP 配置。
        # 比例从截图自身推导(物理宽/逻辑屏宽),对任何 Qt 版本都成立。
        # 缓存 QImage:放大镜每次重绘要采样一万个点,旧代码在循环里反复 toImage()。
        self._image = self.screenshot.toImage()
        logical_w = max(1, screen.geometry().width())
        self._dpr = float(self._image.width()) / float(logical_w)
        if self._dpr <= 0:
            self._dpr = 1.0
        # 交叉校验:与 pixmap 自带的 DPR 不一致说明 grabWindow(0) 抓的不是这块屏
        # (例如返回整个虚拟桌面),此时 _dpr 会被算成屏幕数量倍,取到的颜色整体错位。
        tagged_dpr = float(self.screenshot.devicePixelRatio() or 1.0)
        if abs(self._dpr - tagged_dpr) > 0.01:
            LOG_ERROR(
                f"[取色] DPR 交叉校验不一致: 推导={self._dpr} vs pixmap 自带={tagged_dpr}"
                f" —— 截图可能不是单屏,取色点可能整体偏移"
            )

        self.setGeometry(screen.geometry())

        # 创建放大镜效果
        self.magnifier_size = 100
        self.zoom_factor = 4

        # 设置鼠标追踪
        self.setMouseTracking(True)

        # 确保窗口能接收键盘事件
        self.setFocusPolicy(Qt.StrongFocus)

    def _sample_logical(self, lx, ly) -> QColor:
        """按**逻辑**坐标采样截图颜色(内部换算到物理像素并夹紧到图像边界)。"""
        px = int(round(float(lx) * self._dpr))
        py = int(round(float(ly) * self._dpr))
        px = max(0, min(px, self._image.width() - 1))
        py = max(0, min(py, self._image.height() - 1))
        return self._image.pixelColor(px, py)

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

        # 绘制放大的像素:x/y 是放大镜内的输出像素,先换算回**逻辑**源坐标,
        # 再由 _sample_logical 统一转物理并夹紧(边缘复制,不再留空白边)。
        # 采样与 mousePressEvent 共用同一函数,保证"放大镜看到的"就是"点下去取到的"。
        half = self.magnifier_size / 2.0
        for x in range(self.magnifier_size):
            for y in range(self.magnifier_size):
                color = self._sample_logical(
                    cursor_pos.x() + (x - half) / self.zoom_factor,
                    cursor_pos.y() + (y - half) / self.zoom_factor,
                )
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
            # 截图失败(锁屏/安全桌面/部分 RDP)时 pixmap 是空的。旧的
            # `x < screenshot.width()` 顺带挡住了这种情况,换成逻辑 rect 判断后不再挡:
            # 采样会夹紧到 (0,0) 并返回无效 QColor,getRgb() 给出 (0,0,0),
            # 于是一个纯黑 HSV 被静默写进 HP/MP 配置。这里显式拒绝。
            if self._image.isNull():
                LOG_ERROR("[取色] 屏幕截图为空(可能处于锁屏/安全桌面),已取消取色")
                self.reject()
                return

            cursor_pos = self.mapFromGlobal(QCursor.pos())

            # 越界判断必须在**逻辑**空间做(旧代码拿逻辑坐标比更大的物理 width(),
            # 恒为真,越界也不会被发现)。
            if self.rect().contains(cursor_pos):
                color = self._sample_logical(cursor_pos.x(), cursor_pos.y())

                # 直接获取RGB值，避免HSV转换的精度损失
                r, g, b, _ = color.getRgb()

                self.color_picked.emit(r, g, b)

            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()