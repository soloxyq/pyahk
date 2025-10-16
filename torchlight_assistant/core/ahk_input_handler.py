"""
AHK输入处理器
统一的输入接口，兼容原InputHandler的API
"""

import subprocess
import os
import time
from typing import Optional

from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
# AHKEventReceiver已移除，使用WM_COPYDATA通信
from torchlight_assistant.config.ahk_config import AHKConfig
from torchlight_assistant.core.signal_bridge import ahk_signal_bridge # 导入信号桥


class AHKInputHandler:
    """
    基于AHK的完整输入处理器
    """
    
    def __init__(self, event_bus=None, debug_display_manager=None):
        self.event_bus = event_bus
        self.debug_display_manager = debug_display_manager
        
        if not AHKConfig.validate():
            raise RuntimeError("AHK配置验证失败")
        
        self.ahk_window = AHKConfig.WINDOW_TITLE
        self.ahk_path = AHKConfig.AHK_PATH
        self.server_script = AHKConfig.SERVER_SCRIPT
        
        self.command_sender: Optional[AHKCommandSender] = None
        # event_receiver已移除，使用WM_COPYDATA通信
        self.ahk_process: Optional[subprocess.Popen] = None
        
        self.dry_run_mode = False
        
        self._init_ahk_system()
        
        print("[AHK输入] 初始化完成")
    
    def _init_ahk_system(self):
        """初始化AHK系统"""
        if not self._start_ahk_server():
            raise RuntimeError("无法启动AHK服务器")
        
        try:
            self.command_sender = AHKCommandSender(self.ahk_window)
        except ConnectionError as e:
            raise RuntimeError(f"无法连接到AHK服务器: {e}")
        
        # 设置目标窗口
        if AHKConfig.WINDOW_EXE:
            target_str = f"ahk_exe {AHKConfig.WINDOW_EXE}"
            self.command_sender.set_target_window(target_str)

        # 连接AHK事件信号（通过WM_COPYDATA接收）
        ahk_signal_bridge.ahk_event.connect(self._on_ahk_event)
        
        self._register_priority_hooks()

    def _on_ahk_event(self, event: str):
        """这个方法现在总是在主GUI线程中被调用"""
        if not self.event_bus:
            return
        
        parts = event.split(':', 1)
        event_type = parts[0]
        data = parts[1] if len(parts) > 1 else ""
        
        self.event_bus.publish(event_type, key=data)

    def _start_ahk_server(self) -> bool:
        """启动AHK服务器"""
        if not os.path.exists(self.server_script):
            print(f"[AHK输入] 脚本不存在: {self.server_script}")
            return False
        
        if not os.path.exists(self.ahk_path):
            print(f"[AHK输入] AHK不存在: {self.ahk_path}")
            return False
        
        try:
            self.ahk_process = subprocess.Popen(
                [self.ahk_path, self.server_script],
                cwd=os.getcwd(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            print(f"[AHK输入] AHK服务器已启动 (PID: {self.ahk_process.pid})")
            
            time.sleep(AHKConfig.AHK_STARTUP_WAIT)
            
            return True
            
        except Exception as e:
            print(f"[AHK输入] 启动AHK失败: {e}")
            return False
    
    def _register_priority_hooks(self):
        """注册优先级按键Hook（使用AHK按键名称）"""
        priority_keys = AHKConfig.PRIORITY_KEYS
        
        print(f"[AHK输入] 开始注册 {len(priority_keys)} 个优先级Hook...")
        
        for key in priority_keys:
            try:
                result = self.command_sender.register_hook(key, "intercept")
                if result:
                    print(f"[AHK输入] [OK] 优先级Hook注册成功: {key}")
                else:
                    print(f"[AHK输入] [FAIL] 优先级Hook注册失败: {key} (AHK窗口未找到)")
            except Exception as e:
                print(f"[AHK输入] [ERROR] 优先级Hook注册异常 ({key}): {e}")
        
        system_hotkeys = AHKConfig.SYSTEM_HOTKEYS
        
        print(f"[AHK输入] 开始注册 {len(system_hotkeys)} 个系统热键Hook...")
        
        for key in system_hotkeys:
            try:
                result = self.command_sender.register_hook(key, "intercept")
                if result:
                    print(f"[AHK输入] [OK] 系统热键Hook注册成功: {key}")
                else:
                    print(f"[AHK输入] [FAIL] 系统热键Hook注册失败: {key} (AHK窗口未找到)")
            except Exception as e:
                print(f"[AHK输入] [ERROR] 系统热键Hook注册异常 ({key}): {e}")
    
    def send_key(self, key_str: str) -> bool:
        """
        发送按键
        """
        print(f"[AHK输入][DEBUG] send_key called with: {key_str}")
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Key:{key_str}")
                except Exception:
                    pass
            return True
        
        if "," in key_str:
            return self.command_sender.send_sequence(key_str, priority=2)
        else:
            return self.command_sender.send_key(key_str, priority=2)
    
    def activate_target_window(self):
        """请求AHK激活目标窗口"""
        print(f"[AHK输入] 正在请求AHK激活窗口...")
        return self.command_sender.activate_window()

    def set_target_window(self, target: str):
        """设置AHK的目标窗口"""
        print(f"[AHK输入] 正在设置AHK目标窗口: {target}")
        return self.command_sender.set_target_window(target)

    def click_mouse(self, button: str = "left", hold_time: Optional[float] = None) -> bool:
        """
        点击鼠标
        """
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Mouse:{button}")
                except Exception:
                    pass
            return True
        
        return self.command_sender.send_mouse_click(button, priority=2)
    
    def execute_skill_normal(self, key: str):
        if key:
            self.command_sender.send_normal(key)
    
    def execute_skill_high(self, key: str):
        if key:
            self.command_sender.send_high_priority(key)
    
    def execute_utility(self, key: str):
        if key:
            self.command_sender.send_low_priority(key)
    
    def execute_hp_potion(self, key: str):
        if key:
            self.command_sender.send_emergency(key)
    
    def execute_mp_potion(self, key: str):
        if key:
            self.command_sender.send_emergency(key)
    
    def clear_queue(self):
        self.command_sender.clear_queue(-1)
    
    def get_queue_length(self) -> int:
        return 0
    
    def get_queue_stats(self) -> dict:
        return {"wm_copydata_mode": True}
    
    def register_hook(self, key: str, mode: str = "intercept"):
        return self.command_sender.register_hook(key, mode)
    
    def unregister_hook(self, key: str):
        return self.command_sender.unregister_hook(key)
    
    def pause_queue(self):
        return self.command_sender.pause()
    
    def resume_queue(self):
        return self.command_sender.resume()
    
    def start(self):
        pass
    
    def cleanup(self):
        self.stop()
    
    def stop(self):
        print("[AHK输入] 正在停止...")
        
        # 事件接收现在通过主窗口的WM_COPYDATA处理
        
        if self.ahk_process:
            try:
                self.ahk_process.terminate()
                self.ahk_process.wait(timeout=3)
                print("[AHK输入] AHK进程已终止")
            except Exception as e:
                print(f"[AHK输入] 终止AHK进程失败: {e}")
                try:
                    self.ahk_process.kill()
                except Exception:
                    pass
        
        print("[AHK输入] 已停止")
    
    def set_dry_run_mode(self, enabled: bool):
        self.dry_run_mode = enabled
        print(f"[AHK输入] 干跑模式已 {'开启' if enabled else '关闭'}")
    
    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
