from PySide6.QtWidgets import QCheckBox, QLineEdit, QComboBox, QRadioButton, QSpinBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent, QFocusEvent, QKeyEvent


class ConfigLineEdit(QLineEdit):
    def __init__(self, update_callback=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_callback = update_callback

    def focusOutEvent(self, event: QFocusEvent):
        """当焦点离开时，如果提供了回调，则调用更新回调"""
        super().focusOutEvent(event)
        if self.update_callback:
            self.update_callback()


class ConfigCheckBox(QCheckBox):
    def __init__(self, text, update_callback=None, *args, **kwargs):
        super().__init__(text, *args, **kwargs)
        self.update_callback = update_callback

    def mouseReleaseEvent(self, event: QMouseEvent):
        """当鼠标释放时，如果提供了回调，则调用更新回调"""
        super().mouseReleaseEvent(event)
        if self.update_callback:
            self.update_callback()


class ConfigComboBox(QComboBox):
    def __init__(self, update_callback=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_callback = update_callback
        # 使用currentTextChanged信号来确保值真正改变时才触发回调
        if self.update_callback:
            self.currentTextChanged.connect(self.update_callback)


class ConfigRadioButton(QRadioButton):
    def __init__(self, text, update_callback=None, *args, **kwargs):
        super().__init__(text, *args, **kwargs)
        self.update_callback = update_callback
        # 使用toggled信号来确保只在状态真正改变时才触发回调
        if self.update_callback:
            self.toggled.connect(
                lambda checked: self.update_callback() if checked else None
            )


class ConfigSpinBox(QSpinBox):
    def __init__(self, update_callback=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_callback = update_callback

    def focusOutEvent(self, event: QFocusEvent):
        """当焦点离开时，如果提供了回调，则调用更新回调"""
        super().focusOutEvent(event)
        if self.update_callback:
            self.update_callback()
