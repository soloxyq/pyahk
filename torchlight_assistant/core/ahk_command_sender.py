"""
AHK命令发送器
负责将Python的决策转换为AHK命令并发送
"""

from hold_client import send_ahk_cmd
from torchlight_assistant.config.ahk_commands import (
    CMD_PING, CMD_SET_TARGET, CMD_ACTIVATE, CMD_ENQUEUE,
    CMD_CLEAR_QUEUE, CMD_PAUSE, CMD_RESUME,
    CMD_HOOK_REGISTER, CMD_HOOK_UNREGISTER,
    get_command_name
)


class AHKCommandSender:
    """
    AHK命令发送器
    
    职责:
    - 将动作加入AHK队列
    - 管理Hook注册
    - 控制队列暂停/恢复
    - 处理原地模式的shift修饰符
    """
    
    def __init__(self, window_title: str = "HoldServer_Window_UniqueName_12345"):
        self.window_title = window_title
        self._stationary_mode_active = False
        self._stationary_mode_type = "shift_modifier"
        self._check_connection()
    
    def _check_connection(self):
        """检查AHK连接"""
        if not send_ahk_cmd(self.window_title, CMD_PING):
            raise ConnectionError(
                "无法连接到AHK服务器！\n"
                "请确保 hold_server_extended.ahk 正在运行。\n"
                f"窗口标题: {self.window_title}"
            )
    
    # ========================================================================
    # 窗口管理
    # ========================================================================

    def set_target_window(self, target: str) -> bool:
        """设置AHK的目标窗口标识符 (例如 'ahk_exe notepad++.exe')"""
        return send_ahk_cmd(self.window_title, CMD_SET_TARGET, target)

    def activate_window(self) -> bool:
        """请求AHK激活当前设置的目标窗口"""
        return send_ahk_cmd(self.window_title, CMD_ACTIVATE)

    # ========================================================================
    # 原地模式管理
    # ========================================================================
    
    def set_stationary_mode(self, active: bool, mode_type: str = "shift_modifier"):
        """设置原地模式状态"""
        self._stationary_mode_active = active
        self._stationary_mode_type = mode_type
        
        # 发送命令到AHK
        from torchlight_assistant.config.ahk_commands import CMD_SET_STATIONARY
        param = f"{'true' if active else 'false'}:{mode_type}"
        return send_ahk_cmd(self.window_title, CMD_SET_STATIONARY, param)
    
    def set_force_move_key(self, key: str):
        """设置强制移动键"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_KEY
        return send_ahk_cmd(self.window_title, CMD_SET_FORCE_MOVE_KEY, key)
    
    def set_force_move_state(self, active: bool):
        """设置强制移动状态"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_STATE
        param = "true" if active else "false"
        return send_ahk_cmd(self.window_title, CMD_SET_FORCE_MOVE_STATE, param)
    
    def set_force_move_replacement_key(self, key: str):
        """设置强制移动替换键"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_REPLACEMENT_KEY
        return send_ahk_cmd(self.window_title, CMD_SET_FORCE_MOVE_REPLACEMENT_KEY, key)
    
    def clear_all_configurable_hooks(self) -> bool:
        """清空所有可配置的Hook（保留F8根热键）"""
        from torchlight_assistant.config.ahk_commands import CMD_CLEAR_HOOKS
        return send_ahk_cmd(self.window_title, CMD_CLEAR_HOOKS, "")
    
    def set_python_window_state(self, state: str) -> bool:
        """设置Python窗口状态
        
        Args:
            state: "main" 或 "osd"
        """
        from torchlight_assistant.config.ahk_commands import CMD_SET_PYTHON_WINDOW_STATE
        return send_ahk_cmd(self.window_title, CMD_SET_PYTHON_WINDOW_STATE, state)
    
    def batch_update_config(self, config_dict: dict) -> bool:
        """批量更新配置（Master方案学习）
        
        Args:
            config_dict: 配置字典，如 {"hp_key": "1", "mp_key": "2", "stationary_type": "shift_modifier"}
        """
        from torchlight_assistant.config.ahk_commands import CMD_BATCH_UPDATE_CONFIG
        
        try:
            # 构建参数字符串： "hp_key:1,mp_key:2,stationary_type:shift_modifier"
            config_items = []
            for key, value in config_dict.items():
                if value:  # 只发送非空值
                    config_items.append(f"{key}:{value}")
            
            if not config_items:
                return True  # 没有配置需要更新
            
            param = ",".join(config_items)
            result = send_ahk_cmd(self.window_title, CMD_BATCH_UPDATE_CONFIG, param)
            
            return result
        except Exception as e:
            print(f"【AHKCommandSender】 批量更新配置失败: {e}")
            import traceback
            print(f"【AHKCommandSender】 异常详情:\n{traceback.format_exc()}")
            return False
        
    def is_stationary_mode_active(self) -> bool:
        """检查原地模式是否激活"""
        return self._stationary_mode_active
    
    def set_managed_key_config(self, key: str, target: str, delay: int):
        """设置管理按键配置"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_MANAGED_KEY_CONFIG
        param = f"{key}:{target}:{delay}"
        return send_ahk_cmd(self.window_title, CMD_SET_MANAGED_KEY_CONFIG, param)

    # ========================================================================
    # 队列操作
    # ========================================================================
    
    def enqueue(self, action: str, priority: int = 2) -> bool:
        """
        将动作加入AHK队列
        
        Args:
            action: 动作字符串，格式: "type:data"
            priority: 优先级 (0=emergency, 1=high, 2=normal, 3=low)
        
        Returns:
            是否成功
        """
        param = f"{priority}:{action}"
        return send_ahk_cmd(self.window_title, CMD_ENQUEUE, param)
    
    def send_key(self, key: str, priority: int = 2) -> bool:
        """
        发送按键
        
        Args:
            key: 按键名称 (如 "1", "q", "space")
            priority: 优先级
        """
        return self.enqueue(f"press:{key}", priority)
    

    
    def send_sequence(self, sequence: str, priority: int = 2) -> bool:
        """
        发送按键序列
        
        Args:
            sequence: 序列字符串 (如 "delay50,q,delay100,w")
            priority: 优先级
        """
        return self.enqueue(f"sequence:{sequence}", priority)
    
    def send_mouse_click(self, button: str = "left", priority: int = 2) -> bool:
        """
        发送鼠标点击
        
        Args:
            button: 按钮 ("left", "right", "middle")
            priority: 优先级
        """
        return self.enqueue(f"mouse_click:{button}", priority)
    
    def hold_key(self, key: str, priority: int = 2) -> bool:
        """
        按住按键
        
        Args:
            key: 按键名称
            priority: 优先级
        """
        return self.enqueue(f"hold:{key}", priority)
    
    def release_key(self, key: str, priority: int = 2) -> bool:
        """
        释放按键
        
        Args:
            key: 按键名称
            priority: 优先级
        """
        return self.enqueue(f"release:{key}", priority)
    
    # ========================================================================
    # 队列控制
    # ========================================================================
    
    def pause(self) -> bool:
        """暂停队列处理"""
        return send_ahk_cmd(self.window_title, CMD_PAUSE)
    
    def resume(self) -> bool:
        """恢复队列处理"""
        return send_ahk_cmd(self.window_title, CMD_RESUME)
    
    def clear_queue(self, priority: int = -1) -> bool:
        """
        清空队列
        
        Args:
            priority: 要清空的队列 (-1=全部, 0-3=指定队列)
        """
        return send_ahk_cmd(self.window_title, CMD_CLEAR_QUEUE, str(priority))
    
    # ========================================================================
    # Hook管理
    # ========================================================================
    
    def register_hook(self, key: str, mode: str = "intercept") -> bool:
        """
        注册Hook
        
        Args:
            key: 按键名称
            mode: 模式 ("intercept"=拦截, "monitor"=监控, "block"=阻止)
        """
        param = f"{key}:{mode}"
        return send_ahk_cmd(self.window_title, CMD_HOOK_REGISTER, param)
    
    def unregister_hook(self, key: str) -> bool:
        """
        取消Hook
        
        Args:
            key: 按键名称
        """
        return send_ahk_cmd(self.window_title, CMD_HOOK_UNREGISTER, key)
    
    # ========================================================================
    # 统计信息
    # ========================================================================
    
    def get_stats(self) -> bool:
        """请求统计信息"""
        return send_ahk_cmd(self.window_title, "get_stats")
    
    # ========================================================================
    # 便捷方法
    # ========================================================================
    
    def send_emergency(self, key: str) -> bool:
        """发送紧急按键 (优先级0)"""
        return self.send_key(key, priority=0)
    
    def send_high_priority(self, key: str) -> bool:
        """发送高优先级按键 (优先级1)"""
        return self.send_key(key, priority=1)
    
    def send_normal(self, key: str) -> bool:
        """发送普通按键 (优先级2)"""
        return self.send_key(key, priority=2)
    
    def send_low_priority(self, key: str) -> bool:
        """发送低优先级按键 (优先级3)"""
        return self.send_key(key, priority=3)
