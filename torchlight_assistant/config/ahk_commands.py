"""
AHK命令协议定义
Python和AHK之间的通信命令
"""

# 命令ID定义 (用于WM_COPYDATA的dwData字段)
CMD_PING = 1            # 测试连接
CMD_SET_TARGET = 2      # 设置目标窗口
CMD_ACTIVATE = 3        # 激活目标窗口
CMD_ENQUEUE = 4         # 添加按键到队列
CMD_CLEAR_QUEUE = 5     # 清空队列
CMD_PAUSE = 6           # 暂停队列处理
CMD_RESUME = 7          # 恢复队列处理
CMD_HOOK_REGISTER = 8   # 注册Hook
CMD_HOOK_UNREGISTER = 9 # 取消Hook
CMD_SEND_KEY = 10       # 发送单个按键
CMD_SEND_SEQUENCE = 11  # 发送按键序列

# 命令名称映射（用于调试）
CMD_NAMES = {
    CMD_PING: "PING",
    CMD_SET_TARGET: "SET_TARGET",
    CMD_ACTIVATE: "ACTIVATE",
    CMD_ENQUEUE: "ENQUEUE",
    CMD_CLEAR_QUEUE: "CLEAR_QUEUE",
    CMD_PAUSE: "PAUSE",
    CMD_RESUME: "RESUME",
    CMD_HOOK_REGISTER: "HOOK_REGISTER",
    CMD_HOOK_UNREGISTER: "HOOK_UNREGISTER",
    CMD_SEND_KEY: "SEND_KEY",
    CMD_SEND_SEQUENCE: "SEND_SEQUENCE",
}

def get_command_name(cmd_id: int) -> str:
    """获取命令名称"""
    return CMD_NAMES.get(cmd_id, f"UNKNOWN({cmd_id})")
