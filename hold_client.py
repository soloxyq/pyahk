import ctypes
from ctypes import wintypes

# 兼容部分环境无 wintypes.LRESULT：LRESULT 为 LONG_PTR，使用 c_ssize_t 跨 32/64 位
LRESULT = ctypes.c_ssize_t

WM_COPYDATA = 0x004A

# SendMessageTimeoutW 标志(winuser.h)。这里刻意不用 SMTO_BLOCK:
# Python/AHK 会双向同步发送 WM_COPYDATA。发送线程等待期间必须继续处理传入的
# 非队列消息,否则双方同时发送时会互相等待,最终被误判成 500ms 挂死。
# 不加 SMTO_BLOCK 也与重构前 SendMessageW 的消息泵行为一致。
#   SMTO_ABORTIFHUNG  目标线程已被系统判定挂起时立即失败,不等满超时
#   SMTO_ERRORONEXIT  目标窗口销毁/线程退出时返回失败,而不是当成功
SMTO_ABORTIFHUNG = 0x0002
SMTO_ERRORONEXIT = 0x0020

ERROR_TIMEOUT = 1460  # winerror.h: 操作超时

# 单条命令的响应超时。AHK 实测处理耗时 0.069ms,500ms 是 4 个数量级的余量 ——
# 触发即视为消息循环挂死,不是"偶尔慢了一点"。
SEND_TIMEOUT_MS = 500

# ---- 传输结果分类(send_ahk_cmd_ex 的第二个返回值)----
SEND_OK = "ok"                # AHK 收到并返回 1
SEND_REJECTED = "rejected"    # AHK 收到并返回 2(业务层拒绝,通道本身是好的)
SEND_TIMEOUT = "timeout"      # 超时/挂起:AHK 进程可能还活着,但消息循环不再响应
SEND_NO_WINDOW = "no_window"  # 找不到 AHK 窗口 / 窗口已销毁(进程可能已退出)
SEND_ERROR = "error"          # 发送层自身异常(ctypes 等),防御性兜底

# AHK WM_COPYDATA 处理器返回值。0 保留给未处理/窗口过程默认返回,不能与业务拒绝
# 混用；调用方必须能区分“AHK 明确拒绝”和“没有取得协议层结果”。
AHK_RESULT_OK = 1
AHK_RESULT_REJECTED = 2

user32 = ctypes.WinDLL("user32", use_last_error=True)

FindWindowW = user32.FindWindowW
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
FindWindowW.restype = wintypes.HWND

# 用 SendMessageTimeoutW 而不是 SendMessageW:后者在 AHK 消息循环挂死时**永久阻塞**
# 调用线程(常是 GUI 线程)。进程退出有 ahk_process_died 探测兜底,挂死没有 —— 超时是唯一出路。
SendMessageTimeoutW = user32.SendMessageTimeoutW
SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,      # fuFlags
    wintypes.UINT,      # uTimeout (ms)
    ctypes.POINTER(ctypes.c_size_t),  # lpdwResult (DWORD_PTR)
]
SendMessageTimeoutW.restype = LRESULT


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p),
    ]


# 🎯 性能优化：AHK窗口句柄缓存
# AHK窗口启动后句柄不变，首次获取后可一直使用
_ahk_hwnd = 0  # 缓存的窗口句柄


def _classify_send(ret: int, msg_result: int, last_error: int) -> "tuple[bool, str]":
    """把 SendMessageTimeoutW 的三元结果归类为 (是否成功, 失败类型)。

    纯函数,便于直接单测:ffi 胶水只负责取这三个值。
    """
    if ret != 0:
        # 消息被处理,msg_result 是 AHK 端 WM_COPYDATA 处理器的返回值(1=成功)
        if msg_result == AHK_RESULT_OK:
            return True, SEND_OK
        if msg_result == AHK_RESULT_REJECTED:
            return False, SEND_REJECTED
        return False, SEND_ERROR
    if last_error == ERROR_TIMEOUT:
        return False, SEND_TIMEOUT
    # SMTO_ERRORONEXIT(窗口销毁/线程退出)与无效句柄都落在这里
    return False, SEND_NO_WINDOW


def send_ahk_cmd_ex(window_title: str, cmd_id: int, param: str = "") -> "tuple[bool, str]":
    """发送命令到 AHK 服务器,返回 (是否成功, 传输结果分类)。

    保证**永不抛异常**:上层把本调用作为 _check_send 的参数求值,异常在那一步
    逃逸会绕过所有失败处理(死亡探测/挂死告警)。任何意外都折叠成 SEND_ERROR。
    """
    global _ahk_hwnd

    try:
        # 🎯 优先使用缓存的句柄
        if _ahk_hwnd:
            hwnd = _ahk_hwnd
        else:
            # 首次查找并缓存
            hwnd = FindWindowW(None, window_title)
            if hwnd:
                _ahk_hwnd = hwnd
            else:
                print(f"[AHK客户端][ERROR] Window not found: {window_title}")
                return False, SEND_NO_WINDOW

        # 准备数据。⚠️ cds 与 buf 都必须在同步调用返回前保持存活:
        # WM_COPYDATA 的接收方在消息处理期间直接读这块调用方内存
        # (SendMessageTimeoutW 是同步调用,局部变量的生命周期正好罩住它;
        #  不要把这段封装成"返回 lparam 的辅助函数",buf 会提前被回收)。
        cds = COPYDATASTRUCT()
        cds.dwData = cmd_id  # 命令ID

        buf = None
        if param:
            # 有参数时，传递UTF-8编码的字符串
            data_bytes = param.encode("utf-8")
            buf = ctypes.create_string_buffer(data_bytes, len(data_bytes) + 1)
            cds.cbData = len(data_bytes)
            cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
        else:
            # 无参数时，传递空数据
            cds.cbData = 0
            cds.lpData = None

        msg_result = ctypes.c_size_t(0)
        # SendMessageTimeoutW 失败时并不保证每条路径都覆盖 last-error。
        # 先清零,避免上一条超时留下的 ERROR_TIMEOUT 污染本次分类。
        ctypes.set_last_error(0)
        ret = SendMessageTimeoutW(
            hwnd,
            WM_COPYDATA,
            0,
            ctypes.addressof(cds),
            SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT,
            SEND_TIMEOUT_MS,
            ctypes.byref(msg_result),
        )
        last_error = ctypes.get_last_error() if ret == 0 else 0
        del buf  # 显式标注生命周期终点:同步调用已返回,此后才允许回收

        ok, kind = _classify_send(ret, msg_result.value, last_error)

        # 🎯 窗口失效时清除缓存,下次重新查找;超时**不**清缓存 —— 窗口还在,只是挂了,
        # 反复 FindWindow 只会把同一个挂死窗口再找回来。
        if kind == SEND_NO_WINDOW:
            _ahk_hwnd = 0
        return ok, kind
    except Exception as e:  # 防御:ctypes 层意外异常不得逃逸(见 docstring)
        try:
            print(f"[AHK客户端][ERROR] 发送异常: {e}")
        except Exception:
            pass
        return False, SEND_ERROR


def send_ahk_cmd(window_title: str, cmd_id: int, param: str = "") -> bool:
    """兼容入口:只关心成功与否的调用方使用。失败分类见 send_ahk_cmd_ex。"""
    ok, _kind = send_ahk_cmd_ex(window_title, cmd_id, param)
    return ok


# 示例调用
if __name__ == "__main__":
    WIN = "HoldServer_Window_UniqueName_12345"
    import time

    # 按住 w
    send_ahk_cmd(WIN, "hold:w")
    time.sleep(5)
    # 释放 w
    send_ahk_cmd(WIN, "release:w")
