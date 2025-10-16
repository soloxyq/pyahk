"""
AHK输入系统配置
集中管理所有AHK相关配置
"""

import os


class AHKConfig:
    """AHK配置类"""
    
    # AHK路径配置
    AHK_PATH = r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"
    SERVER_SCRIPT = "hold_server_extended.ahk"
    WINDOW_TITLE = "HoldServer_Window_UniqueName_12345"
    WINDOW_EXE = "notepad++.exe" # 默认目标窗口
    
    # WM_COPYDATA通信配置（已移除文件通信）
    
    # 队列配置
    QUEUE_PROCESS_INTERVAL = 10  # AHK队列处理间隔(ms)
    
    # 优先级配置
    PRIORITY_EMERGENCY = 0  # 紧急 (药剂)
    PRIORITY_HIGH = 1       # 高 (优先级按键)
    PRIORITY_NORMAL = 2     # 普通 (技能)
    PRIORITY_LOW = 3        # 低 (辅助)
    
    # Hook配置（使用AHK按键名称）
    # 参考: https://www.autohotkey.com/docs/v2/KeyList.htm
    PRIORITY_KEYS = [
        "Space",        # 闪避
        "RButton",      # 右键攻击
        "e",            # 交互键
    ]
    
    # 系统热键（会被拦截但不暂停队列）
    SYSTEM_HOTKEYS = [
        "F8",           # 主控键
        "F7",           # 洗练键
        "F9",           # 寻路键
        "z",            # 执行/暂停键
    ]
    
    # 前置延迟配置
    PRIORITY_KEY_DELAY = 50  # 优先级按键前置延迟(ms)
    
    # 启动配置
    AUTO_START_AHK = True
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
                print(f"[配置错误] {error}")
            return False
        
        return True
    
    @classmethod
    def print_config(cls):
        """打印配置信息"""
        print("\n" + "="*60)
        print("AHK输入系统配置")
        print("="*60)
        print(f"AHK路径: {cls.AHK_PATH}")
        print(f"服务器脚本: {cls.SERVER_SCRIPT}")
        print(f"窗口标题: {cls.WINDOW_TITLE}")
        print("通信方式: WM_COPYDATA")
        print(f"队列处理间隔: {cls.QUEUE_PROCESS_INTERVAL}ms")
        print(f"优先级按键: {', '.join(cls.PRIORITY_KEYS)}")
        print(f"前置延迟: {cls.PRIORITY_KEY_DELAY}ms")
        print("="*60 + "\n")
