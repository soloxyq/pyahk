from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont
from typing import Optional
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO


def format_queue_stats_line(data: str) -> str:
    """把 AHK 每秒推送的 stats 载荷压成 OSD 的一行紧凑文本。

    载荷形如 "e=0,h=1,n=3,l=0,p=120,d=2,x=1":e/h/n/l 是**实时**队列深度,
    p/d/x 是累计 处理/过载丢弃/等待过期(与 queue_drop 诊断同口径)。
    丢弃计数只在非零时展示(常态不加噪音)。解析不了返回 ""(不显示,不抛异常)。
    """
    vals = {}
    for part in data.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                vals[k.strip()] = int(v)
            except ValueError:
                pass
    if not all(k in vals for k in ("e", "h", "n", "l")):
        return ""
    line = f"队列 e{vals['e']} h{vals['h']} n{vals['n']} l{vals['l']}"
    dropped = vals.get("d", 0)
    expired = vals.get("x", 0)
    if dropped or expired:
        line += f" | 丢弃 过载{dropped} 过期{expired}"
    return line


class OSDStatusWindow(QWidget):
    """A small, always-on-top OSD status window"""

    def __init__(self, parent=None, width=300, height=80):
        super().__init__(parent)

        # 设置窗口标题供AHK查找
        self.setWindowTitle("TorchLightAssistant_OSD_12345")

        # Window properties - 不使用WindowTransparentForInput，通过Windows API精确控制
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # 透明背景与输入穿透，避免获取焦点与拦截鼠标键盘
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        try:
            # Qt 5.12+ 可用，PySide6 支持；若不可用则忽略异常
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        except Exception as e:
            LOG_INFO(f"[异常] 捕获到Exception: {e}")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowOpacity(0.8)

        self.width = width
        self.height = height
        self.resize(width, height)

        # Position window at center-left
        screen = self.screen().availableGeometry()
        center_x = screen.width() // 2
        center_y = screen.height() // 2
        x = center_x - self.width - 200  # 200 pixels to the left of center
        y = center_y + 100  # 100 pixels below center
        self.move(x, y)

        # Create layout and label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.status_label = QLabel("ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFont(QFont("微软雅黑", 11, QFont.Weight.Bold))
        self.status_label.setWordWrap(True)  # Enable word wrap
        self.status_label.setStyleSheet(
            "QLabel { color: white; background-color: rgba(0, 0, 0, 120); border-radius: 8px; padding: 8px; }"
        )

        layout.addWidget(self.status_label)

        # Hide initially
        self.hide()

    def update_from_macro_state(self, state):
        """根据宏引擎的状态更新OSD显示"""
        state_text = {
            "STOPPED": ("已停止", "gray"),
            "READY": ("就绪", "yellow"),
            "RUNNING": ("运行中", "lime"),
            "PAUSED": ("已暂停", "orange"),
        }.get(state.name, ("未知", "red"))

        self.status_label.setText(state_text[0])
        self.status_label.setStyleSheet(
            f"QLabel {{ color: {state_text[1]}; background-color: rgba(0, 0, 0, 120); border-radius: 8px; padding: 8px; }}"
        )

    def update_status(self, text: str, color: str = "white"):
        """Updates the OSD display, supporting multi-line text."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"QLabel {{ color: {color}; background-color: rgba(0, 0, 0, 120); border-radius: 8px; padding: 8px; }}"
        )

        # Dynamically adjust window height based on content
        lines = text.count("\n") + 1
        new_height = max(80, 30 + lines * 20)
        if new_height != self.height:
            self.height = new_height
            self.resize(self.width, self.height)


    

    def show(self):
        super().show()

    def hide(self):
        super().hide()

    def destroy(self):
        """Destroy the status bar"""
        self.deleteLater()
