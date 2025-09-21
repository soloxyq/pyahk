"""
简单的调试日志接口
通过DEBUG环境变量控制日志输出，类似C语言宏的效果
在模块导入时就决定LOG函数的行为，避免运行时if判断开销
支持日志节流机制，防止高频日志输出影响性能
"""

import os
import sys
import time
from typing import Any, Dict

# 检查是否定义了DEBUG环境变量
DEBUG_ENABLED = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes", "on")

# 日志节流缓存
_log_throttle_cache: Dict[str, float] = {}


def _debug_log(*args: Any, **kwargs: Any) -> None:
    """实际的调试日志函数"""
    print(*args, **kwargs)


def _empty_log(*args: Any, **kwargs: Any) -> None:
    """空的日志函数，什么都不做"""
    pass


# 根据DEBUG_ENABLED在导入时决定LOG函数的实现
# 这样避免了运行时的if判断开销
if DEBUG_ENABLED:
    LOG = _debug_log
else:
    LOG = _empty_log


def LOG_ERROR(*args: Any, **kwargs: Any) -> None:
    """
    错误日志接口
    总是输出到stderr
    """
    print(*args, file=sys.stderr, **kwargs)


def LOG_INFO(*args: Any, **kwargs: Any) -> None:
    """
    信息日志接口
    总是输出
    """
    print(*args, **kwargs)


def LOG_INFO_THROTTLED(key: str, interval: float, *args: Any, **kwargs: Any) -> None:
    """
    节流信息日志接口
    相同key的日志在指定间隔内只输出一次

    Args:
        key: 日志唯一标识
        interval: 节流间隔（秒）
        *args: 日志参数
        **kwargs: 日志关键字参数
    """
    current_time = time.time()
    last_time = _log_throttle_cache.get(key, 0)

    if current_time - last_time >= interval:
        _log_throttle_cache[key] = current_time
        print(*args, **kwargs)


def LOG_ERROR_THROTTLED(key: str, interval: float, *args: Any, **kwargs: Any) -> None:
    """
    节流错误日志接口
    相同key的错误日志在指定间隔内只输出一次

    Args:
        key: 日志唯一标识
        interval: 节流间隔（秒）
        *args: 日志参数
        **kwargs: 日志关键字参数
    """
    current_time = time.time()
    last_time = _log_throttle_cache.get(key, 0)

    if current_time - last_time >= interval:
        _log_throttle_cache[key] = current_time
        print(*args, file=sys.stderr, **kwargs)


# 为了方便使用，也可以直接导入LOG函数
__all__ = ["LOG", "LOG_ERROR", "LOG_INFO", "LOG_INFO_THROTTLED", "LOG_ERROR_THROTTLED", "DEBUG_ENABLED"]
