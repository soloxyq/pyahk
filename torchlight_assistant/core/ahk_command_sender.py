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
    """
    
    def __init__(self, window_title: str = "HoldServer_Window_UniqueName_12345"):
        self.window_title = window_title
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
        cmd = f"enqueue:{priority}:{action}"
        print(f"[AHK命令发送器][DEBUG] Enqueueing command: {cmd}") # 添加日志
        return send_ahk_cmd(self.window_title, cmd)
    
    def send_key(self, key: str, priority: int = 2) -> bool:
        """
        发送按键
        
        Args:
            key: 按键名称 (如 "1", "q", "space")
            priority: 优先级
        """
        print(f"[AHK命令发送器][DEBUG] Sending key: {key} with priority {priority}") # 添加日志
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
        return send_ahk_cmd(self.window_title, "pause")
    
    def resume(self) -> bool:
        """恢复队列处理"""
        return send_ahk_cmd(self.window_title, "resume")
    
    def clear_queue(self, priority: int = -1) -> bool:
        """
        清空队列
        
        Args:
            priority: 要清空的队列 (-1=全部, 0-3=指定队列)
        """
        return send_ahk_cmd(self.window_title, f"clear_queue:{priority}")
    
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
        return send_ahk_cmd(self.window_title, f"hook_register:{key}:{mode}")
    
    def unregister_hook(self, key: str) -> bool:
        """
        取消Hook
        
        Args:
            key: 按键名称
        """
        return send_ahk_cmd(self.window_title, f"hook_unregister:{key}")
    
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
