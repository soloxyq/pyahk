import threading
import time
from collections import defaultdict
from typing import Callable, Dict, List
from concurrent.futures import ThreadPoolExecutor
from ..utils.debug_log import LOG_ERROR, LOG


class EventBus:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # The __init__ might be called multiple times in a singleton pattern,
        # so we check if the attribute exists before initializing.
        if not hasattr(self, "subscribers"):
            self.subscribers: Dict[str, List[Callable]] = defaultdict(list)
            self.subscribers_lock = threading.RLock()
            
            # 增加线程池容量，提高并发处理能力
            self._executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="EventBus")
            
            # 使用threading.local()为每个线程提供独立的调用栈，用于检测递归事件
            self._call_stack = threading.local()

            # 性能监控
            self._event_count = 0
            self._last_performance_log = time.time()
            self._performance_log_interval = 120  # 每2分钟记录一次性能数据

    def subscribe(self, event_name: str, handler: Callable):
        """订阅一个事件"""
        with self.subscribers_lock:
            if handler not in self.subscribers[event_name]:
                self.subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: Callable):
        """取消订阅一个事件"""
        with self.subscribers_lock:
            if handler in self.subscribers[event_name]:
                self.subscribers[event_name].remove(handler)

    def publish(self, event_name: str, *args, **kwargs):
        """发布一个事件，通知所有订阅者"""
        # --- 递归检测 ---
        if not hasattr(self._call_stack, 'events'):
            self._call_stack.events = set()
        if event_name in self._call_stack.events:
            LOG_ERROR(f"[EventBus] 检测到递归事件，已阻止: {event_name}")
            return
        self._call_stack.events.add(event_name)

        try:
            self._update_performance_metrics()
            
            # --- 优化锁粒度 ---
            with self.subscribers_lock:
                # 复制订阅者列表，然后立即释放锁
                handlers = self.subscribers.get(event_name, []).copy()
            
            if not handlers:
                return

            for handler in handlers:
                try:
                    handler(*args, **kwargs)
                except Exception as e:
                    LOG_ERROR(f"Error in event bus handler for '{event_name}': {e}")
        finally:
            # 清理调用栈
            self._call_stack.events.remove(event_name)

    def _update_performance_metrics(self):
        """更新和记录性能指标"""
        self._event_count += 1
        current_time = time.time()
        if current_time - self._last_performance_log >= self._performance_log_interval:
            events_per_second = self._event_count / self._performance_log_interval
            if events_per_second > 1.0:
                LOG(f"[事件总线性能] 事件频率: {events_per_second:.2f} events/s")
            self._event_count = 0
            self._last_performance_log = current_time


    def publish_async(self, event_name: str, *args, **kwargs):
        """异步发布事件（用于非关键路径的事件）"""
        with self.subscribers_lock:
            handlers = self.subscribers.get(event_name, [])
            
            if not handlers:
                return
                
            # 异步执行所有处理器
            for handler in handlers:
                self._executor.submit(self._safe_async_handler, event_name, handler, *args, **kwargs)
    
    def _safe_async_handler(self, event_name: str, handler: Callable, *args, **kwargs):
        """安全地执行异步事件处理器"""
        try:
            handler(*args, **kwargs)
        except Exception as e:
            LOG_ERROR(f"Error in async event bus handler for '{event_name}': {e}")
    
    def cleanup(self):
        """清理资源"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=True)


# 全局唯一的事件总线实例
event_bus = EventBus()
