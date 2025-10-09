"""高效输入处理 - 使用Pynput实现最佳游戏兼容性"""

import time
from typing import Optional, Dict, Any
import threading
from queue import Empty, Full
from ..utils.priority_deque import PriorityDeque
from ..utils.multi_priority_queue import MultiPriorityQueue
from .event_bus import event_bus
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO

# 使用Pynput - 更好的游戏兼容性，类似AHK的实现方式
try:
    from pynput.keyboard import Key, Controller as KeyboardController, Listener as KeyboardListener
    from pynput.mouse import Button, Controller as MouseController, Listener as MouseListener

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
        # --- 新增：优先级按键状态监控 ---
        self._priority_keys_pressed = set()  # 当前按下的优先级按键
        self._priority_keys_config = {'space', 'right_mouse'}  # 默认优先级按键配置
        self._priority_key_delay = 0.05  # 优先级按键前置延迟（秒）- 确保游戏响应
        self._keyboard_listener = None
        self._mouse_listener = None
        self._priority_mode_enabled = True  # 是否启用优先级模式
        self._ahk_enabled = True
        self._ahk_window_title = "HoldServer_Window_UniqueName_12345"
        self._ahk_hwnd = None  # 缓存句柄，减少 FindWindow 频率

        self._setup_event_subscriptions()
        self._start_priority_listeners()  # 启动优先级按键监听

    def _start_priority_listeners(self):
        """启动键盘和鼠标监听器 - 只监听配置的优先级按键"""
        if not self._priority_mode_enabled:
            return
            
        try:
            # 启动键盘监听器 - 只处理优先级按键，其他按键被忽略
            self._keyboard_listener = KeyboardListener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
                suppress=False  # 不抑制按键，让其他程序正常接收
            )
            self._keyboard_listener.start()
            
            # 启动鼠标监听器 - 只处理优先级鼠标按键，其他点击被忽略
            self._mouse_listener = MouseListener(
                on_click=self._on_mouse_click,
                suppress=False  # 不抑制鼠标，让其他程序正常接收
            )
            self._mouse_listener.start()
            
            LOG_INFO(f"[输入处理器] 优先级按键监听已启动: {self._priority_keys_config}")
        except Exception as e:
            LOG_ERROR(f"[输入处理器] 启动监听失败: {e}")

    def _on_key_press(self, key):
        """键盘按下事件处理 - 只处理优先级按键"""
        try:
            key_name = self._get_key_name(key)
            
            # 只处理配置的优先级按键，忽略其他所有按键
            if key_name in self._priority_keys_config:
                was_empty = len(self._priority_keys_pressed) == 0
                self._priority_keys_pressed.add(key_name)
                
                # 🚀 如果是第一个优先级按键被按下，暂停技能调度器
                if was_empty:
                    self._pause_skill_scheduler()
                
                # 🎯 新增：优先级按键需要确保游戏响应，先加延迟再发送
                self._execute_priority_key_with_delay(key_name)
                
        except Exception as e:
            LOG_ERROR(f"[优先级按键] _on_key_press异常: {e}")

    def _on_key_release(self, key):
        """键盘释放事件处理 - 只处理优先级按键"""
        try:
            key_name = self._get_key_name(key)
            
            # 只处理配置的优先级按键，忽略其他所有按键
            if key_name in self._priority_keys_config:
                self._priority_keys_pressed.discard(key_name)
                
                # 🚀 如果所有优先级按键都释放了，恢复技能调度器
                if len(self._priority_keys_pressed) == 0:
                    self._resume_skill_scheduler()
                
        except Exception as e:
            LOG_ERROR(f"[优先级按键] _on_key_release异常: {e}")

    def _on_mouse_click(self, x, y, button, pressed):
        """鼠标点击事件处理 - 只处理优先级按键"""
        try:
            button_name = self._get_button_name(button)
            
            # 只处理配置的优先级按键，忽略其他所有鼠标按键
            if button_name in self._priority_keys_config:
                if pressed:
                    was_empty = len(self._priority_keys_pressed) == 0
                    self._priority_keys_pressed.add(button_name)
                    
                    # 🚀 如果是第一个优先级按键被按下，暂停技能调度器
                    if was_empty:
                        self._pause_skill_scheduler()
                    
                    # 🎯 新增：优先级按键需要确保游戏响应，先加延迟再发送
                    self._execute_priority_key_with_delay(button_name)
                else:
                    self._priority_keys_pressed.discard(button_name)
                    
                    # 🚀 如果所有优先级按键都释放了，恢复技能调度器
                    if len(self._priority_keys_pressed) == 0:
                        self._resume_skill_scheduler()
                    
        except Exception as e:
            LOG_ERROR(f"[优先级按键] _on_mouse_click异常: {e}")

    def _execute_priority_key_with_delay(self, key_name: str):
        """执行优先级按键 - 添加延迟确保游戏响应"""
        try:
            # 使用紧急优先级，添加延迟后发送按键
            delay_command = f"delay={int(self._priority_key_delay * 1000)}"  # 转换为毫秒
            self._key_queue.put(delay_command, priority='emergency', block=False)
            self._key_queue.put(key_name, priority='emergency', block=False)
        except Full:
            LOG_ERROR(f"[优先级按键] 紧急队列已满，优先级按键 {key_name} 被丢弃。")
            
    def _pause_skill_scheduler(self):
        """暂停技能调度器以节省CPU资源"""
        try:
            # 通过事件总线通知 SkillManager 暂停调度
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
            # 通过事件总线通知 SkillManager 恢复调度
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
        
        # 基本标准化：小写并去除空格
        normalized = key.lower().strip()
        
        # 统一按键名称映射
        key_mapping = {
            # 鼠标按键标准化
            'left_mouse': 'left_mouse',
            'leftmouse': 'left_mouse',
            'mouse_left': 'left_mouse',
            'lbutton': 'left_mouse',
            'leftclick': 'left_mouse',
            
            'right_mouse': 'right_mouse',
            'rightmouse': 'right_mouse',
            'mouse_right': 'right_mouse',
            'rbutton': 'right_mouse',
            'rightclick': 'right_mouse',
            
            'middle_mouse': 'middle_mouse',
            'middlemouse': 'middle_mouse',
            'mouse_middle': 'middle_mouse',
            'mbutton': 'middle_mouse',
            
            # 特殊键标准化
            'spacebar': 'space',
            'space_bar': 'space',
            'ctrl': 'ctrl',
            'control': 'ctrl',
            'shift': 'shift',
            'alt': 'alt',
            'enter': 'enter',
            'return': 'enter',
            'tab': 'tab',
            'escape': 'esc',
            'esc': 'esc',
        }
        
        # 应用映射
        return key_mapping.get(normalized, normalized)

    def _get_key_name(self, key) -> str:
        """获取按键名称"""
        if key == Key.space:
            return 'space'
        elif hasattr(key, 'char') and key.char:
            return self._normalize_key_name(key.char)
        else:
            return self._normalize_key_name(str(key).replace('Key.', ''))

    def _get_button_name(self, button) -> str:
        """获取鼠标按键名称"""
        if button == Button.left:
            return 'left_mouse'
        elif button == Button.right:
            return 'right_mouse'
        elif button == Button.middle:
            return 'middle_mouse'
        else:
            return self._normalize_key_name(str(button))

    def set_dry_run_mode(self, enabled: bool):
        """开启或关闭干跑模式"""
        self.dry_run_mode = enabled
        LOG_INFO(f"[输入处理器] 干跑模式已 {'开启' if enabled else '关闭'}")

    def set_dodge_mode(self, enabled: bool):
        """开启或关闭优先级模式（原闪避模式）"""
        self._priority_mode_enabled = enabled
        if enabled and not self._keyboard_listener:
            self._start_priority_listeners()
        elif not enabled and self._keyboard_listener:
            self._stop_priority_listeners()
        LOG_INFO(f"[输入处理器] 优先级模式已 {'开启' if enabled else '关闭'}")

    def set_priority_keys(self, keys_list: list):
        """设置优先级按键列表
        
        Args:
            keys_list: 按键名称列表，如 ['space', 'right_mouse', 'ctrl']
        """
        # 标准化所有按键名称
        normalized_keys = {self._normalize_key_name(key) for key in keys_list if key}
        self._priority_keys_config = normalized_keys
        LOG_INFO(f"[输入处理器] 优先级按键已更新: {self._priority_keys_config}")

    def _stop_priority_listeners(self):
        """停止优先级按键监听器"""
        try:
            if self._keyboard_listener:
                self._keyboard_listener.stop()
                # 等待监听器完全停止
                try:
                    self._keyboard_listener.join(timeout=1.0)
                except:
                    pass
                self._keyboard_listener = None
                
            if self._mouse_listener:
                self._mouse_listener.stop()
                # 等待监听器完全停止
                try:
                    self._mouse_listener.join(timeout=1.0)
                except:
                    pass
                self._mouse_listener = None
                
            self._priority_keys_pressed.clear()
            LOG_INFO("[输入处理器] 优先级按键监听已停止")
            
        except Exception as e:
            LOG_ERROR(f"[输入处理器] 停止优先级监听器时出错: {e}")
            # 强制清理
            self._keyboard_listener = None
            self._mouse_listener = None
            self._priority_keys_pressed.clear()

    def is_priority_mode_active(self) -> bool:
        """检查是否有优先级按键正在按下"""
        return self._priority_mode_enabled and bool(self._priority_keys_pressed)

    def get_active_priority_keys(self) -> set:
        """获取当前按下的优先级按键"""
        return self._priority_keys_pressed.copy()

    def set_priority_key_delay(self, delay_ms: int):
        """设置优先级按键延迟时间（毫秒）"""
        self._priority_key_delay = max(0, delay_ms) / 1000.0  # 转换为秒，确保非负
        LOG_INFO(f"[输入处理器] 优先级按键延迟已设置为: {delay_ms}ms")

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
        
        # 更新优先级按键配置
        priority_keys_config = global_config.get("priority_keys", {})
        if priority_keys_config:
            enabled = priority_keys_config.get("enabled", True)
            keys = priority_keys_config.get("keys", ["space", "right_mouse"])
            delay = priority_keys_config.get("delay_ms", 50)  # 默认50毫秒延迟
            
            self.set_dodge_mode(enabled)
            self.set_priority_keys(keys)
            self.set_priority_key_delay(delay)
            LOG_INFO(f"[输入处理器] 优先级按键配置已更新: 启用={enabled}, 按键={keys}, 延迟={delay}ms")
        
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
        LOG_INFO("[输入处理器] 开始清理资源...")
        
        # 1. 首先停止优先级监听器
        self._stop_priority_listeners()
        
        # 2. 停止处理线程
        self._stop_event.set()
        if self._processing_thread and self._processing_thread.is_alive():
            try:
                self._processing_thread.join(timeout=2.0)  # 增加超时时间
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

    def _should_deduplicate_key(self, key: str) -> bool:
        """判断按键是否应该去重
        
        Args:
            key: 按键字符串，可能是单个按键或序列（如 "delay50,q"）
        
        Returns:
            bool: True表示应该去重，False表示允许重复
        """
        key_lower = key.lower()
        
        # 🎯 序列处理：如果包含逗号，检查序列中是否有不去重的元素
        if ',' in key_lower:
            key_sequence = [k.strip() for k in key_lower.split(',') if k.strip()]
            for individual_key in key_sequence:
                # 如果序列中有任何一个元素不需要去重，整个序列就不去重
                if individual_key.startswith("delay"):
                    return False
                if individual_key in ["1", "2"] and hasattr(self, '_is_emergency_key'):
                    return False
                if individual_key in ["lbutton", "leftclick"]:
                    return False
            # 序列中所有元素都需要去重时，整个序列才去重
            return True
        
        # 单个按键的去重逻辑（保持原有逻辑）
        # 延迟指令不去重
        if key_lower.startswith("delay"):
            return False
            
        # 紧急药剂按键不去重（保证生存）
        if key_lower in ["1", "2"] and hasattr(self, '_is_emergency_key'):
            return False
            
        # 鼠标左键在特定情况下不去重（如连击技能）
        if key_lower in ["lbutton", "leftclick"]:
            return False
            
        return True

    def execute_key(self, key: str, priority: bool = False):
        """执行按键请求（通过队列异步处理）

        Args:
            key: 要执行的按键
            priority: 是否为高优先级按键（插入队列前端）
        """
        # 干跑模式拦截
        if self.dry_run_mode:
            if self.debug_display_manager:
                self.debug_display_manager.add_action(key)
            return

        if not key:
            return

        # 统一的去重检查
        if self._should_deduplicate_key(key) and key in self._queued_keys_set:
            return  # O(1) 复杂度去重

        try:
            if priority:
                self._key_queue.put(key, priority='high', block=False)
            else:
                self._key_queue.put(key, priority='normal', block=False)
            self._queued_keys_set.add(key)
            self._queue_full_warned = False
        except Full:
            # 区分日志文案
            if not self._queue_full_warned:
                if priority:
                    LOG_ERROR("[输入队列] 高优先级队列已满，按键被丢弃。")
                else:
                    LOG_ERROR("[输入队列] 队列已满，按键被丢弃。")
                self._queue_full_warned = True

    # 语义化的按键执行接口
    def execute_hp_potion(self, key: str):
        """执行HP药剂按键 - 紧急优先级（闪避时仍然响应）"""
        if not key:
            return
        try:
            self._key_queue.put(key, priority='emergency', block=False)
            self._queued_keys_set.add(key)
        except Full:
            LOG_ERROR("[输入队列] 紧急队列已满，HP药剂被丢弃！")

    def execute_mp_potion(self, key: str):
        """执行MP药剂按键 - 紧急优先级（闪避时仍然响应）"""
        if not key:
            return
        try:
            self._key_queue.put(key, priority='emergency', block=False)
            self._queued_keys_set.add(key)
        except Full:
            LOG_ERROR("[输入队列] 紧急队列已满，MP药剂被丢弃！")

    def _check_priority_mode_block(self) -> bool:
        """快速优先级模式检查 - 无日志版本"""
        return self.is_priority_mode_active()

    def execute_skill_high(self, key: str):
        """执行高优先级技能按键 - 支持序列 delay50,q"""
        if not key or self._check_priority_mode_block():
            return
        
        # 🎯 整体序列去重检查：序列作为一个整体进行去重判断
        if self._should_deduplicate_key(key) and key in self._queued_keys_set:
            return  # 整个序列已经在队列中，跳过
        
        # 🎯 支持逗号分隔的按键序列
        if ',' in key:
            key_sequence = [k.strip() for k in key.split(',') if k.strip()]
            for i, individual_key in enumerate(key_sequence):
                try:
                    self._key_queue.put(individual_key, priority='high', block=False)
                    # 在最后一个元素后添加清理标记
                    if i == len(key_sequence) - 1 and self._should_deduplicate_key(key):
                        cleanup_marker = f"__cleanup_sequence__{key}"
                        self._key_queue.put(cleanup_marker, priority='high', block=False)
                except Full:
                    LOG_ERROR("[输入队列] 高优先级队列已满，序列按键被丢弃。")
                    break
            # 将整个序列字符串加入去重集合
            if self._should_deduplicate_key(key):
                self._queued_keys_set.add(key)
        else:
            # 原有的单个按键逻辑
            try:
                self._key_queue.put(key, priority='high', block=False)
                if self._should_deduplicate_key(key):
                    self._queued_keys_set.add(key)
            except Full:
                LOG_ERROR("[输入队列] 高优先级队列已满，技能被丢弃。")

    def execute_skill_normal(self, key: str):
        """执行普通技能按键 - 支持序列 delay50,q"""
        if not key or self._check_priority_mode_block():
            return
        
        # 🎯 整体序列去重检查：序列作为一个整体进行去重判断
        if self._should_deduplicate_key(key) and key in self._queued_keys_set:
            return  # 整个序列已经在队列中，跳过
        
        # 🎯 支持逗号分隔的按键序列
        if ',' in key:
            key_sequence = [k.strip() for k in key.split(',') if k.strip()]
            for i, individual_key in enumerate(key_sequence):
                try:
                    self._key_queue.put(individual_key, priority='normal', block=False)
                    # 在最后一个元素后添加清理标记
                    if i == len(key_sequence) - 1 and self._should_deduplicate_key(key):
                        cleanup_marker = f"__cleanup_sequence__{key}"
                        self._key_queue.put(cleanup_marker, priority='normal', block=False)
                except Full:
                    LOG_ERROR("[输入队列] 普通队列已满，序列按键被丢弃。")
                    break
            # 将整个序列字符串加入去重集合
            if self._should_deduplicate_key(key):
                self._queued_keys_set.add(key)
        else:
            # 原有的单个按键逻辑
            try:
                self._key_queue.put(key, priority='normal', block=False)
                if self._should_deduplicate_key(key):
                    self._queued_keys_set.add(key)
            except Full:
                LOG_ERROR("[输入队列] 普通队列已满，技能被丢弃。")

    def execute_utility(self, key: str):
        """执行辅助功能按键 - 低优先级 - 支持序列"""
        if not key or self._check_priority_mode_block():
            return
        
        # 🎯 整体序列去重检查：序列作为一个整体进行去重判断
        if self._should_deduplicate_key(key) and key in self._queued_keys_set:
            return  # 整个序列已经在队列中，跳过
        
        # 🎯 支持逗号分隔的按键序列
        if ',' in key:
            key_sequence = [k.strip() for k in key.split(',') if k.strip()]
            for i, individual_key in enumerate(key_sequence):
                try:
                    self._key_queue.put(individual_key, priority='low', block=False)
                    # 在最后一个元素后添加清理标记
                    if i == len(key_sequence) - 1 and self._should_deduplicate_key(key):
                        cleanup_marker = f"__cleanup_sequence__{key}"
                        self._key_queue.put(cleanup_marker, priority='low', block=False)
                except Full:
                    LOG_ERROR("[输入队列] 低优先级队列已满，序列按键被丢弃。")
                    break
            # 将整个序列字符串加入去重集合
            if self._should_deduplicate_key(key):
                self._queued_keys_set.add(key)
        else:
            # 原有的单个按键逻辑
            try:
                self._key_queue.put(key, priority='low', block=False)
                if self._should_deduplicate_key(key):
                    self._queued_keys_set.add(key)
            except Full:
                LOG_ERROR("[输入队列] 低优先级队列已满，辅助功能被丢弃。")

    def get_queue_length(self) -> int:
        """获取当前队列长度"""
        return self._key_queue.qsize()

    def get_queue_stats(self) -> dict:
        """获取简单的队列状态"""
        return {"total": self._key_queue.qsize()}

    def clear_queue(self):
        """安全地清空按键队列和跟踪集合"""
        self._key_queue.clear()
        self._queued_keys_set.clear()
        LOG_INFO("[输入处理器] 按键队列已清空")

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
                
                # 🎯 处理序列清理标记
                if key_lower.startswith("__cleanup_sequence__"):
                    sequence_key = key_to_execute[len("__cleanup_sequence__"):]
                    self._queued_keys_set.discard(sequence_key)
                    continue
                
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
                    # X键按下时，所有技能键都改成F键（交互键）
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

    def _execute_key(self, key_str: str):
        """根据按键类型执行具体输入操作 - 简化版本"""
        key_lower = key_str.lower()
        
        # 处理按键执行
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
        self, enabled: bool = True, ahk_class: Optional[str] = None, ahk_exe: Optional[str] = None
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
