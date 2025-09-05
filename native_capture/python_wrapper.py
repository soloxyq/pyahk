import ctypes
import ctypes.wintypes
from ctypes import Structure, POINTER, c_void_p, c_int, c_uint8, c_uint64, c_char_p
import os
import sys
from typing import Optional, Callable, Any
import numpy as np
from PIL import Image


# 错误码定义
class CaptureResult:
    SUCCESS = 0
    ERROR_INIT_FAILED = -1
    ERROR_WINDOW_NOT_FOUND = -2
    ERROR_CAPTURE_FAILED = -3
    ERROR_INVALID_PARAM = -4
    ERROR_NOT_INITIALIZED = -5


# 捕获区域结构
class CaptureRegion(Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int)]


# 捕获配置结构
class CaptureConfig(Structure):
    _fields_ = [
        ("capture_interval_ms", c_int),
        ("region", CaptureRegion),
        ("enable_region", c_int),  # 0 = false, 1 = true
    ]


# 帧数据结构
class CaptureFrame(Structure):
    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("stride", c_int),
        ("timestamp", c_uint64),
        ("data", POINTER(c_uint8)),
        ("data_size", c_int),
        ("format", c_int),
    ]


# 窗口信息结构
class WindowInfo(Structure):
    _fields_ = [("hwnd", ctypes.wintypes.HWND), ("title", ctypes.c_char * 256)]


# 回调函数类型
FrameCallbackType = ctypes.WINFUNCTYPE(None, POINTER(CaptureFrame), c_void_p)


class GameCaptureLib:
    """游戏画面捕获库的Python包装器"""

    def __init__(self, dll_path: Optional[str] = None):
        """初始化捕获库

        Args:
            dll_path: DLL文件路径，如果为None则自动查找
        """
        self._lib = None
        self._initialized = False
        self._sessions = {}

        # 查找DLL文件
        if dll_path is None:
            dll_path = self._find_dll()

        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"找不到DLL文件: {dll_path}")

        # 加载DLL
        try:
            # 使用绝对路径并添加当前目录到DLL搜索路径
            dll_path = os.path.abspath(dll_path)
            dll_dir = os.path.dirname(dll_path)

            # 添加DLL目录到PATH环境变量
            if dll_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")

            # 设置DLL目录为当前工作目录（临时）
            old_cwd = os.getcwd()
            try:
                os.chdir(dll_dir)
                self._lib = ctypes.CDLL(dll_path)
            finally:
                os.chdir(old_cwd)

            self._setup_functions()
        except Exception as e:
            raise RuntimeError(f"加载DLL失败: {e}")

    def _find_dll(self) -> str:
        """自动查找DLL文件"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            os.path.join(current_dir, "capture_lib.dll"),
            os.path.join(current_dir, "bin", "capture_lib.dll"),
            os.path.join(current_dir, "build", "bin", "capture_lib.dll"),
            os.path.join(current_dir, "build", "Release", "capture_lib.dll"),
            os.path.join(current_dir, "build", "Debug", "capture_lib.dll"),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                return path

        raise FileNotFoundError("找不到capture_lib.dll文件")

    def _setup_functions(self):
        """设置函数签名"""
        # capture_init
        self._lib.capture_init.argtypes = []
        self._lib.capture_init.restype = c_int

        # capture_cleanup
        self._lib.capture_cleanup.argtypes = []
        self._lib.capture_cleanup.restype = None

        # capture_create_window_session
        self._lib.capture_create_window_session.argtypes = [ctypes.wintypes.HWND]
        self._lib.capture_create_window_session.restype = c_void_p

        # capture_create_monitor_session
        self._lib.capture_create_monitor_session.argtypes = [c_int]
        self._lib.capture_create_monitor_session.restype = c_void_p

        # capture_start
        self._lib.capture_start.argtypes = [c_void_p]
        self._lib.capture_start.restype = c_int

        # capture_stop
        self._lib.capture_stop.argtypes = [c_void_p]
        self._lib.capture_stop.restype = c_int

        # capture_destroy_session
        self._lib.capture_destroy_session.argtypes = [c_void_p]
        self._lib.capture_destroy_session.restype = None

        # capture_get_frame
        self._lib.capture_get_frame.argtypes = [c_void_p]
        self._lib.capture_get_frame.restype = POINTER(CaptureFrame)

        # capture_free_frame (now a no-op)
        self._lib.capture_free_frame.argtypes = [POINTER(CaptureFrame)]
        self._lib.capture_free_frame.restype = None

        # capture_get_error_string
        self._lib.capture_get_error_string.argtypes = [c_int]
        self._lib.capture_get_error_string.restype = c_char_p

        # capture_get_last_error
        self._lib.capture_get_last_error.argtypes = []
        self._lib.capture_get_last_error.restype = c_int

        # capture_enum_windows
        self._lib.capture_enum_windows.argtypes = [POINTER(WindowInfo), c_int]
        self._lib.capture_enum_windows.restype = c_int

        # capture_get_window_title
        self._lib.capture_get_window_title.argtypes = [
            ctypes.wintypes.HWND,
            c_char_p,
            c_int,
        ]
        self._lib.capture_get_window_title.restype = c_int

        # capture_create_window_session_with_config
        self._lib.capture_create_window_session_with_config.argtypes = [
            ctypes.wintypes.HWND,
            POINTER(CaptureConfig),
        ]
        self._lib.capture_create_window_session_with_config.restype = c_void_p

        # capture_create_monitor_session_with_config
        self._lib.capture_create_monitor_session_with_config.argtypes = [
            c_int,
            POINTER(CaptureConfig),
        ]
        self._lib.capture_create_monitor_session_with_config.restype = c_void_p

        # capture_set_config
        self._lib.capture_set_config.argtypes = [c_void_p, POINTER(CaptureConfig)]
        self._lib.capture_set_config.restype = c_int

        # capture_get_config
        self._lib.capture_get_config.argtypes = [c_void_p, POINTER(CaptureConfig)]
        self._lib.capture_get_config.restype = c_int

        # capture_clear_frame_cache
        self._lib.capture_clear_frame_cache.argtypes = [c_void_p]
        self._lib.capture_clear_frame_cache.restype = None

    def initialize(self) -> bool:
        """初始化捕获库

        Returns:
            bool: 是否初始化成功
        """
        if self._initialized:
            return True

        result = self._lib.capture_init()
        if result == CaptureResult.SUCCESS:
            self._initialized = True
            return True
        else:
            print(f"初始化失败: {self.get_error_string(result)}")
            return False

    def cleanup(self):
        """清理捕获库"""
        if self._initialized:
            # 停止所有会话
            for session_id in list(self._sessions.keys()):
                self.destroy_session(session_id)

            self._lib.capture_cleanup()
            self._initialized = False

    def create_window_session(
        self, window_handle: int, config: Optional[dict] = None
    ) -> Optional[int]:
        """创建窗口捕获会话

        Args:
            window_handle: 窗口句柄
            config: 捕获配置字典

        Returns:
            Optional[int]: 会话ID，失败返回None
        """
        if not self._initialized:
            print("库未初始化")
            return None

        if config:
            c_config = self._dict_to_capture_config(config)
            handle = self._lib.capture_create_window_session_with_config(
                window_handle, ctypes.byref(c_config)
            )
        else:
            handle = self._lib.capture_create_window_session(window_handle)

        if handle:
            session_id = id(handle)  # Use a unique ID
            self._sessions[session_id] = handle
            return session_id
        else:
            error = self._lib.capture_get_last_error()
            print(f"创建窗口会话失败: {self.get_error_string(error)}")
            return None

    def create_monitor_session(
        self, monitor_index: int = 0, config: Optional[dict] = None
    ) -> Optional[int]:
        """创建显示器捕获会话

        Args:
            monitor_index: 显示器索引
            config: 捕获配置字典

        Returns:
            Optional[int]: 会话ID，失败返回None
        """
        if not self._initialized:
            print("库未初始化")
            return None

        if config:
            c_config = self._dict_to_capture_config(config)
            handle = self._lib.capture_create_monitor_session_with_config(
                monitor_index, ctypes.byref(c_config)
            )
        else:
            handle = self._lib.capture_create_monitor_session(monitor_index)

        if handle:
            session_id = id(handle)
            self._sessions[session_id] = handle
            return session_id
        else:
            error = self._lib.capture_get_last_error()
            print(f"创建显示器会话失败: {self.get_error_string(error)}")
            return None

    def start_capture(self, session_id: int) -> bool:
        """开始捕获

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否成功开始捕获
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return False

        handle = self._sessions[session_id]
        result = self._lib.capture_start(handle)

        if result == CaptureResult.SUCCESS:
            return True
        else:
            print(f"开始捕获失败: {self.get_error_string(result)}")
            return False

    def stop_capture(self, session_id: int) -> bool:
        """停止捕获

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否成功停止捕获
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return False

        handle = self._sessions[session_id]
        result = self._lib.capture_stop(handle)

        if result == CaptureResult.SUCCESS:
            return True
        else:
            print(f"停止捕获失败: {self.get_error_string(result)}")
            return False

    def get_frame(self, session_id: int) -> Optional[np.ndarray]:
        """获取单帧图像（同步方式, 零拷贝）

        Args:
            session_id: 会话ID

        Returns:
            Optional[np.ndarray]: 图像数据，格式为(height, width, 4) BGRA。
                                  返回的数组与C++共享内存，请勿长期持有。
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return None

        handle = self._sessions[session_id]
        frame_ptr = self._lib.capture_get_frame(handle)

        if frame_ptr and frame_ptr.contents.data:
            frame = frame_ptr.contents
            try:
                # --- Zero-Copy NumPy Array Creation ---
                # Create a NumPy array that directly uses the C++ buffer memory.
                # This is the core of the zero-copy mechanism.
                np_array = np.ctypeslib.as_array(
                    frame.data, shape=(frame.height, frame.width, 4)
                )

                # NOTE: The returned array shares memory with the C++ buffer.
                # The caller should process it immediately and not hold references to it,
                # as the buffer content will be overwritten by the next capture.

                # The C++ side now manages the memory, so we no longer call free_frame.
                # self._lib.capture_free_frame(frame_ptr) # This is now a NO-OP

                return np_array
            except Exception as e:
                print(f"创建零拷贝NumPy数组时出错: {e}")
                return None
        else:
            # This can happen if there's no new frame. It's not necessarily an error.
            return None

    def destroy_session(self, session_id: int):
        """销毁捕获会话

        Args:
            session_id: 会话ID
        """
        if session_id in self._sessions:
            handle = self._sessions[session_id]
            self._lib.capture_destroy_session(handle)
            del self._sessions[session_id]

    def set_config(self, session_id: int, config: dict) -> bool:
        """设置会话配置

        Args:
            session_id: 会话ID
            config: 捕获配置字典

        Returns:
            bool: 是否设置成功
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return False

        handle = self._sessions[session_id]
        c_config = self._dict_to_capture_config(config)
        result = self._lib.capture_set_config(handle, ctypes.byref(c_config))

        if result == CaptureResult.SUCCESS:
            return True
        else:
            print(f"设置配置失败: {self.get_error_string(result)}")
            return False

    def get_config(self, session_id: int) -> Optional[dict]:
        """获取会话配置

        Args:
            session_id: 会话ID

        Returns:
            Optional[dict]: 配置字典，失败返回None
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return None

        handle = self._sessions[session_id]
        c_config = CaptureConfig()
        result = self._lib.capture_get_config(handle, ctypes.byref(c_config))

        if result == CaptureResult.SUCCESS:
            return self._capture_config_to_dict(c_config)
        else:
            print(f"获取配置失败: {self.get_error_string(result)}")
            return None

    def clear_frame_cache(self, session_id: int):
        """清理帧缓存

        Args:
            session_id: 会话ID
        """
        if session_id not in self._sessions:
            print("无效的会话ID")
            return

        handle = self._sessions[session_id]
        self._lib.capture_clear_frame_cache(handle)

    def _dict_to_capture_config(self, config: dict) -> CaptureConfig:
        """将字典转换为CaptureConfig结构体"""
        c_config = CaptureConfig()
        c_config.capture_interval_ms = config.get("capture_interval_ms", 40)
        c_config.enable_region = 1 if config.get("enable_region", False) else 0

        region = config.get("region", {})
        c_config.region.x = region.get("x", 0)
        c_config.region.y = region.get("y", 0)
        c_config.region.width = region.get("width", 0)
        c_config.region.height = region.get("height", 0)

        return c_config

    def _capture_config_to_dict(self, c_config: CaptureConfig) -> dict:
        """将CaptureConfig结构体转换为字典"""
        return {
            "capture_interval_ms": c_config.capture_interval_ms,
            "enable_region": bool(c_config.enable_region),
            "region": {
                "x": c_config.region.x,
                "y": c_config.region.y,
                "width": c_config.region.width,
                "height": c_config.region.height,
            },
        }

    def get_error_string(self, error_code: int) -> str:
        """获取错误信息

        Args:
            error_code: 错误码

        Returns:
            str: 错误信息
        """
        result = self._lib.capture_get_error_string(error_code)
        return result.decode("utf-8") if result else "未知错误"

    def enum_windows(self, max_count: int = 100) -> list:
        """枚举窗口

        Args:
            max_count: 最大窗口数量

        Returns:
            list: (窗口句柄, 窗口标题) 元组列表
        """
        window_array = (WindowInfo * max_count)()

        count = self._lib.capture_enum_windows(window_array, max_count)

        if count > 0:
            result = []
            for i in range(count):
                hwnd = int(window_array[i].hwnd) if window_array[i].hwnd else 0
                title = (
                    window_array[i]
                    .title.decode("utf-8", errors="ignore")
                    .rstrip("\x00")
                )
                result.append((hwnd, title))
            return result
        else:
            return []

    def get_window_title(self, window_handle: int) -> str:
        """获取窗口标题

        Args:
            window_handle: 窗口句柄

        Returns:
            str: 窗口标题
        """
        buffer = ctypes.create_string_buffer(256)
        result = self._lib.capture_get_window_title(window_handle, buffer, 256)

        if result > 0:
            # 尝试多种编码方式来解码窗口标题
            raw_bytes = buffer.value
            for encoding in ['utf-8', 'gbk', 'gb2312', 'cp936', 'latin1']:
                try:
                    return raw_bytes.decode(encoding)
                except UnicodeDecodeError:
                    continue
            # 如果所有编码都失败，使用错误处理方式
            return raw_bytes.decode('utf-8', errors='replace')
        else:
            return ""

    def find_window_by_title(self, title_pattern: str) -> Optional[int]:
        """根据标题查找窗口

        Args:
            title_pattern: 标题模式（支持部分匹配）

        Returns:
            Optional[int]: 窗口句柄，未找到返回None
        """
        windows = self.enum_windows()
        for hwnd, title in windows:
            if title_pattern.lower() in title.lower():
                return hwnd
        return None

    def __enter__(self):
        """上下文管理器入口"""
        if not self.initialize():
            raise RuntimeError("初始化捕获库失败")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.cleanup()

    def __del__(self):
        """析构函数"""
        self.cleanup()


# 高级封装类 - 所有其他Python文件应该使用这个类而不是直接使用GameCaptureLib
class CaptureManager:
    """捕获管理器 - 封装所有C++库调用的高级接口"""

    def __init__(self, dll_path: Optional[str] = None):
        """初始化捕获管理器

        Args:
            dll_path: DLL文件路径，如果为None则自动查找
        """
        self._capture_lib = GameCaptureLib(dll_path)
        self._initialized = False

    def initialize(self) -> bool:
        """初始化捕获库

        Returns:
            bool: 是否初始化成功
        """
        if not self._initialized:
            self._initialized = self._capture_lib.initialize()
        return self._initialized

    def cleanup(self):
        """清理捕获库"""
        if self._initialized:
            self._capture_lib.cleanup()
            self._initialized = False

    def create_window_session(
        self, window_handle: int, config: Optional[dict] = None
    ) -> Optional[int]:
        """创建窗口捕获会话

        Args:
            window_handle: 窗口句柄
            config: 捕获配置字典

        Returns:
            Optional[int]: 会话ID，失败返回None
        """
        if not self._initialized:
            return None
        return self._capture_lib.create_window_session(window_handle, config)

    def create_monitor_session(
        self, monitor_index: int = 0, config: Optional[dict] = None
    ) -> Optional[int]:
        """创建显示器捕获会话

        Args:
            monitor_index: 显示器索引
            config: 捕获配置字典

        Returns:
            Optional[int]: 会话ID，失败返回None
        """
        if not self._initialized:
            return None
        return self._capture_lib.create_monitor_session(monitor_index, config)

    def start_capture(self, session_id: int) -> bool:
        """开始捕获

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否成功开始捕获
        """
        if not self._initialized:
            return False
        return self._capture_lib.start_capture(session_id)

    def stop_capture(self, session_id: int) -> bool:
        """停止捕获

        Args:
            session_id: 会话ID

        Returns:
            bool: 是否成功停止捕获
        """
        if not self._initialized:
            return False
        return self._capture_lib.stop_capture(session_id)

    def get_frame(self, session_id: int) -> Optional[np.ndarray]:
        """获取最新帧 (零拷贝)

        Args:
            session_id: 会话ID

        Returns:
            Optional[np.ndarray]: BGRA 图像数据。注意：此数组与C++共享内存，
                                  应立即处理，不要长期持有引用。
        """
        if not self._initialized:
            return None
        return self._capture_lib.get_frame(session_id)

    def destroy_session(self, session_id: int):
        """销毁捕获会话

        Args:
            session_id: 会话ID
        """
        if not self._initialized:
            return
        self._capture_lib.destroy_session(session_id)

    def set_config(self, session_id: int, config: dict) -> bool:
        """设置会话配置

        Args:
            session_id: 会话ID
            config: 捕获配置字典

        Returns:
            bool: 是否设置成功
        """
        if not self._initialized:
            return False
        return self._capture_lib.set_config(session_id, config)

    def get_config(self, session_id: int) -> Optional[dict]:
        """获取会话配置

        Args:
            session_id: 会话ID

        Returns:
            Optional[dict]: 配置字典，失败返回None
        """
        if not self._initialized:
            return None
        return self._capture_lib.get_config(session_id)

    def clear_frame_cache(self, session_id: int):
        """清理帧缓存"""
        if not self._initialized:
            return
        self._capture_lib.clear_frame_cache(session_id)

    def find_window_by_title(self, title_pattern: str) -> Optional[int]:
        """根据标题查找窗口

        Args:
            title_pattern: 窗口标题模式

        Returns:
            Optional[int]: 窗口句柄，失败返回None
        """
        if not self._initialized:
            return None
        return self._capture_lib.find_window_by_title(title_pattern)

    def enum_windows(self, max_count: int = 100) -> list:
        """枚举窗口

        Args:
            max_count: 最大窗口数量

        Returns:
            list: (窗口句柄, 窗口标题) 元组列表
        """
        if not self._initialized:
            return []
        return self._capture_lib.enum_windows(max_count)

    def get_window_title(self, window_handle: int) -> str:
        """获取窗口标题

        Args:
            window_handle: 窗口句柄

        Returns:
            str: 窗口标题
        """
        if not self._initialized:
            return ""
        return self._capture_lib.get_window_title(window_handle)

    def __enter__(self):
        """上下文管理器入口"""
        if not self.initialize():
            raise RuntimeError("初始化捕获库失败")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.cleanup()

    def __del__(self):
        """析构函数"""
        self.cleanup()


# 便捷函数
def capture_window_by_title(
    title_pattern: str, save_path: Optional[str] = None
) -> Optional[np.ndarray]:
    """根据窗口标题捕获单帧图像

    Args:
        title_pattern: 窗口标题模式
        save_path: 保存路径（可选）

    Returns:
        Optional[np.ndarray]: 图像数据
    """
    try:
        with CaptureManager() as capture:
            window_handle = capture.find_window_by_title(title_pattern)
            if not window_handle:
                print(f"找不到标题包含 '{title_pattern}' 的窗口")
                return None

            session_id = capture.create_window_session(window_handle)
            if session_id is None:
                return None

            if not capture.start_capture(session_id):
                return None

            import time

            time.sleep(0.1)  # Give it a moment to capture the first frame

            frame = capture.get_frame(session_id)
            if frame is not None and save_path:
                # The frame is BGRA, convert to RGB for saving with PIL
                rgb_frame = frame[:, :, [2, 1, 0]]  # BGRA -> RGB
                Image.fromarray(rgb_frame).save(save_path)
                print(f"图像已保存到: {save_path}")

            # Important: Return a copy if the caller needs to hold onto the data
            return frame.copy() if frame is not None else None
    except Exception as e:
        print(f"捕获失败: {e}")
        return None


if __name__ == "__main__":
    # 测试代码
    print("游戏画面捕获库零拷贝实现测试")

    try:
        with CaptureManager() as capture:
            print("库初始化成功")

            # Find a window to capture (e.g., Notepad)
            target_window_title = "Notepad"
            hwnd = capture.find_window_by_title(target_window_title)

            if not hwnd:
                print(
                    f"找不到标题包含 '{target_window_title}' 的窗口，请打开一个记事本窗口。"
                )
                # Fallback to enumerating all windows
                windows = capture.enum_windows()
                if not windows:
                    print("找不到任何可见窗口。")
                    sys.exit(1)
                hwnd, title = windows[0]
                print(f"将改为捕获第一个可用窗口: {title}")

            session_id = capture.create_window_session(hwnd)
            if session_id is not None:
                print(f"会话创建成功 (ID: {session_id})")

                if capture.start_capture(session_id):
                    print("开始捕获...")

                    # Capture for a few seconds
                    import time

                    start_time = time.time()
                    frames_captured = 0
                    while time.time() - start_time < 5:
                        frame = capture.get_frame(session_id)
                        if frame is not None:
                            frames_captured += 1
                            if frames_captured == 1:
                                # Save the first frame
                                print(f"第一帧捕获成功! 图像尺寸: {frame.shape}")
                                # Make a copy before saving
                                frame_copy = frame.copy()
                                # Convert BGRA to RGB for PIL
                                rgb_frame = frame_copy[:, :, [2, 1, 0]]
                                Image.fromarray(rgb_frame).save(
                                    "test_capture_zero_copy.png"
                                )
                                print("测试图像已保存为 test_capture_zero_copy.png")
                        # Small delay to prevent busy-waiting
                        time.sleep(0.016)  # ~60 FPS

                    end_time = time.time()
                    duration = end_time - start_time
                    fps = frames_captured / duration
                    print(
                        f"捕获结束。在 {duration:.2f} 秒内捕获了 {frames_captured} 帧 (平均FPS: {fps:.2f})"
                    )

                    capture.stop_capture(session_id)
                    print("捕获已停止。")

                capture.destroy_session(session_id)
                print("会话已销毁。")

    except Exception as e:
        print(f"测试失败: {e}")
        import traceback

        traceback.print_exc()
