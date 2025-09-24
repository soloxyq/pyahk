#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多级优先队列实现"""

import threading
from collections import deque
from typing import Any, Optional
from queue import Empty, Full


class MultiPriorityQueue:
    """多级优先队列 - 支持多个优先级等级
    
    使用场景：
    - emergency (紧急): HP/MP药剂
    - high (高): 技能高优先级  
    - normal (普通): 技能普通优先级
    - low (低): 辅助功能
    """

    def __init__(self, maxsize: int = 0):
        # 使用字典存储不同优先级的队列，按优先级从高到低排列
        self._queues = {
            'emergency': deque(),  # 紧急队列
            'high': deque(),       # 高优先级队列
            'normal': deque(),     # 普通队列
            'low': deque()         # 低优先级队列
        }
        self._priority_order = ['emergency', 'high', 'normal', 'low']
        self._maxsize = maxsize
        self._cond = threading.Condition()

    def _total_size_unlocked(self) -> int:
        return sum(len(q) for q in self._queues.values())

    def put(self, item: Any, priority: str = 'normal', block: bool = False, timeout: Optional[float] = None):
        """放入元素
        
        Args:
            item: 要放入的元素
            priority: 优先级 ('emergency', 'high', 'normal', 'low')
            block: 是否阻塞
            timeout: 超时时间
        """
        # 简单验证优先级参数
        if priority not in self._queues:
            priority = 'normal'  # 自动降级到普通优先级
            
        with self._cond:
            if self._maxsize > 0 and self._total_size_unlocked() >= self._maxsize:
                if not block:
                    raise Full
                # 阻塞等待
                if timeout is None:
                    while self._total_size_unlocked() >= self._maxsize:
                        self._cond.wait()
                else:
                    end_time = timeout + __import__('time').time()
                    while self._total_size_unlocked() >= self._maxsize:
                        remaining = end_time - __import__('time').time() if end_time else None
                        if remaining is not None and remaining <= 0:
                            raise Full
                        self._cond.wait(remaining)

            self._queues[priority].append(item)
            self._cond.notify()

    def get(self, block: bool = True, timeout: Optional[float] = None):
        """取出元素 - 按优先级顺序"""
        with self._cond:
            if not block:
                if self._total_size_unlocked() == 0:
                    raise Empty
            else:
                if timeout is None:
                    while self._total_size_unlocked() == 0:
                        self._cond.wait()
                else:
                    end_time = timeout + __import__('time').time()
                    while self._total_size_unlocked() == 0:
                        remaining = end_time - __import__('time').time()
                        if remaining is not None and remaining <= 0:
                            raise Empty
                        self._cond.wait(remaining)

            # 按优先级顺序取出
            for priority in self._priority_order:
                if self._queues[priority]:
                    return self._queues[priority].popleft()
            
            raise Empty  # 理论上不会到达这里

    def qsize(self) -> int:
        """获取队列总大小"""
        with self._cond:
            return self._total_size_unlocked()

    def empty(self) -> bool:
        """检查队列是否为空"""
        with self._cond:
            return self._total_size_unlocked() == 0

    def full(self) -> bool:
        """检查队列是否已满"""
        if self._maxsize <= 0:
            return False
        with self._cond:
            return self._total_size_unlocked() >= self._maxsize

    def clear(self):
        """清空所有队列"""
        with self._cond:
            for queue in self._queues.values():
                queue.clear()
            self._unfinished_tasks = 0