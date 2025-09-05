"""状态定义模块

包含系统中使用的各种状态枚举定义。
"""

from enum import Enum


class MacroState(Enum):
    """宏引擎状态枚举"""

    STOPPED = "stopped"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
