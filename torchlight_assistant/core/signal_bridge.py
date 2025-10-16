from PySide6.QtCore import QObject, Signal

class SignalBridge(QObject):
    """
    一个简单的QObject子类，用作信号桥梁，
    允许非Qt线程安全地与主GUI线程通信。
    """
    # 定义一个信号，它将携带一个字符串参数（来自AHK的事件）
    ahk_event = Signal(str)

# 创建一个全局单例，以便在应用程序的任何地方都可以访问
ahk_signal_bridge = SignalBridge()
