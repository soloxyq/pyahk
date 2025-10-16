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

def send_ahk_cmd(window_title: str, cmd_id: int, param: str = "") -> bool:
    """
    发送命令到AHK服务器
    
    Args:
        window_title: AHK窗口标题
        cmd_id: 命令ID
        param: 命令参数（可选）
    
    Returns:
        命令执行结果（True/False）
    """
    hwnd = FindWindowW(None, window_title)
    if not hwnd:
        print(f"[AHK客户端][ERROR] Window not found: {window_title}")
        return False
    
    # 准备数据
    cds = COPYDATASTRUCT()
    cds.dwData = cmd_id  # 命令ID
    
    if param:
        # 有参数时，传递UTF-8编码的字符串
        data_bytes = param.encode('utf-8')
        buf = ctypes.create_string_buffer(data_bytes, len(data_bytes) + 1)
        cds.cbData = len(data_bytes)
        cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
    else:
        # 无参数时，传递空数据
        cds.cbData = 0
        cds.lpData = None
    
    # 发送消息
    res = SendMessageW(hwnd, WM_COPYDATA, 0, ctypes.addressof(cds))
    return res == 1  # AHK返回1表示成功

# 示例调用
if __name__ == "__main__":
    WIN = "HoldServer_Window_UniqueName_12345"
    import time
    # 按住 w
    send_ahk_cmd(WIN, "hold:w")
    time.sleep(5)
    # 释放 w
    send_ahk_cmd(WIN, "release:w")
