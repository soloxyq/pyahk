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

    def _on_ahk_event(self, event: str):
        """这个方法现在总是在主GUI线程中被调用"""
        if not self.event_bus:
            return
        
        parts = event.split(':', 1)
        event_type = parts[0]
        data = parts[1] if len(parts) > 1 else ""
        
        # 🎯 特殊处理special_key_pause事件
        if event_type == "special_key_pause":
            self.event_bus.publish(event_type, action=data)
        else:
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
    
    def _register_f8_hook(self):
        """注册F8主控键（程序启动时立即注册，永远不变）"""
        try:
            result = self.command_sender.register_hook("F8", "intercept")
            if result:
                print("[AHK输入] [OK] F8主控键注册成功")
            else:
                print("[AHK输入] [FAIL] F8主控键注册失败 (AHK窗口未找到)")
        except Exception as e:
            print(f"[AHK输入] [ERROR] F8主控键注册异常: {e}")

    def register_all_hooks_on_f8_ready(self, special_keys=None, managed_keys=None, other_hooks=None):
        """
        用户按F8准备时注册所有其他按键
        
        Args:
            special_keys: 特殊按键列表 (special模式) - 如 ["space"]
            managed_keys: 管理按键字典 (priority模式) - 如 {"e": {"target": "+", "delay": 500}}
            other_hooks: 其他Hook配置 - 如 {"x": "intercept", "a": "monitor", "RButton": "intercept"}
        """
        special_keys = special_keys or []
        managed_keys = managed_keys or {}
        other_hooks = other_hooks or {}
        
        print("[AHK输入] F8准备 - 开始注册所有业务按键...")
        
        # 1. 先注册其他系统热键 (z, F7, F9)
        system_keys = ["z", "F7", "F9"]  # F8已经在启动时注册了
        print(f"[AHK输入] 注册 {len(system_keys)} 个系统热键...")
        for key in system_keys:
            try:
                result = self.command_sender.register_hook(key, "intercept")
                if result:
                    print(f"[AHK输入] [OK] 系统热键注册成功: {key}")
                else:
                    print(f"[AHK输入] [FAIL] 系统热键注册失败: {key}")
            except Exception as e:
                print(f"[AHK输入] [ERROR] 系统热键注册异常 ({key}): {e}")
        
        # 2. 注册特殊按键 (special模式)
        if special_keys:
            print(f"[AHK输入] 注册 {len(special_keys)} 个特殊按键...")
            for key in special_keys:
                try:
                    result = self.command_sender.register_hook(key, "special")
                    if result:
                        print(f"[AHK输入] [OK] 特殊按键注册成功: {key}")
                    else:
                        print(f"[AHK输入] [FAIL] 特殊按键注册失败: {key}")
                except Exception as e:
                    print(f"[AHK输入] [ERROR] 特殊按键注册异常 ({key}): {e}")
        
        # 3. 注册管理按键 (priority模式)
        if managed_keys:
            print(f"[AHK输入] 注册 {len(managed_keys)} 个管理按键...")
            for key, config in managed_keys.items():
                try:
                    result = self.command_sender.register_hook(key, "priority")
                    if result:
                        target = config.get("target", key) if isinstance(config, dict) else key
                        delay = config.get("delay", 0) if isinstance(config, dict) else 0
                        
                        # 发送管理按键配置到AHK
                        config_result = self.command_sender.set_managed_key_config(key, target, delay)
                        if config_result:
                            print(f"[AHK输入] [OK] 管理按键注册成功: {key} -> {target} (延迟: {delay}ms)")
                        else:
                            print(f"[AHK输入] [FAIL] 管理按键配置失败: {key}")
                    else:
                        print(f"[AHK输入] [FAIL] 管理按键注册失败: {key}")
                except Exception as e:
                    print(f"[AHK输入] [ERROR] 管理按键注册异常 ({key}): {e}")
        
        # 4. 注册其他业务按键
        if other_hooks:
            print(f"[AHK输入] 注册 {len(other_hooks)} 个其他业务按键...")
            for key, mode in other_hooks.items():
                try:
                    result = self.command_sender.register_hook(key, mode)
                    if result:
                        print(f"[AHK输入] [OK] 业务按键注册成功: {key} ({mode}模式)")
                    else:
                        print(f"[AHK输入] [FAIL] 业务按键注册失败: {key}")
                except Exception as e:
                    print(f"[AHK输入] [ERROR] 业务按键注册异常 ({key}): {e}")
        
        print("[AHK输入] F8准备 - 所有业务按键注册完成")
        
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
    
    def set_force_move_state(self, active: bool) -> bool:
        """设置强制移动状态"""
        return self.command_sender.set_force_move_state(active)
    
    def set_force_move_key(self, key: str) -> bool:
        """设置强制移动键"""
        return self.command_sender.set_force_move_key(key)
    
    def set_force_move_replacement_key(self, key: str) -> bool:
        """设置强制移动替换键"""
        return self.command_sender.set_force_move_replacement_key(key)
    
    def clear_all_configurable_hooks(self) -> bool:
        """清空所有可配置的Hook（保留F8根热键）"""
        return self.command_sender.clear_all_configurable_hooks()
    
    def set_python_window_state(self, state: str) -> bool:
        """设置Python窗口状态
        
        Args:
            state: "main" 或 "osd"
        """
        return self.command_sender.set_python_window_state(state)
    
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
