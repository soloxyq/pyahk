"""PriorityDeque - 线程安全的双队列结构

设计目标:
1. 支持高/普通优先级插入, 取出时始终先消费高优先级。
2. 提供与 queue.Queue 近似的最小接口 (put/get/qsize/full/clear/task_done)。
3. 不暴露内部数据结构, 禁止外部直接操作锁与双端队列, 避免像标准 Queue 那样去动 .mutex/.queue。
4. 可选 maxsize (高+普通 总容量)。

注意: 不实现 join() 统计复杂逻辑, 当前使用场景仅需要非阻塞 put/get + 尺寸与清空。
"""

from collections import deque
from threading import Condition
from typing import Deque, Optional, Any
from queue import Empty, Full  # 复用标准异常类型, 调用方无需改变


class PriorityDeque:
    """线程安全高/普通优先级双队列。

    高优先级采用独立 deque 存储; get() 时若高优先级非空优先弹出。
    提供 put(..., priority=True/False) 区分。
    """

    def __init__(self, maxsize: int = 0):
        self._high: Deque[Any] = deque()
        self._normal: Deque[Any] = deque()
        self._maxsize = maxsize  # 0 表示无限
        self._cond = Condition()
        self._unfinished_tasks = 0  # 仅保持与 task_done() 兼容 (调试用)

    # -------- 内部辅助 --------
    def _total_size_unlocked(self) -> int:
        return len(self._high) + len(self._normal)

    # -------- 公共接口 --------
    def qsize(self) -> int:
        with self._cond:
            return self._total_size_unlocked()

    def full(self) -> bool:
        if self._maxsize <= 0:
            return False
        with self._cond:
            return self._total_size_unlocked() >= self._maxsize

    def empty(self) -> bool:
        with self._cond:
            return self._total_size_unlocked() == 0

    def put(self, item: Any, priority: bool = False, block: bool = False, timeout: Optional[float] = None):
        """放入元素.

        默认为非阻塞行为 (与调用方当前模式匹配). 若队列满则抛出 Full。
        """
        with self._cond:
            if self._maxsize > 0 and self._total_size_unlocked() >= self._maxsize:
                if not block:
                    raise Full
                # 阻塞模式: 等待空间
                if timeout is None:
                    while self._total_size_unlocked() >= self._maxsize:
                        self._cond.wait()
                else:
                    end_time = timeout and (timeout + __import__('time').time())
                    while self._total_size_unlocked() >= self._maxsize:
                        remaining = end_time - __import__('time').time() if end_time else None
                        if remaining is not None and remaining <= 0:
                            raise Full
                        self._cond.wait(remaining)

            if priority:
                self._high.append(item)
            else:
                self._normal.append(item)
            self._unfinished_tasks += 1
            self._cond.notify()

    def get(self, block: bool = True, timeout: Optional[float] = None):
        """取出元素; 优先返回高优先级。默认阻塞, 与 queue.Queue 行为对齐。"""
        with self._cond:
            if not block:
                if not self._high and not self._normal:
                    raise Empty
            else:
                if timeout is None:
                    while not self._high and not self._normal:
                        self._cond.wait()
                else:
                    end_time = timeout + __import__('time').time()
                    while not self._high and not self._normal:
                        remaining = end_time - __import__('time').time()
                        if remaining <= 0:
                            raise Empty
                        self._cond.wait(remaining)

            if self._high:
                item = self._high.popleft()
            else:
                item = self._normal.popleft()

            return item

    def task_done(self):
        # 轻量实现: 仅递减计数, 不做 join() 等待, 避免复杂性
        with self._cond:
            if self._unfinished_tasks > 0:
                self._unfinished_tasks -= 1
                if self._maxsize > 0:
                    # 释放等待 put 的线程
                    self._cond.notify_all()

    def clear(self):
        with self._cond:
            self._high.clear()
            self._normal.clear()
            self._unfinished_tasks = 0
            if self._maxsize > 0:
                self._cond.notify_all()
