"""高效输入处理 - 使用Pynput实现最佳游戏兼容性"""

import time
from typing import Optional, Dict, Any
import threading
from queue import Empty, Full
from ..utils.priority_deque import PriorityDeque
from ..utils.multi_priority_queue import MultiPriorityQueue
from .event_bus import event_bus
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO

# 使用Pynput控制器进行输入模拟
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
        debug_display_manager=None,  # 添加Debug Manager
    ):
        if not PYNPUT_AVAILABLE:
            raise RuntimeError("Pynput is required for InputHandler")

        self.input_lock = threading.Lock()
        self.keyboard = KeyboardController()
        self.mouse = MouseController()
        self.hotkey_manager = hotkey_manager
        self.config_manager = None  # Defer initialization
        self.debug_display_manager = debug_display_manager  # 保存引用
        self.dry_run_mode = False  # 添加干跑模式开关

        # 可配置的时间间隔
        self.key_press_duration = key_press_duration
        self.mouse_click_duration = mouse_click_duration

        # --- 新增：队列和线程管理 ---
        # 使用多级优先队列
        self._key_queue = MultiPriorityQueue(maxsize=9)
        self._queued_keys_set = set()
        self._processing_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._queue_full_warned = False

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

        # 高效按键映射 - 只映射特殊键，普通字符直接使用
        self.special_key_mapping = {
            "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
            "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
            "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
            "space": Key.space, "enter": Key.enter, "shift": Key.shift,
            "ctrl": Key.ctrl, "alt": Key.alt, "tab": Key.tab,
            "esc": Key.esc, "backspace": Key.backspace, "delete": Key.delete,
        }
        
        # --- 统一的优先级按键配置 ---
        self._priority_keys_pressed = set()  # 当前按下的优先级按键
        self._priority_keys_config = {}  # {source_key: {"target": target_key, "delay": ms, "type": "managed"|"monitoring"}}
        self._registered_priority_keys = set()  # 已注册到热键管理器的按键
        self._priority_mode_enabled = True  # 是否启用优先级模式

        self._ahk_enabled = True
        self._ahk_window_title = "HoldServer_Window_UniqueName_12345"
        self._ahk_hwnd = None  # 缓存句柄，减少 FindWindow 频率

        self._setup_event_subscriptions()
        self._start_priority_listeners()  # 启动优先级按键监听

    def _start_priority_listeners(self):
        """使用热键管理器注册优先级按键监听"""
        if not self._priority_mode_enabled or not self._priority_keys_config or not self.hotkey_manager:
            return
            
        try:
            for key_name, config in self._priority_keys_config.items():
                if key_name not in self._registered_priority_keys:
                    suppress_mode = "never" if config.get('type') == 'monitoring' else "always"
                    
                    self.hotkey_manager.register_key_event(
                        key_name,
                        on_press=lambda k=key_name: self._on_priority_key_press(k),
                        on_release=lambda k=key_name: self._on_priority_key_release(k),
                        suppress=suppress_mode
                    )
                    self._registered_priority_keys.add(key_name)
            
            LOG_INFO(f"[输入处理器] 优先级按键监听已启动: {self._priority_keys_config}")
        except Exception as e:
            LOG_ERROR(f"[输入处理器] 启动监听失败: {e}")

    def _on_priority_key_press(self, key_name: str):
        """统一的优先级按键按下处理"""
        try:
            if key_name not in self._priority_keys_config:
                return

            if key_name not in self._priority_keys_pressed:
                was_empty = len(self._priority_keys_pressed) == 0
                self._priority_keys_pressed.add(key_name)
                
                if was_empty:
                    self._pause_skill_scheduler()

                config = self._priority_keys_config[key_name]
                key_type = config.get('type', 'monitoring')

                if key_type == 'monitoring':
                    # 特殊按键：只监控状态，不拦截，不重发
                    LOG_INFO(f"[优先级按键] {key_name} 特殊按键 - 仅监控状态")
                elif key_type in ['managed', 'remapping']:
                    # 管理按键和映射按键：统一使用延迟+队列机制
                    target_key = config.get('target', key_name)
                    delay_ms = config.get('delay', 50)
                    
                    # 统一使用队列处理，确保延迟生效
                    self._handle_managed_key_with_delay(target_key, delay_ms, key_name)
                    
                    # 管理按键处理后立即释放状态
                    self._priority_keys_pressed.discard(key_name)
                    if len(self._priority_keys_pressed) == 0:
                        self._resume_skill_scheduler()

        except Exception as e:
            LOG_ERROR(f"[优先级按键] 按键按下处理异常: {e}")

    def _on_priority_key_release(self, key_name: str):
        """统一的优先级按键释放处理"""
        try:
            if key_name in self._priority_keys_pressed:
                self._priority_keys_pressed.discard(key_name)
                if len(self._priority_keys_pressed) == 0:
                    self._resume_skill_scheduler()
        except Exception as e:
            LOG_ERROR(f"[优先级按键] 按键释放处理异常: {e}")

    def _handle_managed_key_with_delay(self, target_key: str, delay_ms: int, source_key: str):
        """统一处理管理按键（包括普通管理和重映射），确保延迟生效"""
        try:
            if source_key != target_key:
                LOG_INFO(f"[按键映射] {source_key} → {target_key} (延迟: {delay_ms}ms)")
            else:
                LOG_INFO(f"[管理按键] {source_key} 程序接管 (延迟: {delay_ms}ms)")
            
            # 使用紧急优先级队列，确保延迟和按键顺序执行
            if delay_ms > 0:
                self._key_queue.put(f"delay{delay_ms}", priority='emergency', block=False)
            self._key_queue.put(target_key, priority='emergency', block=False)
            
        except Exception as e:
            LOG_ERROR(f"[管理按键] 处理失败: {e}")

    def _pause_skill_scheduler(self):
        """暂停技能调度器以节省CPU资源"""
        try:
            event_bus.publish('scheduler_pause_requested', {
                'reason': 'priority_key_pressed',
                'active_keys': list(self._priority_keys_pressed)
            })
            LOG_INFO("[性能优化] 优先级按键激活 - 技能调度器已暂停")
        except Exception as e:
            LOG_ERROR(f"[性能优化] 暂停调度器失败: {e}")
    
    def _resume_skill_scheduler(self):
        """恢复技能调度器"""
        try:
            event_bus.publish('scheduler_resume_requested', {
                'reason': 'priority_key_released'
            })
            LOG_INFO("[性能优化] 优先级按键释放 - 技能调度器已恢复")
        except Exception as e:
            LOG_ERROR(f"[性能优化] 恢复调度器失败: {e}")

    def _normalize_key_name(self, key: str) -> str:
        """标准化按键名称，避免大小写和格式问题"""
        if not key:
            return ""
        
        normalized = key.lower().strip()
        
        key_mapping = {
            'left_mouse': 'left_mouse', 'leftmouse': 'left_mouse', 'lbutton': 'left_mouse', 'leftclick': 'left_mouse',
            'right_mouse': 'right_mouse', 'rightmouse': 'right_mouse', 'rbutton': 'right_mouse', 'rightclick': 'right_mouse',
            'middle_mouse': 'middle_mouse', 'middlemouse': 'middle_mouse', 'mbutton': 'middle_mouse',
            'spacebar': 'space', 'space_bar': 'space',
            'ctrl': 'ctrl', 'control': 'ctrl',
            'shift': 'shift',
            'alt': 'alt',
            'enter': 'enter', 'return': 'enter',
            'tab': 'tab',
            'escape': 'esc',
        }
        
        return key_mapping.get(normalized, normalized)

    def set_dry_run_mode(self, enabled: bool):
        """开启或关闭干跑模式"""
        self.dry_run_mode = enabled
        LOG_INFO(f"[输入处理器] 干跑模式已 {'开启' if enabled else '关闭'}")

    def set_dodge_mode(self, enabled: bool):
        """开启或关闭优先级模式（原闪避模式）"""
        self._priority_mode_enabled = enabled
        if enabled and not self._registered_priority_keys and self._priority_keys_config:
            self._start_priority_listeners()
        elif not enabled and self._registered_priority_keys:
            self._stop_priority_listeners()
        LOG_INFO(f"[输入处理器] 优先级模式已 {'开启' if enabled else '关闭'} (配置按键数: {len(self._priority_keys_config)})")

    def set_priority_keys(self, config: dict):
        """设置优先级按键配置，采用统一的新格式"""
        
        # 新格式: { "special_keys": ["space"], "managed_keys": {"right_mouse": 50, "e": {"target": "0", "delay": 30}} }
        special_keys = config.get('special_keys', [])
        managed_keys_config = config.get('managed_keys', {})
        
        # 兼容旧格式
        if 'monitoring_keys' in config:
            special_keys = config.get('monitoring_keys', [])
        
        new_config = {}

        # 处理特殊按键（监控按键）
        for key in special_keys:
            normalized_key = self._normalize_key_name(key)
            if normalized_key:
                new_config[normalized_key] = {"type": "monitoring"}

        # 处理管理按键（包括普通和重映射）
        for key, value in managed_keys_config.items():
            normalized_key = self._normalize_key_name(key)
            if not normalized_key:
                continue
            
            if isinstance(value, dict) and 'target' in value:
                # 重映射按键：拦截 key，发送 target
                new_config[normalized_key] = {
                    "type": "remapping",
                    "target": value['target'],
                    "delay": max(0, int(value.get('delay', 50)))
                }
            else:
                # 普通管理按键：拦截并重发同名按键（但只支持映射按键）
                # 因为纯简单延迟配置会导致拦截后无法成功发送，所以跳过
                LOG_INFO(f"[输入处理器] 跳过简单延迟配置: {key} = {value} （需要映射配置才能正常工作）")
                continue

        self._priority_keys_config = new_config
        
        LOG_INFO(f"[输入处理器] 优先级按键已更新: {self._priority_keys_config}")
        
        # 重启监听器以应用新配置
        if self._priority_mode_enabled:
            if self._registered_priority_keys:
                self._stop_priority_listeners()
            if self._priority_keys_config:
                self._start_priority_listeners()

    def _stop_priority_listeners(self):
        """停止并注销所有优先级按键监听"""
        try:
            if self.hotkey_manager and self._registered_priority_keys:
                for key_name in list(self._registered_priority_keys):
                    self.hotkey_manager.unregister_hotkey(key_name)
                self._registered_priority_keys.clear()
            self._priority_keys_pressed.clear()
            LOG_INFO("[输入处理器] 优先级按键监听已停止")
        except Exception as e:
            LOG_ERROR(f"[输入处理器] 停止优先级监听器时出错: {e}")

    def is_priority_mode_active(self) -> bool:
        """检查是否有优先级按键正在按下"""
        return self._priority_mode_enabled and bool(self._priority_keys_pressed)

    def _setup_event_subscriptions(self):
        """订阅事件以接收状态更新"""
        event_bus.subscribe("engine:status_updated", self._on_status_updated)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

    def _on_config_updated(self, skills_config: Dict, global_config: Dict):
        """从全局配置中更新输入处理器的相关配置"""
        # ... (省略部分不相关代码)
        
        priority_keys_config = global_config.get("priority_keys", {})
        if priority_keys_config:
            enabled = priority_keys_config.get("enabled", True)
            self.set_dodge_mode(enabled)
            self.set_priority_keys(priority_keys_config)
        
        self.key_press_duration = global_config.get("key_press_duration", 10) / 1000.0
        self.mouse_click_duration = (
            global_config.get("mouse_click_duration", 5) / 1000.0
        )

    def _on_status_updated(self, status_info: Dict[str, Any]):
        """响应状态更新，更新缓存的状态信息"""
        self._cached_force_move = status_info.get("force_move_active", False)
        self._cached_stationary_mode = status_info.get("stationary_mode", False)

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
        LOG_INFO("[输入处理器] 开始清理资源...")

        # 1. 首先停止优先级监听器
        self._stop_priority_listeners()

        # 2. 停止处理线程
        self._stop_event.set()
        if self._processing_thread and self._processing_thread.is_alive():
            try:
                self._processing_thread.join(timeout=2.0)
                if self._processing_thread.is_alive():
                    LOG_ERROR("[输入处理器] 处理线程未能在规定时间内停止")
            except Exception as e:
                LOG_ERROR(f"[输入处理器] 停止处理线程时出错: {e}")

        # 3. 清理队列和状态
        try:
            self._key_queue.clear()
            self._queued_keys_set.clear()
            self._priority_keys_pressed.clear()
        except Exception as e:
            LOG_ERROR(f"[输入处理器] 清理队列时出错: {e}")

        LOG_INFO("[输入处理器] 资源清理完成")

    def _queue_processor_loop(self):
        """队列处理器循环"""
        while not self._stop_event.is_set():
            try:
                key_to_execute = self._key_queue.get(timeout=0.1)
                self._queued_keys_set.discard(key_to_execute)
            except Empty:
                continue

            try:
                key_lower = key_to_execute.lower()

                # 处理延迟指令
                if key_lower.startswith("delay"):
                    try:
                        delay_ms = int(key_lower[5:])
                        if self._stop_event.wait(delay_ms / 1000.0):
                            break
                    except (ValueError, IndexError):
                        LOG_ERROR(f"[按键处理] 无效的延迟按键格式: {key_to_execute}")
                    continue

                # 使用缓存的状态进行决策
                if self._cached_force_move:
                    # X键按下时，所有技能键都改成F键（交互键）
                    self._execute_key("f")
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

    def get_queue_length(self) -> int:
        """获取当前队列长度"""
        return self._key_queue.qsize()

    def clear_queue(self):
        """安全地清空按键队列和跟踪集合"""
        self._key_queue.clear()
        self._queued_keys_set.clear()
        LOG_INFO("[输入处理器] 按键队列已清空")

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

            # 获取按键对象 - 优化：直接检查特殊键，普通字符直接使用
            if key_lower in self.special_key_mapping:
                key_obj = self.special_key_mapping[key_lower]
            elif len(key_lower) == 1 and key_lower.isalnum():
                key_obj = key_lower  # 普通字符直接使用
            else:
                return False

            # 发送按键事件 - 按下并释放
            self.keyboard.press(key_obj)
            time.sleep(self.key_press_duration)
            self.keyboard.release(key_obj)

            return True

        except Exception:
            return False

    def click_mouse(self, button: str = "left", hold_time: Optional[float] = None) -> bool:
        """使用Pynput点击鼠标"""
        with self.input_lock:
            try:
                return self._click_mouse_pynput(button, hold_time)
            except Exception as e:
                LOG_ERROR(f"Error clicking mouse: {e}")
                return False

    def _click_mouse_pynput(self, button: str, hold_time: Optional[float] = None) -> bool:
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

            # 获取按键对象 - 优化：直接检查特殊键，普通字符直接使用
            if key_lower in self.special_key_mapping:
                key_obj = self.special_key_mapping[key_lower]
            elif len(key_lower) == 1 and key_lower.isalnum():
                key_obj = key_lower  # 普通字符直接使用
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

    def click_mouse_with_modifier(self, button: str = "left", modifier: str = "shift", hold_time: Optional[float] = None) -> bool:
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
                click_duration = hold_time if hold_time is not None else self.mouse_click_duration

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

    # ========== AHK Hold/Release 对外接口 ==========
    def set_ahk_hold(self, enabled: bool = True, window_title: Optional[str] = None):
        """配置是否启用 AHK 处理 hold/release，以及服务窗口标题"""
        self._ahk_enabled = bool(enabled)
        if window_title:
            self._ahk_window_title = window_title
            self._ahk_hwnd = None  # 标题变更后重置缓存

    def hold_key(self, key: str) -> bool:
        """通过 AHK 发送按住指令（仅处理按住/释放，不影响其他输入路径）"""
        # 干跑模式拦截
        if self.dry_run_mode:
            if self.debug_display_manager:
                self.debug_display_manager.add_action(f"Hold:{key}")
            return True  # 在干跑模式中，我们认为操作是"成功"的

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
        # 干跑模式拦截
        if self.dry_run_mode:
            if self.debug_display_manager:
                self.debug_display_manager.add_action(f"Release:{key}")
            return True  # 在干跑模式中，我们认为操作是"成功"的

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