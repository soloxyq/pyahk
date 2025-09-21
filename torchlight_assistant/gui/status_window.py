from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont
from typing import Optional



class OSDStatusWindow(QWidget):
    """A small, always-on-top OSD status window"""

    def __init__(self, parent=None, width=300, height=80):
        super().__init__(parent)

        # Window properties
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
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
