"""AHK 子进程启动配置。

运行时输入、目标窗口和队列策略分别来自用户配置与 AHK 服务端常量；这里不再
保留看似可调但实际无人读取的重复配置，避免文档和运行行为分叉。
"""

import os
from torchlight_assistant.utils.debug_log import LOG_INFO


class AHKConfig:
    """AHK配置类"""
    
    # AHK路径配置
    AHK_PATH = r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"
    SERVER_SCRIPT = "hold_server_extended.ahk"
    WINDOW_TITLE = "HoldServer_Window_UniqueName_12345"
    # 启动配置
    AHK_STARTUP_WAIT = 1.5  # 等待AHK启动的时间(秒)
    
    @classmethod
    def validate(cls) -> bool:
        """验证配置是否有效"""
        errors = []
        
        # 检查AHK路径
        if not os.path.exists(cls.AHK_PATH):
            errors.append(f"AHK不存在: {cls.AHK_PATH}")
        
        # 检查脚本路径
        if not os.path.exists(cls.SERVER_SCRIPT):
            errors.append(f"AHK脚本不存在: {cls.SERVER_SCRIPT}")
        
        if errors:
            for error in errors:
                LOG_INFO(f"[配置错误] {error}")
            return False
        
        return True
    
    @classmethod
    def print_config(cls):
        """打印配置信息"""
        LOG_INFO("\n" + "="*60)
        LOG_INFO("AHK输入系统配置")
        LOG_INFO("="*60)
        LOG_INFO(f"AHK路径: {cls.AHK_PATH}")
        LOG_INFO(f"服务器脚本: {cls.SERVER_SCRIPT}")
        LOG_INFO(f"窗口标题: {cls.WINDOW_TITLE}")
        LOG_INFO("通信方式: WM_COPYDATA")
        LOG_INFO("="*60 + "\n")
