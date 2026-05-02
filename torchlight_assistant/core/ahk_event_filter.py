"""AHK 事件全局过滤器

通过 QAbstractNativeEventFilter 安装到 QApplication,集中接收来自 AHK 子进程的
WM_COPYDATA 事件,解析后通过 SignalBridge 在主线程统一分发。

设计动机:
- 替代 MainWindow / OSDStatusWindow 各自实现的 nativeEvent,消除重复代码
- 单一接收点,避免多窗口同时监听导致潜在的重复处理
- 不消费消息(返回 False),不影响 Qt 默认窗口过程
"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

from .signal_bridge import ahk_signal_bridge
from ..utils.debug_log import LOG, LOG_ERROR

WM_COPYDATA = 0x004A
AHK_EVENT_DWDATA = 9999  # AHK SendWMCopyDataToPython 中约定的事件标识


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p),
    ]


class AHKEventFilter(QAbstractNativeEventFilter):
    """全局原生事件过滤器:从 WM_COPYDATA 提取 AHK 事件并转发"""

    def nativeEventFilter(self, eventType, message):
        # eventType 在 Windows 上为 b"windows_generic_MSG" 或 "windows_generic_MSG"
        if eventType != b"windows_generic_MSG" and eventType != "windows_generic_MSG":
            return False, 0

        try:
            msg = wintypes.MSG.from_address(message.__int__())
            if msg.message != WM_COPYDATA:
                return False, 0

            cds = _COPYDATASTRUCT.from_address(msg.lParam)
            if cds.dwData != AHK_EVENT_DWDATA or cds.cbData == 0:
                return False, 0

            event_data = ctypes.string_at(cds.lpData, cds.cbData).decode("utf-8")
            ahk_signal_bridge.ahk_event.emit(event_data)
            LOG(f"[AHK事件过滤器] 收到事件: {event_data}")

        except Exception as e:
            LOG_ERROR(f"[AHK事件过滤器] 处理 WM_COPYDATA 失败: {e}")

        # 不消费消息,允许其他过滤器/窗口默认过程继续处理
        return False, 0
