"""统一调度器 - 替代多个Timer线程的高效调度系统"""

import time
import threading
from typing import Dict, Any, Callable, Optional
from collections import defaultdict
import heapq
from dataclasses import dataclass
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO


@dataclass
class ScheduledTask:
    """调度任务数据类"""

    task_id: str
    next_run_time: float
    interval: float
    callback: Callable
    args: tuple = ()
    kwargs: dict = None
    enabled: bool = True

    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}

    def __lt__(self, other):
        return self.next_run_time < other.next_run_time


class UnifiedScheduler:
    """统一调度器 - 使用单线程管理所有定时任务"""

    def __init__(self):
        self._tasks = {}  # task_id -> ScheduledTask
        self._task_heap = []  # 优先级队列，按执行时间排序
        self._running = False
        self._paused = False
        self._scheduler_thread = None
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def add_task(
        self,
        task_id: str,
        interval: float,
        callback: Callable,
        args: tuple = (),
        kwargs: dict = None,
        start_immediately: bool = False,
    ) -> bool:
        """添加定时任务

        Args:
            task_id: 任务唯一标识
            interval: 执行间隔（秒）
            callback: 回调函数
            args: 回调函数参数
            kwargs: 回调函数关键字参数
            start_immediately: 是否立即开始执行

        Returns:
            是否添加成功
        """
        try:
            with self._condition:
                if task_id in self._tasks:
                    return False

                # 移除高频调试输出以减少日志噪音

                next_run = time.time() + (0.01 if start_immediately else interval)
                task = ScheduledTask(
                    task_id=task_id,
                    next_run_time=next_run,
                    interval=interval,
                    callback=callback,
                    args=args,
                    kwargs=kwargs or {},
                )

                self._tasks[task_id] = task
                heapq.heappush(self._task_heap, task)
                self._condition.notify()

                return True

        except Exception as e:
            LOG_ERROR(f"[统一调度器] 添加任务 '{task_id}' 失败: {e}")
            return False

    def remove_task(self, task_id: str) -> bool:
        """移除任务"""
        with self._condition:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.enabled = False  # 标记为禁用，实际清理在调度循环中进行
                del self._tasks[task_id]
                self._condition.notify()
                return True
            return False

    def update_task_interval(self, task_id: str, new_interval: float) -> bool:
        """更新任务执行间隔"""
        with self._condition:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                old_interval = task.interval
                task.interval = new_interval

                # 重新计算下次执行时间
                task.next_run_time = time.time() + new_interval
                # 重新构建堆（简单方式）
                self._rebuild_heap()
                self._condition.notify()
                return True
            return False

    def pause_task(self, task_id: str) -> bool:
        """暂停特定任务"""
        with self._condition:
            if task_id in self._tasks:
                self._tasks[task_id].enabled = False
                return True
            return False

    def resume_task(self, task_id: str) -> bool:
        """恢复特定任务"""
        with self._condition:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.enabled = True
                task.next_run_time = time.time() + task.interval
                self._rebuild_heap()
                self._condition.notify()
                return True
            return False

    def start(self) -> bool:
        """启动调度器"""
        with self._condition:
            if self._running:
                return False

            self._running = True
            self._paused = False
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, name="UnifiedScheduler", daemon=True
            )
            self._scheduler_thread.start()
            return True

    def stop(self) -> bool:
        """停止调度器"""
        with self._condition:
            if not self._running:
                return False

            self._running = False
            self._condition.notify()

        # 等待调度线程结束
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)

        return True

    def pause(self) -> bool:
        """暂停所有任务执行"""
        with self._condition:
            if not self._running:
                return False

            self._paused = not self._paused
            if not self._paused:
                # 恢复时重新计算所有任务的执行时间
                current_time = time.time()
                for task in self._tasks.values():
                    if task.enabled:
                        task.next_run_time = current_time + task.interval
                self._rebuild_heap()
                self._condition.notify()

            return True

    def resume(self) -> bool:
        """恢复所有任务执行"""
        with self._condition:
            if not self._running or not self._paused:
                return False

            self._paused = False
            # 恢复时重新计算所有任务的执行时间
            current_time = time.time()
            for task in self._tasks.values():
                if task.enabled:
                    task.next_run_time = current_time + task.interval
            self._rebuild_heap()
            self._condition.notify()
            return True

    def clear_all_tasks(self):
        """清除所有任务"""
        with self._condition:
            self._tasks.clear()
            self._task_heap.clear()
            self._condition.notify()

    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        with self._condition:
            return {
                "running": self._running,
                "paused": self._paused,
                "total_tasks": len(self._tasks),
                "enabled_tasks": sum(1 for t in self._tasks.values() if t.enabled),
                "next_execution": (
                    self._task_heap[0].next_run_time if self._task_heap else None
                ),
            }

    def _rebuild_heap(self):
        """重建任务堆 - 优化版本，只重建启用的任务"""
        # 只重建启用的任务，减少内存分配
        enabled_tasks = [task for task in self._tasks.values() if task.enabled]
        if len(enabled_tasks) != len(self._task_heap):
            self._task_heap = enabled_tasks
            heapq.heapify(self._task_heap)

    def _scheduler_loop(self):
        """调度器主循环 - 优化版本"""
        disabled_task_cleanup_counter = 0
        max_disabled_tasks = 10  # 累积这么多个禁用任务后才清理
        
        while True:
            with self._condition:
                # 检查是否应该停止
                if not self._running:
                    break

                # 如果暂停或没有任务，等待
                if self._paused or not self._task_heap:
                    self._condition.wait(timeout=0.1)
                    continue

                # 批量清理已禁用的任务（减少频繁清理）
                while self._task_heap and not self._task_heap[0].enabled:
                    heapq.heappop(self._task_heap)
                    disabled_task_cleanup_counter += 1
                    
                # 如果清理了太多禁用任务，重建堆以优化性能
                if disabled_task_cleanup_counter >= max_disabled_tasks:
                    self._rebuild_heap()
                    disabled_task_cleanup_counter = 0

                if not self._task_heap:
                    self._condition.wait(timeout=0.1)
                    continue

                # 获取下一个要执行的任务
                next_task = self._task_heap[0]
                current_time = time.time()

                # 如果还没到执行时间，等待
                if next_task.next_run_time > current_time:
                    wait_time = min(next_task.next_run_time - current_time, 0.1)
                    self._condition.wait(timeout=wait_time)
                    continue

                # 执行任务
                task = heapq.heappop(self._task_heap)

                # 重新安排下次执行
                if task.enabled and task.task_id in self._tasks:
                    task.next_run_time = current_time + task.interval
                    heapq.heappush(self._task_heap, task)

            # 在锁外执行回调，避免死锁
            try:
                if task.enabled:
                    task.callback(*task.args, **task.kwargs)
            except Exception as e:
                LOG_ERROR(f"[统一调度器] 任务 '{task.task_id}' 回调执行异常: {e}")

    def __del__(self):
        """析构函数，确保资源清理"""
        self.stop()
