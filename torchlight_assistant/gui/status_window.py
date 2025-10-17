from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont
from typing import Optional
import ctypes
from ctypes import wintypes
from ..utils.debug_log import LOG, LOG_ERROR

# Windows API常量
WM_COPYDATA = 0x004A



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

    def nativeEvent(self, eventType, message):
        """处理Windows原生消息，特别是WM_COPYDATA"""
        if eventType == "windows_generic_MSG":
            try:
                # 解析消息结构
                msg = wintypes.MSG.from_address(message.__int__())

                # 只处理WM_COPYDATA消息
                if msg.message == WM_COPYDATA:
                    LOG(f"[OSD WM_COPYDATA] 检测到WM_COPYDATA消息")
                    # 处理WM_COPYDATA消息
                    self._handle_wm_copydata(msg.wParam, msg.lParam)
                    return True, 0

            except Exception as e:
                LOG_ERROR(f"[OSD WM_COPYDATA] 处理原生消息失败: {e}")

        return False, 0
    
    def _handle_wm_copydata(self, wParam, lParam):
        """处理WM_COPYDATA消息内容"""
        try:
            LOG(f"[OSD WM_COPYDATA] 开始处理消息，wParam: {wParam}, lParam: {lParam}")

            # 定义COPYDATASTRUCT结构
            class COPYDATASTRUCT(ctypes.Structure):
                _fields_ = [
                    ("dwData", ctypes.c_void_p),
                    ("cbData", ctypes.c_ulong),
                    ("lpData", ctypes.c_void_p)
                ]

            # 从lParam解析COPYDATASTRUCT
            cds = COPYDATASTRUCT.from_address(lParam)

            LOG(f"[OSD WM_COPYDATA] 解析结构：dwData={cds.dwData}, cbData={cds.cbData}, lpData={cds.lpData}")

            # 检查是否是AHK事件消息（使用9999作为标识）
            if cds.dwData == 9999 and cds.cbData > 0:
                # 读取事件数据
                event_data = ctypes.string_at(cds.lpData, cds.cbData).decode('utf-8')

                LOG(f"[OSD WM_COPYDATA] 成功解码事件数据: {event_data}")

                # 处理AHK事件
                self._process_ahk_event(event_data)

                LOG(f"[OSD WM_COPYDATA] 收到AHK事件: {event_data}")
            else:
                LOG(f"[OSD WM_COPYDATA] 不是AHK事件消息，dwData={cds.dwData}, cbData={cds.cbData}")

        except Exception as e:
            LOG_ERROR(f"[OSD WM_COPYDATA] 解析消息失败: {e}")
    
    def _process_ahk_event(self, event_data: str):
        """处理从AHK接收到的事件"""
        try:
            # 直接使用信号桥接发射事件
            from ..core.signal_bridge import ahk_signal_bridge
            ahk_signal_bridge.ahk_event.emit(event_data)

        except Exception as e:
            LOG_ERROR(f"[OSD WM_COPYDATA] 处理AHK事件失败: {e}")

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
