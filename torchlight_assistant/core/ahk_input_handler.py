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
from torchlight_assistant.utils.debug_log import LOG_INFO, LOG, LOG_ERROR


class AHKInputHandler:
    """
    基于AHK的完整输入处理器
    """

    # 永久根热键(F8 主控 / F7 洗练 / F9 寻路) — 业务配置侧禁止注册,避免覆盖 STOPPED 时的状态机 handler
    RESERVED_ROOT_KEYS = frozenset({"f8", "f7", "f9"})

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
        # 当特殊按键激活时，丢弃所有非紧急（HP/MP以外）的入队请求
        self._drop_non_emergency = False
        
        self._init_ahk_system()
        
        LOG_INFO("[AHK输入] 初始化完成")
    
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
            LOG_INFO(f"[AHK输入] 脚本不存在: {self.server_script}")
            return False
        
        if not os.path.exists(self.ahk_path):
            LOG_INFO(f"[AHK输入] AHK不存在: {self.ahk_path}")
            return False
        
        try:
            self.ahk_process = subprocess.Popen(
                [self.ahk_path, self.server_script],
                cwd=os.getcwd(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            LOG_INFO(f"[AHK输入] AHK服务器已启动 (PID: {self.ahk_process.pid})")
            
            time.sleep(AHKConfig.AHK_STARTUP_WAIT)
            
            return True
            
        except Exception as e:
            LOG_INFO(f"[AHK输入] 启动AHK失败: {e}")
            return False
    
    def send_key(self, key_str: str) -> bool:
        """
        发送按键
        """
        LOG(f"[AHK输入][DEBUG] send_key called with: {key_str}")
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Key:{key_str}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True
        
        # 特殊按键激活时，丢弃所有非紧急入队（send_key 视为非紧急）
        if self._drop_non_emergency:
            return False
        
        if "," in key_str:
            return self.command_sender.send_sequence(key_str, priority=2)
        else:
            return self.command_sender.send_key(key_str, priority=2)
    
    def activate_target_window(self):
        """请求AHK激活目标窗口"""
        LOG_INFO(f"[AHK输入] 正在请求AHK激活窗口...")
        return self.command_sender.activate_window()

    def set_target_window(self, target: str):
        """设置AHK的目标窗口"""
        LOG_INFO(f"[AHK输入] 正在设置AHK目标窗口: {target}")
        return self.command_sender.set_target_window(target)

    def click_mouse(self, button: str = "left", hold_time: Optional[float] = None) -> bool:
        """
        点击鼠标
        """
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Mouse:{button}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True

        # 特殊按键激活时，丢弃非紧急入队（鼠标点击视为非紧急）
        if self._drop_non_emergency:
            return False

        return self.command_sender.send_mouse_click(button, priority=2)

    def click_mouse_at(self, x: int, y: int, hold_time: Optional[float] = None) -> bool:
        """点击屏幕指定坐标

        ⚠️ 暂未实现 - 当前 AHK 命令协议中没有定义带坐标的鼠标点击命令。
        洗练(SimpleAffixRerollManager)和寻路(PathfindingManager)调用本方法,
        在新增 AHK 命令支持前会返回 False 并记录错误,而不会抛 AttributeError。

        TODO: 新增 CMD_MOUSE_CLICK_AT 命令,AHK 端用 `Click x, y` 实现。
        """
        LOG_ERROR(
            f"[AHK输入] click_mouse_at 暂未实现 (x={x}, y={y}, hold_time={hold_time})。"
            f"洗练/寻路功能需要新增 AHK 命令才能正常工作。"
        )
        return False
    
    def execute_skill_normal(self, key: str):
        # 🔧 BUG修复: 配置中 Key 字段允许序列(如 "delay50,1,delay100,2"),
        # 之前直接 send_normal 会把整串当作单个按键名 press: 出去导致无效。
        # 现在检测逗号自动走 send_sequence(AHK 端在 EnqueueAction 入口展开为原子动作)。
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self.command_sender.send_sequence(key, priority=2)
        else:
            self.command_sender.send_normal(key)

    def execute_skill_high(self, key: str):
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self.command_sender.send_sequence(key, priority=1)
        else:
            self.command_sender.send_high_priority(key)

    def execute_utility(self, key: str):
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self.command_sender.send_sequence(key, priority=3)
        else:
            self.command_sender.send_low_priority(key)
    
    def execute_hp_potion(self, key: str):
        if key:
            self.command_sender.send_emergency(key)
    
    def execute_mp_potion(self, key: str):
        if key:
            self.command_sender.send_emergency(key)
    
    def clear_queue(self):
        """清空所有队列(含 emergency)。用于 PAUSED 状态完全停下。"""
        self.command_sender.clear_queue(-1)

    def clear_non_emergency_queue(self):
        """只清非紧急队列,保留 emergency。用于管理按键期间保留 HP/MP 救命动作。"""
        self.command_sender.clear_queue(-2)

    def get_queue_stats(self) -> dict:
        return {"wm_copydata_mode": True}
    
    def register_root_hook(self, key: str):
        """注册永久根热键(F8/F7/F9 专用,intercept 模式)。

        绕过两层 register_hook 的保留键检查,仅供 MacroEngine._setup_primary_hotkey 调用。
        """
        return self.command_sender.register_root_hook(key)

    def register_hook(self, key: str, mode: str = "intercept"):
        """
        注册业务热键 Hook

        Args:
            key: 按键名（使用AHK标准名称，如 "RButton", "space"）
            mode: Hook模式

        Returns:
            注册是否成功;若 key 是保留根热键(F8/F7/F9),返回 False
        """
        # 🔧 保留键防御:F8/F7/F9 是永久根热键,业务侧禁止注册
        # 即使 AHK 端 RegisterHook 不会把它们记入 RegisteredHooks,Hotkey 绑定仍会被覆盖,
        # 导致 STOPPED 时按 F8/F7/F9 走错 handler。永久注册请用 register_root_hook。
        if key and key.lower() in self.RESERVED_ROOT_KEYS:
            LOG_ERROR(
                f"[register_hook] 拒绝注册保留根热键 '{key}' (mode={mode})。"
                f"F8/F7/F9 是永久根热键,业务配置不可覆盖。"
            )
            return False
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

    def set_force_move_passthrough_keys(self, keys) -> bool:
        """设置强制移动白名单(位移技能,如 RButton 闪现)"""
        return self.command_sender.set_force_move_passthrough_keys(keys)

    def clear_all_configurable_hooks(self) -> bool:
        """清空所有可配置的Hook（保留 F8/F7/F9 永久根热键）"""
        return self.command_sender.clear_all_configurable_hooks()
    
    def set_python_window_state(self, state: str) -> bool:
        """设置Python窗口状态
        
        Args:
            state: "main" 或 "osd"
        """
        return self.command_sender.set_python_window_state(state)
    
    def set_drop_non_emergency(self, enabled: bool):
        """在特殊按键激活时启用，丢弃所有非紧急入队"""
        self._drop_non_emergency = enabled
    
    def start(self):
        pass
    
    def cleanup(self):
        self.stop()
    
    def stop(self):
        if not self.ahk_process:
            return

        LOG_INFO("[AHK输入] 正在停止...")

        # 事件接收由 ahk_event_filter.AHKEventFilter 全局过滤器处理,无需在此清理

        process = self.ahk_process
        self.ahk_process = None

        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
                LOG_INFO("[AHK输入] AHK进程已终止")
            except Exception as e:
                LOG_INFO(f"[AHK输入] 终止AHK进程失败: {e}")
                try:
                    process.kill()
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 强制终止AHK进程失败: {e}")
        else:
            LOG_INFO("[AHK输入] AHK进程已退出")
        
        LOG_INFO("[AHK输入] 已停止")
    
    def set_dry_run_mode(self, enabled: bool):
        self.dry_run_mode = enabled
        LOG_INFO(f"[AHK输入] 干跑模式已 {'\u5f00\u542f' if enabled else '\u5173\u95ed'}")
    
    def __del__(self):
        try:
            self.stop()
        except Exception as e:
            LOG_INFO(f"[AHK输入] 清理资源失败: {e}")
