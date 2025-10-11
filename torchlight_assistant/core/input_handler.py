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
        self._managed_key_map = {}  # 映射：target_key -> source_key，用于去重清理
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
        # 注意：优先级按键监听将在配置加载后通过 _on_config_updated 启动

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
                    # 不清空队列，而是在执行时过滤普通技能，但保留HP/MP等紧急按键
                    pass  # 静默处理，不输出日志
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
            # 规范化目标按键名，兼容多种写法
            target_key = self._normalize_key_name(target_key)
            
            # 1. 去重检查：使用源按键作为唯一标识（delay + 按键作为整体）
            # 例如：e键映射到+，去重标识就是 "e"，而不是单独的 "+"
            if source_key in self._queued_keys_set:
                return
            
            # 2. 将源按键加入去重集合
            self._queued_keys_set.add(source_key)
            
            if source_key != target_key:
                LOG(f"[按键映射] {source_key} → {target_key} (延迟: {delay_ms}ms)")
            else:
                LOG(f"[管理按键] {source_key} 程序接管 (延迟: {delay_ms}ms)")
            
            # 3. 将指令序列推入队列
            if delay_ms > 0:
                self._key_queue.put(f"delay{delay_ms}", priority='emergency', block=False)
            self._key_queue.put(target_key, priority='emergency', block=False)
            
            # 4. 推入清理标记（序列执行完后清除源按键的去重标记）
            cleanup_marker = f"__cleanup_sequence__{source_key}"
            self._key_queue.put(cleanup_marker, priority='emergency', block=False)
            
        except Exception as e:
            LOG_ERROR(f"[管理按键] 处理失败: {e}")
            import traceback
            LOG_ERROR(f"[管理按键] 异常详情:\n{traceback.format_exc()}")

    def _pause_skill_scheduler(self):
        """暂停技能调度器以节省CPU资源"""
        try:
            event_bus.publish('scheduler_pause_requested', {
                'reason': 'priority_key_pressed',
                'active_keys': list(self._priority_keys_pressed)
            })
            LOG("[性能优化] 优先级按键激活 - 技能调度器已暂停")
        except Exception as e:
            LOG_ERROR(f"[性能优化] 暂停调度器失败: {e}")
    
    def _resume_skill_scheduler(self):
        """恢复技能调度器"""
        try:
            event_bus.publish('scheduler_resume_requested', {
                'reason': 'priority_key_released'
            })
            LOG("[性能优化] 优先级按键释放 - 技能调度器已恢复")
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
        priority_keys_config = global_config.get("priority_keys", {})
        if priority_keys_config:
            enabled = priority_keys_config.get("enabled", True)
            self.set_dodge_mode(enabled)
            self.set_priority_keys(priority_keys_config)
        
        self.key_press_duration = global_config.get("key_press_duration", 10) / 1000.0
        self.mouse_click_duration = (
            global_config.get("mouse_click_duration", 5) / 1000.0
        )
        
        # 更新原地模式配置
        stationary_config = global_config.get("stationary_mode_config", {})
        if stationary_config:
            mode_type = stationary_config.get("mode_type", "block_mouse")
            self._cached_stationary_mode_type = mode_type
            LOG_INFO(f"[输入处理器] 原地模式类型已更新: {mode_type}")
        
        # 更新窗口激活配置
        window_activation = global_config.get("window_activation", {})
        if window_activation:
            self.window_activation_config["enabled"] = window_activation.get("enabled", False)
            self.window_activation_config["ahk_class"] = window_activation.get("ahk_class", "")
            self.window_activation_config["ahk_exe"] = window_activation.get("ahk_exe", "")
            LOG_INFO(f"[输入处理器] 窗口激活配置已更新: enabled={self.window_activation_config['enabled']}, class={self.window_activation_config['ahk_class']}, exe={self.window_activation_config['ahk_exe']}")

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
        """队列处理器循环 - 优化版：提取方法，减少嵌套"""
        while not self._stop_event.is_set():
            try:
                key_to_execute = self._key_queue.get(timeout=0.1)
                self._queued_keys_set.discard(key_to_execute)
            except Empty:
                continue

            try:
                # 处理延迟指令（提前返回，减少嵌套）
                if self._handle_delay_command(key_to_execute):
                    continue
                
                # 处理清理标记（序列执行完毕后清除去重标识）
                if key_to_execute.startswith("__cleanup_sequence__"):
                    source_key = key_to_execute.replace("__cleanup_sequence__", "")
                    self._queued_keys_set.discard(source_key)
                    continue
                
                # Space等特殊监控按键按下时，过滤普通技能，但保留HP/MP等紧急按键
                if self.is_priority_mode_active():
                    # 检查是否是紧急按键（HP/MP等）
                    if not self._is_emergency_key(key_to_execute):
                        # 普通技能，丢弃
                        continue
                    # 紧急按键，继续执行
                
                # 根据缓存状态选择执行策略
                self._execute_with_current_mode(key_to_execute)
                
            except Exception as e:
                LOG_ERROR(f"[队列处理器] 处理按键 '{key_to_execute}' 时发生异常: {e}")

    def _is_emergency_key(self, key: str) -> bool:
        """判断是否是紧急按键（HP/MP药剂等生存技能）
        
        紧急按键的特征：
        1. 通过 execute_hp_potion 或 execute_mp_potion 入队（使用 emergency 优先级）
        2. 即使在优先级模式激活时也应该执行
        """
        # 这里可以根据实际情况扩展判断逻辑
        # 目前简单判断：由于HP/MP使用emergency优先级，
        # 而普通技能使用normal优先级，我们可以通过队列优先级来区分
        # 但由于这里已经从队列中取出，我们需要另一种方式
        
        # 暂时使用简单策略：所有按键都可能是紧急的
        # 更好的方式是在入队时打标记，或者维护一个紧急按键列表
        # 由于HP/MP使用单独的方法入队，我们可以检查配置
        
        # 获取资源配置中的HP/MP按键
        if hasattr(self, 'config_manager') and self.config_manager:
            global_config = self.config_manager.get_global_config()
            resource_config = global_config.get('resource_management', {})
            
            hp_key = resource_config.get('hp_config', {}).get('key', '')
            mp_key = resource_config.get('mp_config', {}).get('key', '')
            
            # 如果是HP或MP按键，认为是紧急按键
            if key.lower() in [hp_key.lower(), mp_key.lower()]:
                return True
        
        return False


    def _handle_delay_command(self, key: str) -> bool:
        """处理延迟指令，返回True表示已处理"""
        key_lower = key.lower()
        if not key_lower.startswith("delay"):
            return False
        
        try:
            delay_ms = int(key_lower[5:])
            # 等待指定时间，如果收到停止信号则提前返回
            self._stop_event.wait(delay_ms / 1000.0)
        except (ValueError, IndexError):
            LOG_ERROR(f"[按键处理] 无效的延迟格式: {key}")
        
        return True

    def _execute_with_current_mode(self, key: str):
        """根据当前模式执行按键"""
        if self._cached_force_move:
            # X键按下时，所有技能键都改成F键（交互键）
            self._execute_key("f")
        elif self._cached_stationary_mode:
            self._execute_stationary_mode(key)
        else:
            self._execute_key(key)

    def _execute_stationary_mode(self, key: str):
        """原地攻击模式的执行逻辑"""
        if self._cached_stationary_mode_type == "block_mouse":
            # 阻断鼠标模式：过滤鼠标按键
            key_lower = key.lower()
            if key_lower not in ["lbutton", "leftclick", "rbutton", "rightclick"]:
                self._execute_key(key)
        elif self._cached_stationary_mode_type == "shift_modifier":
            # Shift修饰模式：所有按键带Shift
            self._execute_key_with_shift(key)

    def _execute_key(self, key_str: str):
        """根据按键类型执行具体输入操作"""
        key_lower = key_str.lower()

        if key_lower in ["lbutton", "leftclick", "left_mouse"]:
            self.click_mouse("left")
        elif key_lower in ["rbutton", "rightclick", "right_mouse"]:
            self.click_mouse("right")
        elif key_lower in ["middle_mouse"]:
            self.click_mouse("middle")
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

    def execute_skill_normal(self, key: str):
        """执行普通技能按键 - 普通优先级"""
        if not key:
            return

        # 优先级模式检查：有优先级按键按下时技能不响应
        if self.is_priority_mode_active():
            return

        try:
            self._key_queue.put(key, priority='normal', block=False)
            self._queued_keys_set.add(key)
        except Full:
            if not self._queue_full_warned:
                LOG_ERROR(f"[输入队列] 普通队列已满 (大小: {self._key_queue.qsize()})，按键 '{key}' 被丢弃")
                self._queue_full_warned = True

    def execute_skill_high(self, key: str):
        """执行高优先级技能按键"""
        if not key:
            return

        # 优先级模式检查：有优先级按键按下时技能不响应
        if self.is_priority_mode_active():
            return

        try:
            self._key_queue.put(key, priority='high', block=False)
            self._queued_keys_set.add(key)
        except Full:
            LOG_ERROR("[输入队列] 高优先级队列已满，技能被丢弃。")

    def execute_utility(self, key: str):
        """执行辅助功能按键 - 低优先级"""
        if not key:
            return

        # 优先级模式检查：有优先级按键按下时辅助功能也不响应
        if self.is_priority_mode_active():
            return

        try:
            self._key_queue.put(key, priority='low', block=False)
            self._queued_keys_set.add(key)
        except Full:
            LOG_ERROR("[输入队列] 低优先级队列已满，辅助功能被丢弃。")

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

    def activate_target_window(self):
        """根据配置激活目标窗口"""
        LOG_INFO(f"[窗口激活] 开始激活窗口，配置: enabled={self.window_activation_config.get('enabled')}, class={self.window_activation_config.get('ahk_class')}, exe={self.window_activation_config.get('ahk_exe')}")
        
        if not self.window_activation_config["enabled"]:
            LOG_INFO("[窗口激活] 窗口激活功能未启用")
            return False
            
        if not WIN32_AVAILABLE:
            LOG_ERROR("[窗口激活] Win32 API不可用")
            return False

        ahk_class = self.window_activation_config.get("ahk_class", "").strip()
        ahk_exe = self.window_activation_config.get("ahk_exe", "").strip()

        # 检查是否有有效的配置参数
        if not ahk_class and not ahk_exe:
            LOG_ERROR("[窗口激活] 未配置ahk_class或ahk_exe参数")
            return False

        hwnd = None
        # 优先使用类名查找
        if ahk_class:
            LOG_INFO(f"[窗口激活] 尝试使用类名查找窗口: {ahk_class}")
            hwnd = win32gui.FindWindow(ahk_class, None)
            if hwnd:
                LOG_INFO(f"[窗口激活] 通过类名找到窗口句柄: {hwnd}")
            else:
                LOG_INFO(f"[窗口激活] 类名 '{ahk_class}' 未找到窗口")

        # 如果类名找不到，并且提供了进程名，则使用进程名查找
        if not hwnd and ahk_exe:
            LOG_INFO(f"[窗口激活] 尝试使用进程名查找窗口: {ahk_exe}")
            hwnd = WindowUtils.find_window_by_process_name(ahk_exe)
            if hwnd:
                LOG_INFO(f"[窗口激活] 通过进程名找到窗口句柄: {hwnd}")
            else:
                LOG_INFO(f"[窗口激活] 进程名 '{ahk_exe}' 未找到窗口")

        if hwnd:
            try:
                LOG_INFO(f"[窗口激活] 准备激活窗口句柄: {hwnd}")
                # 使用改进的WindowUtils.activate_window方法
                success = WindowUtils.activate_window(hwnd)
                if success:
                    LOG_INFO(f"[窗口激活] 窗口激活成功！句柄: {hwnd}")
                else:
                    LOG_ERROR(f"[窗口激活] 窗口激活失败，句柄: {hwnd}")
                return success
            except Exception as e:
                LOG_ERROR(f"[窗口激活] 激活异常: {e}")
                import traceback
                LOG_ERROR(f"[窗口激活] 异常详情:\n{traceback.format_exc()}")
                return False
        else:
            LOG_ERROR(f"[窗口激活] 未找到目标窗口 (class={ahk_class}, exe={ahk_exe})")
            return False

    def send_key(self, key_str: str) -> bool:
        """使用Pynput发送按键 - 高游戏兼容性"""
        if not key_str:
            return False

        # 干跑模式：只记录动作，不实际发送
        if self.dry_run_mode:
            try:
                if self.debug_display_manager:
                    self.debug_display_manager.add_action(f"Key:{key_str}")
            except Exception:
                pass
            return True

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
            elif len(key_str) == 1:
                # 支持所有单字符（字母、数字、符号如 +、-、= 等）
                key_obj = key_str  # 保持原始大小写，让pynput处理
            else:
                LOG_ERROR(f"[按键发送] 不支持的按键: {key_str}")
                return False

            # 发送按键事件 - 按下并释放
            self.keyboard.press(key_obj)
            time.sleep(self.key_press_duration)
            self.keyboard.release(key_obj)

            return True

        except Exception as e:
            LOG_ERROR(f"[按键发送] 发送按键 '{key_str}' 时出错: {e}")
            return False

    def click_mouse(self, button: str = "left", hold_time: Optional[float] = None) -> bool:
        """使用Pynput点击鼠标"""
        # 干跑模式：只记录动作，不实际发送
        if self.dry_run_mode:
            try:
                if self.debug_display_manager:
                    detail = f",{hold_time:.3f}s" if hold_time is not None else ""
                    self.debug_display_manager.add_action(f"Mouse:{button}{detail}")
            except Exception:
                pass
            return True

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

        # 干跑模式：只记录动作，不实际发送
        if self.dry_run_mode:
            try:
                if self.debug_display_manager:
                    self.debug_display_manager.add_action(f"Key:{modifier}+{key_str}")
            except Exception:
                pass
            return True

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
        # 干跑模式：只记录动作，不实际发送
        if self.dry_run_mode:
            try:
                if self.debug_display_manager:
                    detail = f",{hold_time:.3f}s" if hold_time is not None else ""
                    self.debug_display_manager.add_action(f"Mouse:{modifier}+{button}{detail}")
            except Exception:
                pass
            return True

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