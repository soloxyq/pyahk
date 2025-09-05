#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""".
特殊游戏自动化脚本 - 独立实现
(Z键控制自动左键, 纯声音提示版)
"""

import os
import sys
import time
import threading
import cv2
import numpy as np
import ctypes
import ctypes.wintypes
import signal

# 添加项目路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 导入项目模块
from torchlight_assistant.utils.native_graphics_capture_manager import (
    NativeGraphicsCaptureManager,
    CaptureConfig
)
from torchlight_assistant.core.input_handler import InputHandler
from torchlight_assistant.utils.sound_manager import SoundManager
from pynput import keyboard

class SpecialGameAutomation:
    """特殊游戏自动化控制器"""
    
    def __init__(self):
        # 状态
        self.is_running = False
        self.autoclicking = False
        self.shutting_down = False
        self.shutdown_lock = threading.Lock()
        # 线程
        self.listener = None
        self.capture_thread = None
        self.autoclick_thread = None
        # 模块
        self.sound_manager = None
        self.capture_manager = None
        self.input_handler = None
        # 其他
        self.buff_image = None
        self.last_frame = None
        self.stop_event = threading.Event()
        self.frame_lock = threading.Lock()
        self.capture_region = {'x': 0, 'y': 0, 'width': 400, 'height': 100}
        self.capture_interval = 0.08
        self.autoclick_interval = 0.1
        self.buff_image_path = os.path.join(current_dir, "tuteng.png")
        
    def initialize(self) -> bool:
        try:
            print("[初始化] 开始初始化组件...")
            if not self._load_buff_image(): return False
            self.input_handler = InputHandler(hotkey_manager=None)
            self.sound_manager = SoundManager()
            self.sound_manager.enabled = True
            capture_config = CaptureConfig(
                capture_monitor=True, monitor_index=0,
                capture_interval_ms=int(self.capture_interval * 1000),
                enable_region=True, region_x=self.capture_region['x'],
                region_y=self.capture_region['y'], region_width=self.capture_region['width'],
                region_height=self.capture_region['height']
            )
            self.capture_manager = NativeGraphicsCaptureManager(capture_config)
            if not self.capture_manager.initialize(): return False
            print("[初始化] 所有组件初始化成功")
            return True
        except Exception as e:
            print(f"[错误] 初始化异常: {e}")
            return False
            
    def _load_buff_image(self) -> bool:
        try:
            if not os.path.exists(self.buff_image_path): return False
            self.buff_image = cv2.imread(self.buff_image_path, cv2.IMREAD_COLOR)
            if self.buff_image is None: return False
            return True
        except Exception: return False

    def _on_press(self, key):
        try:
            if key == keyboard.Key.f8: self._toggle_automation()
            elif key == keyboard.KeyCode.from_char('z'): self._toggle_autoclick()
        except AttributeError: pass

    def _toggle_automation(self):
        if self.is_running: self._stop_automation()
        else: self._start_automation()

    def _toggle_autoclick(self):
        if not self.is_running: return
        self.autoclicking = not self.autoclicking
        if self.autoclicking:
            if self.autoclick_thread is None or not self.autoclick_thread.is_alive():
                self.autoclick_thread = threading.Thread(target=self._autoclick_loop, daemon=True)
                self.autoclick_thread.start()
            self.sound_manager.play("resume") # Z键开启音效
        else:
            self.sound_manager.play("pause") # Z键关闭音效

    def _autoclick_loop(self):
        print("[自动点击] 循环已启动")
        while self.autoclicking:
            self._perform_left_click_logic()
            time.sleep(self.autoclick_interval)
        print("[自动点击] 循环已停止")

    def _perform_left_click_logic(self):
        try:
            self._press_key("lbutton")
            with self.frame_lock: current_frame = self.last_frame
            if current_frame is None: return
            buff_exists = self._detect_buff_in_frame(current_frame)
            if not buff_exists:
                self._press_key("q")
            else:
                self._press_key("c")
        except Exception as e:
            print(f"[错误] 左键逻辑异常: {e}")

    def _start_automation(self):
        try:
            if self.is_running: return
            print("[启动] 开始启动自动化...")
            if not self.capture_manager.start_capture(): return
            self.input_handler.start()
            self.stop_event.clear()
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()
            self.is_running = True
            self.sound_manager.play("hello") # F8开启音效
            print("[启动] 自动化启动成功！按F8停止")
        except Exception as e:
            print(f"[错误] 启动自动化异常: {e}")
            
    def _stop_automation(self):
        try:
            if not self.is_running: return
            print("[停止] 正在停止自动化...")
            self.sound_manager.play("goodbye") # F8关闭音效
            self.autoclicking = False
            time.sleep(0.3)
            self.is_running = False
            self.stop_event.set()
            if self.capture_thread and self.capture_thread.is_alive(): self.capture_thread.join(timeout=1.0)
            if self.autoclick_thread and self.autoclick_thread.is_alive(): self.autoclick_thread.join(timeout=1.0)
            if self.capture_manager: self.capture_manager.stop_capture()
            if self.input_handler: self.input_handler.cleanup()
            print("[停止] 自动化已停止")
        except Exception as e:
            print(f"[错误] 停止自动化异常: {e}")
            
    def _capture_loop(self):
        while not self.stop_event.is_set():
            try:
                frame_data = self.capture_manager.get_latest_frame()
                if frame_data is not None: 
                    with self.frame_lock: self.last_frame = frame_data
                time.sleep(self.capture_interval)
            except Exception: time.sleep(0.1)
        
    def _detect_buff_in_frame(self, frame_data) -> bool:
        try:
            if self.buff_image is None or frame_data is None: return False
            if not isinstance(frame_data, np.ndarray) or len(frame_data.shape) != 3: return False
            if frame_data.shape[2] == 4: frame_bgr = cv2.cvtColor(frame_data, cv2.COLOR_BGRA2BGR)
            elif frame_data.shape[2] == 3: frame_bgr = frame_data
            else: return False
            result = cv2.matchTemplate(frame_bgr, self.buff_image, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            return max_val >= 0.8
        except Exception: return False
            
    def _press_key(self, key: str):
        if self.input_handler: self.input_handler.execute_key(key)

    def signal_handler(self, sig, frame):
        with self.shutdown_lock:
            if self.shutting_down: return
            self.shutting_down = True
        print("\n[退出] 检测到Ctrl+C，正在清理资源...")
        self.cleanup()
        sys.exit(0)

    def start(self):
        signal.signal(signal.SIGINT, self.signal_handler)
        try:
            print("=" * 50)
            print("特殊游戏自动化脚本启动")
            print("操作说明: F8(总开关), Z(自动左键开关), Ctrl+C(退出)")
            print("=" * 50)
            if not self.initialize():
                print("[错误] 初始化失败，程序退出")
                return
            self.listener = keyboard.Listener(on_press=self._on_press)
            self.listener.start()
            self.listener.join()
        except KeyboardInterrupt:
            print("\n[退出] 监听到键盘中断...")
        finally:
            self.cleanup()
            
def main():
    try:
        is_admin = (os.getuid() == 0)
    except AttributeError:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
    if not is_admin:
        print("[错误] 请以管理员权限运行，否则无法监听热键。正在尝试提权...")
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        return

    automation = SpecialGameAutomation()
    automation.start()

if __name__ == "__main__":
    main()
