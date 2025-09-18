"""高效输入处理 - 使用Pynput实现最佳游戏兼容性"""

import time
from typing import Optional, Dict, Any
import threading
from queue import Queue, Empty, Full
from .event_bus import event_bus
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO

# 使用Pynput - 更好的游戏兼容性，类似AHK的实现方式
try:
    from pynput.keyboard import Key, Controller as KeyboardController
    from pynput.mouse import Button, Controller as MouseController

    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    raise ImportError(
        "Pynput is required for optimal game compatibility. Install with: pip install pynput"
    )

# 导入窗口工具
from ..utils.window_utils import WindowUtils

# 导入win32相关库
try:
    import win32gui
    import win32con

    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

# ========== WM_COPYDATA 客户端（用于 AHK 按住/释放）==========
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

IsWindow = user32.IsWindow
IsWindow.argtypes = [wintypes.HWND]
IsWindow.restype = wintypes.BOOL

class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_ulong),
        ("lpData", ctypes.c_void_p),
    ]
# ============================================================


class InputHandler:
    """
    高效输入处理器 - 使用Pynput实现，类似AHK的游戏兼容性
    现在它拥有并管理一个按键队列，使其成为一个独立的输入服务。
    """

    def __init__(
        self,
        hotkey_manager,
        key_press_duration: float = 0.01,
        mouse_click_duration: float = 0.005,
    ):
        if not PYNPUT_AVAILABLE:
            raise RuntimeError("Pynput is required for InputHandler")

        self.input_lock = threading.Lock()
        self.keyboard = KeyboardController()
        self.mouse = MouseController()
        self.hotkey_manager = hotkey_manager
        self.config_manager = None  # Defer initialization

        # 可配置的时间间隔
        self.key_press_duration = key_press_duration
        self.mouse_click_duration = mouse_click_duration

        # --- 新增：队列和线程管理 ---
        self._key_queue = Queue(maxsize=9)
        self._queued_keys_set = set()
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._queue_full_warned = False
        
        # --- 新增：性能监控 ---
        self._processed_keys_count = 0
        self._last_performance_log_time = time.time()
        self._performance_log_interval = 60  # 每60秒记录一次性能数据

        # --- 新增：缓存的状态信息 ---
        self._cached_force_move = False
        self._cached_stationary_mode = False
        self._cached_stationary_mode_type = "block_mouse"

        # 简化的窗口激活配置
        self.window_activation_config = {
            "enabled": False,
            "ahk_class": None,
            "ahk_exe": None,
        }

        # 按键映射 - 支持常用游戏按键
        self.key_mapping = {
            "a": "a",
            "b": "b",
            "c": "c",
            "d": "d",
            "e": "e",
            "f": "f",
            "g": "g",
            "h": "h",
            "i": "i",
            "j": "j",
            "k": "k",
            "l": "l",
            "m": "m",
            "n": "n",
            "o": "o",
            "p": "p",
            "q": "q",
            "r": "r",
            "s": "s",
            "t": "t",
            "u": "u",
            "v": "v",
            "w": "w",
            "x": "x",
            "y": "y",
            "z": "z",
            "f1": Key.f1,
            "f2": Key.f2,
            "f3": Key.f3,
            "f4": Key.f4,
            "f5": Key.f5,
            "f6": Key.f6,
            "f7": Key.f7,
            "f8": Key.f8,
            "f9": Key.f9,
            "f10": Key.f10,
            "f11": Key.f11,
            "f12": Key.f12,
            "space": Key.space,
            "enter": Key.enter,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "tab": Key.tab,
            "esc": Key.esc,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "1": "1",
            "2": "2",
            "3": "3",
            "4": "4",
            "5": "5",
            "6": "6",
            "7": "7",
            "8": "8",
            "9": "9",
            "0": "0",
        }
        # --- 新增：AHK Hold 集成配置 ---
        self._ahk_enabled = True
        self._ahk_window_title = "HoldServer_Window_UniqueName_12345"
        self._ahk_hwnd = None  # 缓存句柄，减少 FindWindow 频率

        self._setup_event_subscriptions()

    def _setup_event_subscriptions(self):
        """订阅事件以接收状态更新"""
        event_bus.subscribe("engine:status_updated", self._on_status_updated)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

    def _on_status_updated(self, status_info: Dict[str, Any]):
        """响应状态更新，更新缓存的状态信息"""
        self._cached_force_move = status_info.get("force_move_active", False)
        self._cached_stationary_mode = status_info.get("stationary_mode", False)

    def _on_config_updated(self, skills_config: Dict, global_config: Dict):
        """从全局配置中更新原地模式类型和窗口激活配置"""
        stationary_config = global_config.get("stationary_mode_config", {})
        self._cached_stationary_mode_type = stationary_config.get(
            "mode_type", "block_mouse"
        )
        
        # 更新窗口激活配置
        window_config = global_config.get("window_activation", {})
        self.set_window_activation(
            enabled=window_config.get("enabled", False),
            ahk_class=window_config.get("ahk_class", ""),
            ahk_exe=window_config.get("ahk_exe", "")
        )
        
        # Update timing from global config
        self.key_press_duration = global_config.get("key_press_duration", 10) / 1000.0
        self.mouse_click_duration = (
            global_config.get("mouse_click_duration", 5) / 1000.0
        )

    def start(self):
        """启动按键队列处理线程"""
        if self._processing_thread and self._processing_thread.is_alive():
            return
        self._stop_event.clear()
        self._processing_thread = threading.Thread(
            target=self._queue_processor_loop,
            name="InputHandler-QueueProcessor",
            daemon=True,
        )
        self._processing_thread.start()
        LOG_INFO("[输入处理器] 按键队列处理线程已启动")

    def cleanup(self):
        """停止并清理资源"""
        self._stop_event.set()
        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=0.5)
        with self._key_queue.mutex:
            self._key_queue.queue.clear()
        self._queued_keys_set.clear()
        LOG_INFO("[输入处理器] 按键队列处理线程已停止")

    def execute_key(self, key: str):
        """执行按键请求（通过队列异步处理）"""
        if not key:
            return

        key_lower = key.lower()
        # 对于非延迟和非鼠标左键的按键，进行去重检查
        if not key_lower.startswith("delay") and key_lower not in [
            "lbutton",
            "leftclick",
        ]:
            if key in self._queued_keys_set:
                return  # O(1) 复杂度去重

        try:
            self._key_queue.put_nowait(key)
            self._queued_keys_set.add(key)  # 同步添加到集合
            self._queue_full_warned = False  # Reset warning on successful put
        except Full:
            if not self._queue_full_warned:
                LOG_ERROR("[输入队列] 队列已满，按键被丢弃。")
                self._queue_full_warned = True

    def get_queue_length(self) -> int:
        """获取当前队列长度"""
        return self._key_queue.qsize()

    def _queue_processor_loop(self):
        """队列处理器循环"""
        while not self._stop_event.is_set():
            try:
                key_to_execute = self._key_queue.get(timeout=0.1)
                # 移除时检查是否存在，避免KeyError
                self._queued_keys_set.discard(key_to_execute)
            except Empty:
                continue

            try:
                key_lower = key_to_execute.lower()
                
                # 增加性能计数
                self._processed_keys_count += 1
                
                # 定期记录性能数据
                current_time = time.time()
                if current_time - self._last_performance_log_time >= self._performance_log_interval:
                    queue_size = self.get_queue_length()
                    keys_per_second = self._processed_keys_count / self._performance_log_interval
                    LOG(f"[输入性能] 队列长度: {queue_size}, 处理速度: {keys_per_second:.2f} keys/s")
                    self._processed_keys_count = 0
                    self._last_performance_log_time = current_time
                
                # 处理延迟指令
                if key_lower.startswith("delay"):
                    try:
                        delay_ms = int(key_lower[5:])
                        if self._stop_event.wait(delay_ms / 1000.0):
                            break
                    except (ValueError, IndexError):
                        LOG_ERROR(f"[按键处理] 无效的延迟按键格式: {key_to_execute}")
                    continue

                # 使用缓存的状态和配置进行决策
                if self._cached_force_move:
                    # A键按下时，所有技能键都改成F键（交互键）
                    self.send_key("f")
                elif self._cached_stationary_mode:
                    if self._cached_stationary_mode_type == "block_mouse":
                        if key_lower not in ["lbutton", "leftclick", "rbutton", "rightclick"]:
                            self._execute_key(key_to_execute)
                    elif self._cached_stationary_mode_type == "shift_modifier":
                        self._execute_key_with_shift(key_to_execute)
                else:
                    self._execute_key(key_to_execute)

            except Exception as e:
                LOG_ERROR(f"[队列处理器] 处理按键 '{key_to_execute}' 时发生异常: {e}")
            finally:
                self._key_queue.task_done()

    def _execute_key(self, key_str: str):
        """根据按键类型执行具体输入操作"""
        key_lower = key_str.lower()
        if key_lower in ["lbutton", "leftclick"]:
            self.click_mouse("left")
        elif key_lower in ["rbutton", "rightclick"]:
            self.click_mouse("right")
        else:
            self.send_key(key_str)

    def _execute_key_with_shift(self, key_str: str):
        """执行带Shift修饰符的按键"""
        key_lower = key_str.lower()
        if key_lower in ["lbutton", "leftclick"]:
            self.click_mouse_with_modifier("left", "shift")
        elif key_lower in ["rbutton", "rightclick"]:
            self.click_mouse_with_modifier("right", "shift")
        else:
            self.send_key_with_modifier(key_str, "shift")

    def set_window_activation(
        self, enabled: bool = True, ahk_class: str = None, ahk_exe: str = None
    ) -> bool:
        """设置窗口激活配置

        Args:
            ahk_class: 窗口类名 (例如: 'Qt5156QWindowIcon')
            ahk_exe: 进程可执行文件名 (例如: 'MuMuNxDevice.exe')
            enabled: 是否启用窗口激活功能

        Returns:
            是否设置成功
        """
        try:
            # 清理参数，将None转换为空字符串
            ahk_class = ahk_class or ""
            ahk_exe = ahk_exe or ""

            self.window_activation_config.update(
                {"enabled": enabled, "ahk_class": ahk_class, "ahk_exe": ahk_exe}
            )

            pass

            return True
        except Exception as e:
            return False

    def activate_target_window(self):
        """根据配置激活目标窗口。"""
        if not self.window_activation_config["enabled"] or not WIN32_AVAILABLE:
            return False

        ahk_class = self.window_activation_config.get("ahk_class", "").strip()
        ahk_exe = self.window_activation_config.get("ahk_exe", "").strip()

        # 检查是否有有效的配置参数
        if not ahk_class and not ahk_exe:
            return False

        hwnd = None
        # 优先使用类名查找
        if ahk_class:
            hwnd = win32gui.FindWindow(ahk_class, None)

        # 如果类名找不到，并且提供了进程名，则使用进程名查找
        if not hwnd and ahk_exe:
            hwnd = WindowUtils.find_window_by_process_name(ahk_exe)

        if hwnd:
            try:
                # 使用改进的WindowUtils.activate_window方法
                success = WindowUtils.activate_window(hwnd)
                return success
            except Exception as e:
                return False
        else:
            return False

    def send_key(self, key_str: str) -> bool:
        """使用Pynput发送按键 - 高游戏兼容性"""
        if not key_str:
            return False

        with self.input_lock:
            try:
                return self._send_key_pynput(key_str)
            except Exception as e:
                LOG_ERROR(f"Error sending key {key_str}: {e}")
                return False

    def _send_key_pynput(self, key_str: str) -> bool:
        """使用Pynput发送按键 - 类似AHK SendInput的实现"""
        try:
            key_lower = key_str.lower()

            # 获取按键对象
            if key_lower in self.key_mapping:
                key_obj = self.key_mapping[key_lower]
            else:
                return False

            # 发送按键事件 - 按下并释放
            self.keyboard.press(key_obj)
            time.sleep(self.key_press_duration)
            self.keyboard.release(key_obj)

            return True

        except Exception:
            return False

    def click_mouse(
        self, button: str = "left", hold_time: Optional[float] = None
    ) -> bool:
        """使用Pynput点击鼠标"""
        with self.input_lock:
            try:
                return self._click_mouse_pynput(button, hold_time)
            except Exception as e:
                LOG_ERROR(f"Error clicking mouse: {e}")
                return False

    def _click_mouse_pynput(
        self, button: str, hold_time: Optional[float] = None
    ) -> bool:
        """使用Pynput点击鼠标 - 类似AHK Click的实现"""
        try:
            if button.lower() == "left":
                mouse_button = Button.left
            elif button.lower() == "right":
                mouse_button = Button.right
            elif button.lower() == "lbutton":  # 兼容原有代码
                mouse_button = Button.left
            elif button.lower() == "rbutton":  # 兼容原有代码
                mouse_button = Button.right
            else:
                return False

            # 发送鼠标事件
            self.mouse.press(mouse_button)
            time.sleep(hold_time or self.mouse_click_duration)
            self.mouse.release(mouse_button)

            return True

        except Exception:
            return False

    def get_mouse_position(self) -> tuple:
        """获取鼠标位置"""
        try:
            return self.mouse.position
        except Exception:
            return (0, 0)

    def is_key_pressed(self, key_str: str) -> bool:
        """检查按键是否被按下"""
        return self.hotkey_manager.is_key_pressed(key_str)

    def send_key_with_modifier(self, key_str: str, modifier: str = "shift") -> bool:
        """发送带修饰符的按键"""
        if not key_str:
            return False

        with self.input_lock:
            try:
                return self._send_key_with_modifier_pynput(key_str, modifier)
            except Exception as e:
                LOG_ERROR(f"Error sending key {key_str} with {modifier}: {e}")
                return False

    def _send_key_with_modifier_pynput(self, key_str: str, modifier: str) -> bool:
        """使用Pynput发送带修饰符的按键"""
        try:
            key_lower = key_str.lower()
            modifier_lower = modifier.lower()

            # 获取按键对象
            if key_lower in self.key_mapping:
                key_obj = self.key_mapping[key_lower]
            else:
                return False

            # 获取修饰符对象
            modifier_obj = None
            if modifier_lower == "shift":
                modifier_obj = Key.shift
            elif modifier_lower == "ctrl":
                modifier_obj = Key.ctrl
            elif modifier_lower == "alt":
                modifier_obj = Key.alt
            else:
                return False

            # 发送修饰符+按键事件
            self.keyboard.press(modifier_obj)
            time.sleep(0.001)  # 短暂延迟确保修饰符生效
            self.keyboard.press(key_obj)
            time.sleep(self.key_press_duration)
            self.keyboard.release(key_obj)
            self.keyboard.release(modifier_obj)

            return True

        except Exception:
            return False

    def click_mouse_at(self, x: int, y: int, button: str = "left", 
                      hold_time: Optional[float] = None) -> bool:
        """
        在指定坐标点击鼠标
        
        Args:
            x, y: 点击坐标
            button: 鼠标按钮 ("left", "right", "middle")
            hold_time: 按住时间（秒）
        """
        with self.input_lock:
            try:
                return self._click_mouse_at_pynput(x, y, button, hold_time)
            except Exception as e:
                LOG_ERROR(f"Error clicking mouse at ({x}, {y}): {e}")
                return False
    
    def _click_mouse_at_pynput(self, x: int, y: int, button: str, 
                              hold_time: Optional[float] = None) -> bool:
        """使用pynput在指定坐标点击鼠标"""
        if not PYNPUT_AVAILABLE:
            return False
        
        try:
            # 映射按钮名称
            button_map = {
                "left": Button.left,
                "right": Button.right,
                "middle": Button.middle,
            }
            
            mouse_button = button_map.get(button.lower(), Button.left)
            
            # 移动鼠标到指定位置
            self.mouse.position = (x, y)
            time.sleep(0.01)  # 短暂延迟确保鼠标移动完成
            
            # 点击
            self.mouse.press(mouse_button)
            time.sleep(hold_time or self.mouse_click_duration)
            self.mouse.release(mouse_button)
            
            return True
            
        except Exception as e:
            LOG_ERROR(f"Error in _click_mouse_at_pynput: {e}")
            return False

    def click_mouse_with_modifier(
        self,
        button: str = "left",
        modifier: str = "shift",
        hold_time: Optional[float] = None,
    ) -> bool:
        """发送带修饰符的鼠标点击"""
        with self.input_lock:
            try:
                # 获取修饰符对象
                modifier_obj = None
                modifier_lower = modifier.lower()
                if modifier_lower == "shift":
                    modifier_obj = Key.shift
                elif modifier_lower == "ctrl":
                    modifier_obj = Key.ctrl
                elif modifier_lower == "alt":
                    modifier_obj = Key.alt
                else:
                    return False

                # 获取鼠标按钮对象
                if button.lower() == "left":
                    button_obj = Button.left
                elif button.lower() == "right":
                    button_obj = Button.right
                elif button.lower() == "middle":
                    button_obj = Button.middle
                else:
                    return False

                # 使用传入的时间或默认时间
                click_duration = (
                    hold_time if hold_time is not None else self.mouse_click_duration
                )

                # 发送修饰符+鼠标点击事件
                self.keyboard.press(modifier_obj)
                time.sleep(0.001)  # 短暂延迟确保修饰符生效
                self.mouse.press(button_obj)
                time.sleep(click_duration)
                self.mouse.release(button_obj)
                self.keyboard.release(modifier_obj)

                return True

            except Exception as e:
                LOG_ERROR(f"Error clicking mouse {button} with {modifier}: {e}")
                return False

    def send_key_sequence(self, keys: list, delay: float = 0.1) -> bool:
        """发送按键序列"""
        try:
            for key in keys:
                if not self.send_key(key):
                    return False
                if delay > 0:
                    time.sleep(delay)
            return True
        except Exception as e:
            LOG_ERROR(f"Error sending key sequence: {e}")
            return False

    # ========== AHK Hold/Release 对外接口 ==========
    def set_ahk_hold(self, enabled: bool = True, window_title: Optional[str] = None):
        """配置是否启用 AHK 处理 hold/release，以及服务窗口标题"""
        self._ahk_enabled = bool(enabled)
        if window_title:
            self._ahk_window_title = window_title
            self._ahk_hwnd = None  # 标题变更后重置缓存

    def hold_key(self, key: str) -> bool:
        """通过 AHK 发送按住指令（仅处理按住/释放，不影响其他输入路径）"""
        if not key or not self._ahk_enabled:
            return False
        try:
            text = f"hold:{key}"
            return self._ahk_send(text)
        except Exception as e:
            LOG_ERROR(f"[AHK] hold_key 失败: {e}")
            return False

    def release_key(self, key: str) -> bool:
        """通过 AHK 发送释放指令"""
        if not key or not self._ahk_enabled:
            return False
        try:
            text = f"release:{key}"
            return self._ahk_send(text)
        except Exception as e:
            LOG_ERROR(f"[AHK] release_key 失败: {e}")
            return False

    # ---------- 内部：WM_COPYDATA 发送 ----------
    def _ahk_get_hwnd(self):
        """获取/缓存 AHK 服务窗口句柄"""
        if self._ahk_hwnd and IsWindow(self._ahk_hwnd):
            return self._ahk_hwnd
        hwnd = FindWindowW(None, self._ahk_window_title)
        self._ahk_hwnd = hwnd
        return hwnd

    def _ahk_send(self, text: str) -> bool:
        """向 AHK 服务窗口发送 WM_COPYDATA 文本（UTF-8，含 NUL）"""
        hwnd = self._ahk_get_hwnd()
        if not hwnd:
            LOG_ERROR(f"[AHK] 找不到服务窗口: {self._ahk_window_title}")
            return False
        data_bytes = text.encode("utf-8")
        buf = ctypes.create_string_buffer(data_bytes)  # 包含结尾 NUL
        cds = COPYDATASTRUCT()
        cds.dwData = 1
        cds.cbData = len(data_bytes) + 1  # 更稳：包含 NUL
        cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
        res = SendMessageW(hwnd, WM_COPYDATA, 0, ctypes.byref(cds))
        return bool(res)
    # =================================================

    def get_window_activation_status(self) -> Dict[str, Any]:
        """获取窗口激活配置状态

        Returns:
            包含窗口激活配置的字典
        """
        return {
            "enabled": self.window_activation_config["enabled"],
            "ahk_class": self.window_activation_config["ahk_class"],
            "ahk_exe": self.window_activation_config["ahk_exe"],
        }
