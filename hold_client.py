import ctypes
from ctypes import wintypes
# 兼容部分环境无 wintypes.LRESULT：LRESULT 为 LONG_PTR，使用 c_ssize_t 跨 32/64 位
LRESULT = ctypes.c_ssize_t

WM_COPYDATA = 0x004A

user32 = ctypes.WinDLL('user32', use_last_error=True)

FindWindowW = user32.FindWindowW
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
FindWindowW.restype = wintypes.HWND

SendMessageW = user32.SendMessageW
SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
SendMessageW.restype = LRESULT

class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p)
    ]

def send_ahk_cmd(window_title: str, text: str) -> bool:
    """给 AHk 常驻窗口发送 WM_COPYDATA。text 应为 UTF-8 字符串，如 "hold:w" 或 "release:w""""
    hwnd = FindWindowW(None, window_title)
    if not hwnd:
        return False
    # 编码为 UTF-8 bytes，C端要传指针
    data_bytes = text.encode('utf-8')
    buf = ctypes.create_string_buffer(data_bytes)  # buffer 包含 bytes 内容
    cds = COPYDATASTRUCT()
    cds.dwData = 1  # 用户自定义，可不使用
    cds.cbData = len(data_bytes)
    cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
    # 传递 POINTER(COPYDATASTRUCT)
    res = SendMessageW(hwnd, WM_COPYDATA, 0, ctypes.byref(cds))
    return True

# 示例调用
if __name__ == "__main__":
    WIN = "HoldServer_Window_UniqueName_12345"
    import time
    # 按住 w
    send_ahk_cmd(WIN, "hold:w")
    time.sleep(5)
    # 释放 w
    send_ahk_cmd(WIN, "release:w")
