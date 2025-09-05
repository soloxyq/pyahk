"""Global hotkey management for Torchlight Assistant"""

import threading
import time
import ctypes
import ctypes.wintypes
from typing import Dict, Callable, Optional
from .debug_log import LOG, LOG_ERROR, LOG_INFO

# Windows API constants
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
HC_ACTION = 0

# Virtual key codes
VK_CODES = {
    # Function keys
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,

    # Alphabet keys
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46, "g": 0x47, "h": 0x48,
    "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F, "p": 0x50,
    "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58,
    "y": 0x59, "z": 0x5A,

    # Number keys (0-9)
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35, "6": 0x36, "7": 0x37,
    "8": 0x38, "9": 0x39,

    # Numpad keys
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64, "num5": 0x65,
    "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "separator": 0x6C, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,

    # Special keys
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11,
    "alt": 0x12, "pause": 0x13, "capslock": 0x14, "escape": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
    "down": 0x28, "select": 0x29, "print": 0x2A, "execute": 0x2B, "snapshot": 0x2C, "insert": 0x2D,
    "delete": 0x2E, "help": 0x2F, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D, "sleep": 0x5F,
    "numlock": 0x90, "scrolllock": 0x91,

    # Mouse buttons (for hook processing, not standard VK_CODEs for RegisterHotkey)
    "xbutton1": 0x05,  # 鼠标侧键1
    "xbutton2": 0x06,  # 鼠标侧键2
}

REVERSE_VK_CODES = {v: k for k, v in VK_CODES.items()}


class CtypesHotkeyManager:
    """Low-level keyboard hook manager using Windows API, supporting separate press/release events."""

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
    )

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", ctypes.wintypes.DWORD),
            ("scanCode", ctypes.wintypes.DWORD),
            ("flags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
        ]

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long),
            ("y", ctypes.c_long),
            ("mouseData", ctypes.wintypes.DWORD),
            ("flags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
        ]

    def __init__(self):
        self.hotkeys: Dict[int, Callable] = {}
        self.key_events: Dict[int, Dict[str, Optional[Callable]]] = {}
        self.suppress_keys: set = set()
        self.conditional_suppress_keys: set = set()
        self.suppress_condition_callback = None
        self.keyboard_hook = None
        self.mouse_hook = None
        self.is_active = False
        self._keyboard_hook_proc_instance = None
        self._mouse_hook_proc_instance = None
        self._message_loop_thread = None
        self._stop_event = threading.Event()
        self.pressed_keys = set()

    def register_hotkey(
        self, key_name: str, callback: Callable, suppress: str = "never"
    ):
        """Registers a simple hotkey that triggers on key press."""
        self.register_key_event(key_name, on_press=callback, suppress=suppress)

    def register_key_event(
        self,
        key_name: str,
        on_press: Optional[Callable] = None,
        on_release: Optional[Callable] = None,
        suppress: str = "never",
    ):
        """Registers callbacks for key press and/or release events."""
        key_lower = key_name.lower()
        vk_code = VK_CODES.get(key_lower)

        if vk_code is None:
            try:
                # 尝试使用 VkKeyScanW 获取虚拟键码
                vk_code_short = ctypes.windll.user32.VkKeyScanW(ord(key_name[0]))
                if vk_code_short == -1:  # -1 表示没有找到对应的虚拟键码
                    LOG_ERROR(f"Unsupported key: {key_name}")
                    raise ValueError(f"Unsupported key: {key_name}")
                
                # 核心修正：只需要低8位作为虚拟键码，高8位是状态信息
                vk_code = vk_code_short & 0xFF
                
                # 将动态获取的键添加到 VK_CODES 和 REVERSE_VK_CODES，以便后续查找
                VK_CODES[key_lower] = vk_code
                REVERSE_VK_CODES[vk_code] = key_lower
                LOG_INFO(f"Dynamically registered key '{key_name}' with VK_CODE {vk_code}")
            except Exception as e:
                LOG_ERROR(f"Failed to dynamically register key: {key_name}, error: {e}")
                raise ValueError(f"Unsupported key: {key_name}")

        self.key_events[vk_code] = {"on_press": on_press, "on_release": on_release}
        if on_press:
            self.hotkeys[vk_code] = on_press

        if suppress == "always":
            self.suppress_keys.add(vk_code)
        elif suppress == "conditional":
            self.conditional_suppress_keys.add(vk_code)

    def unregister_hotkey(self, key_name: str):
        """Unregisters a hotkey and its events."""
        key_lower = key_name.lower()
        if key_lower not in VK_CODES:
            return

        vk_code = VK_CODES[key_lower]
        if vk_code in self.hotkeys:
            del self.hotkeys[vk_code]
        if vk_code in self.key_events:
            del self.key_events[vk_code]

        self.suppress_keys.discard(vk_code)
        self.conditional_suppress_keys.discard(vk_code)

    def set_suppress_condition_callback(self, callback: Callable[[str], bool]):
        self.suppress_condition_callback = callback

    def _low_level_keyboard_proc(self, nCode, wParam, lParam):
        """The actual hook procedure, now handling separate press/release events."""
        try:
            if nCode >= 0:
                kb_struct = ctypes.cast(
                    lParam, ctypes.POINTER(self.KBDLLHOOKSTRUCT)
                ).contents
                vk_code = kb_struct.vkCode
                key_name = REVERSE_VK_CODES.get(vk_code, None)

                should_suppress = False
                if vk_code in self.suppress_keys:
                    should_suppress = True
                elif (
                    vk_code in self.conditional_suppress_keys
                    and self.suppress_condition_callback
                    and key_name
                ):
                    should_suppress = self.suppress_condition_callback(key_name)

                if wParam == WM_KEYDOWN or wParam == WM_SYSKEYDOWN:
                    if key_name and key_name not in self.pressed_keys:
                        self.pressed_keys.add(key_name)
                        if (
                            vk_code in self.key_events
                            and self.key_events[vk_code]["on_press"]
                        ):
                            self.key_events[vk_code]["on_press"]()

                elif wParam == WM_KEYUP or wParam == WM_SYSKEYUP:
                    if key_name and key_name in self.pressed_keys:
                        self.pressed_keys.remove(key_name)
                        if (
                            vk_code in self.key_events
                            and self.key_events[vk_code]["on_release"]
                        ):
                            self.key_events[vk_code]["on_release"]()

                if should_suppress:
                    return 1

            return ctypes.windll.user32.CallNextHookEx(
                self.keyboard_hook, nCode, wParam, lParam
            )
        except Exception as e:
            LOG_ERROR(f"Error in keyboard hook procedure: {e}")
            return ctypes.windll.user32.CallNextHookEx(
                self.keyboard_hook, nCode, wParam, lParam
            )

    def _low_level_mouse_proc(self, nCode, wParam, lParam):
        """鼠标钩子处理程序，专门处理鼠标侧键"""
        try:
            if nCode >= 0:
                mouse_struct = ctypes.cast(
                    lParam, ctypes.POINTER(self.MSLLHOOKSTRUCT)
                ).contents

                # 检查是否是鼠标侧键事件
                if wParam == WM_XBUTTONDOWN:
                    # 从mouseData中提取按键信息
                    mouse_data = mouse_struct.mouseData
                    # 高位字节包含按键信息
                    button = (mouse_data >> 16) & 0xFFFF

                    if button == 1:  # XButton1
                        vk_code = VK_CODES.get("xbutton1")
                        key_name = "xbutton1"
                    elif button == 2:  # XButton2
                        vk_code = VK_CODES.get("xbutton2")
                        key_name = "xbutton2"
                    else:
                        return ctypes.windll.user32.CallNextHookEx(
                            self.mouse_hook, nCode, wParam, lParam
                        )

                    should_suppress = False
                    if vk_code in self.suppress_keys:
                        should_suppress = True
                    elif (
                        vk_code in self.conditional_suppress_keys
                        and self.suppress_condition_callback
                        and key_name
                    ):
                        should_suppress = self.suppress_condition_callback(key_name)

                    if key_name and key_name not in self.pressed_keys:
                        self.pressed_keys.add(key_name)
                        if (
                            vk_code in self.key_events
                            and self.key_events[vk_code]["on_press"]
                        ):
                            self.key_events[vk_code]["on_press"]()

                    if should_suppress:
                        return 1

                elif wParam == WM_XBUTTONUP:
                    # 处理鼠标侧键释放事件
                    mouse_data = mouse_struct.mouseData
                    button = (mouse_data >> 16) & 0xFFFF

                    if button == 1:  # XButton1
                        vk_code = VK_CODES.get("xbutton1")
                        key_name = "xbutton1"
                    elif button == 2:  # XButton2
                        vk_code = VK_CODES.get("xbutton2")
                        key_name = "xbutton2"
                    else:
                        return ctypes.windll.user32.CallNextHookEx(
                            self.mouse_hook, nCode, wParam, lParam
                        )

                    if key_name and key_name in self.pressed_keys:
                        self.pressed_keys.remove(key_name)
                        if (
                            vk_code in self.key_events
                            and self.key_events[vk_code]["on_release"]
                        ):
                            self.key_events[vk_code]["on_release"]()

            return ctypes.windll.user32.CallNextHookEx(
                self.mouse_hook, nCode, wParam, lParam
            )
        except Exception as e:
            LOG_ERROR(f"Error in mouse hook procedure: {e}")
            return ctypes.windll.user32.CallNextHookEx(
                self.mouse_hook, nCode, wParam, lParam
            )

    def start_listening(self):
        if self.is_active:
            return True
        try:
            # 设置键盘钩子
            self._keyboard_hook_proc_instance = self.HOOKPROC(
                self._low_level_keyboard_proc
            )
            self.keyboard_hook = ctypes.windll.user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._keyboard_hook_proc_instance, 0, 0
            )
            if not self.keyboard_hook:
                raise ctypes.WinError(ctypes.windll.kernel32.GetLastError())

            # 设置鼠标钩子
            self._mouse_hook_proc_instance = self.HOOKPROC(self._low_level_mouse_proc)
            self.mouse_hook = ctypes.windll.user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._mouse_hook_proc_instance, 0, 0
            )
            if not self.mouse_hook:
                # 如果鼠标钩子失败，清理键盘钩子
                ctypes.windll.user32.UnhookWindowsHookEx(self.keyboard_hook)
                raise ctypes.WinError(ctypes.windll.kernel32.GetLastError())

            self._stop_event.clear()
            self._message_loop_thread = threading.Thread(
                target=self._message_loop, daemon=True
            )
            self._message_loop_thread.start()
            self.is_active = True
            return True
        except Exception as e:
            LOG_ERROR(f"Failed to start hotkey manager: {e}")
            if self.keyboard_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(self.keyboard_hook)
            if self.mouse_hook:
                ctypes.windll.user32.UnhookWindowsHookEx(self.mouse_hook)
            self.keyboard_hook = None
            self.mouse_hook = None
            self._keyboard_hook_proc_instance = None
            self._mouse_hook_proc_instance = None
            return False

    def _message_loop(self):
        """优化的消息循环，提高响应速度"""
        try:
            while not self._stop_event.is_set():
                msg = ctypes.wintypes.MSG()
                # 使用非阻塞模式检查消息，提高响应速度
                bRet = ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE
                if bRet != 0:
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    # 没有消息时稍微等待，避免占用过多CPU
                    time.sleep(0.001)  # 1ms
        except Exception as e:
            LOG_ERROR(f"Error in message loop: {e}")

    def stop_listening(self):
        if self.is_active:
            try:
                self._stop_event.set()
                if self.keyboard_hook:
                    ctypes.windll.user32.UnhookWindowsHookEx(self.keyboard_hook)
                    self.keyboard_hook = None
                if self.mouse_hook:
                    ctypes.windll.user32.UnhookWindowsHookEx(self.mouse_hook)
                    self.mouse_hook = None

                # 唤醒消息循环线程
                if self._message_loop_thread and self._message_loop_thread.is_alive():
                    thread_id = self._message_loop_thread.ident
                    if thread_id:
                        ctypes.windll.user32.PostThreadMessageW(
                            thread_id, 0x0012, 0, 0
                        )  # WM_QUIT
                    self._message_loop_thread.join(timeout=1.0)  # 缩短等待时间
                    if self._message_loop_thread.is_alive():
                        LOG_ERROR("警告: 热键监听线程未能正常结束")

                self.is_active = False
                self._keyboard_hook_proc_instance = None
                self._mouse_hook_proc_instance = None
            except Exception as e:
                LOG_ERROR(f"Error stopping hooks: {e}")

    def is_listening(self) -> bool:
        return self.is_active

    def get_registered_hotkeys(self) -> Dict[int, Callable]:
        return self.hotkeys.copy()

    def clear_all_hotkeys(self):
        self.hotkeys.clear()
        self.key_events.clear()
        self.suppress_keys.clear()
        self.conditional_suppress_keys.clear()

    def is_key_pressed(self, key_name: str) -> bool:
        return key_name.lower() in self.pressed_keys
