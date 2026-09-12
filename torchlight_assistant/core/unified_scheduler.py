"""统一调度器 - 替代多个Timer线程的高效调度系统

改进: 使用 `time.monotonic()` 作为内部时间源，避免系统时钟回拨/同步导致的任务漂移。
外部仅提供 interval（相对时间），不依赖绝对 wall clock，因此切换安全。
`get_status` 额外提供一个估算的 wall clock 执行时间字段，方便 UI 显示。
"""

import time
import threading
from typing import Dict, Any, Callable, Optional
from collections import defaultdict
import heapq
import math
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
    kwargs: Optional[dict] = None
    enabled: bool = True

    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}

    def __lt__(self, other):
        return self.next_run_time < other.next_run_time


@dataclass
class _SchedulerRun:
    """一次调度线程的私有停止令牌。

    ``stop()`` 的 join 有超时，旧 callback 可能还没有返回。令牌不能复用，否则下一次
    ``start()`` 清掉共享停止状态时，旧线程会跟着新线程一起恢复执行。
    """

    generation: int
    stop_event: threading.Event
    thread: Optional[threading.Thread] = None


class UnifiedScheduler:
    """统一调度器 - 使用单线程管理所有定时任务"""

    STOP_JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(self):
        self._tasks = {}  # task_id -> ScheduledTask
        self._task_heap = []  # 优先级队列，按执行时间排序
        self._running = False
        self._paused = False
        self._scheduler_thread = None
        self._active_run: Optional[_SchedulerRun] = None
        self._next_generation = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def _now(self) -> float:
        """内部时间源（单调递增，不受系统时间调整影响）。"""
        return time.monotonic()

    def add_task(
        self,
        task_id: str,
        interval: float,
        callback: Callable,
        args: tuple = (),
        kwargs: Optional[dict] = None,
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
            normalized_interval = float(interval)
        except (TypeError, ValueError, OverflowError):
            normalized_interval = math.nan
        if not math.isfinite(normalized_interval) or normalized_interval <= 0:
            LOG_ERROR(f"[统一调度器] 任务 '{task_id}' 间隔必须是有限正数: {interval}")
            return False
        interval = normalized_interval
            
        try:
            with self._condition:
                if task_id in self._tasks:
                    return False

                # 基于 monotonic 计算下次执行时间
                next_run = self._now() + (0.01 if start_immediately else interval)
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
        try:
            normalized_interval = float(new_interval)
        except (TypeError, ValueError, OverflowError):
            normalized_interval = math.nan
        if not math.isfinite(normalized_interval) or normalized_interval <= 0:
            LOG_ERROR(
                f"[统一调度器] 任务 '{task_id}' 新间隔必须是有限正数: "
                f"{new_interval}"
            )
            return False
        with self._condition:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.interval = normalized_interval

                # 重新计算下次执行时间
                task.next_run_time = self._now() + normalized_interval
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
                task.next_run_time = self._now() + task.interval
                self._rebuild_heap()
                self._condition.notify()
                return True
            return False

    def start(self) -> bool:
        """启动调度器"""
        with self._condition:
            if self._running:
                return False

            # 调度 callback 是任意第三方代码：一旦已经进入 callback，调度器无法在
            # callback 内部再做世代检查。若上一代 join 超时仍活着，此时启动新一代会
            # 让旧 callback 与新 callback 并行，并可能在新 AHK 闸门打开后发迟到动作。
            # 因此这里选择 fail-closed；等旧 callback 返回、worker finally 清理后再试。
            previous_run = self._active_run
            if (
                previous_run is not None
                and previous_run.thread is not None
                and previous_run.thread.is_alive()
            ):
                LOG_ERROR(
                    f"[统一调度器] 第 {previous_run.generation} 代 callback 尚未退出，"
                    "拒绝启动新一代"
                )
                return False
            if previous_run is not None:
                self._active_run = None
                self._scheduler_thread = None

            self._next_generation += 1
            run = _SchedulerRun(self._next_generation, threading.Event())
            self._running = True
            self._paused = False
            run.thread = threading.Thread(
                target=self._scheduler_loop,
                args=(run,),
                name=f"UnifiedScheduler-{run.generation}",
                daemon=True,
            )
            self._active_run = run
            self._scheduler_thread = run.thread
            run.thread.start()
            return True

    def stop(self) -> bool:
        """停止调度器"""
        with self._condition:
            run = self._active_run
            if run is None:
                return False

            was_running = self._running
            self._running = False
            run.stop_event.set()
            self._condition.notify_all()

        # callback 是第三方代码，无法强杀。超时后旧线程持有的私有 stop_event
        # 永远保持 set，callback 一返回就会退出；在它真正退出前 start() 会
        # fail-closed，避免两代 callback 并行跨越上层输入闸门。
        thread = run.thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=self.STOP_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                LOG_ERROR(
                    f"[统一调度器] 第 {run.generation} 代线程停止超时，"
                    "已隔离；它返回后不会再执行回调"
                )

        return was_running or bool(thread and thread.is_alive())

    def pause(self) -> bool:
        """暂停所有任务执行"""
        with self._condition:
            if not self._running or self._paused:
                return False

            self._paused = True
            return True

    def resume(self) -> bool:
        """恢复所有任务执行"""
        with self._condition:
            if not self._running or not self._paused:
                return False

            self._paused = False
            # 恢复时重新计算所有任务的执行时间
            current_time = self._now()
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
            monotonic_now = self._now()
            next_run_monotonic = self._task_heap[0].next_run_time if self._task_heap else None
            # 估算 wall clock: 当前 wall clock + (next_monotonic - monotonic_now)
            if next_run_monotonic is not None:
                wall_clock_estimate = time.time() + (next_run_monotonic - monotonic_now)
            else:
                wall_clock_estimate = None

            return {
                "running": self._running,
                "paused": self._paused,
                "total_tasks": len(self._tasks),
                "enabled_tasks": sum(1 for t in self._tasks.values() if t.enabled),
                # 内部使用的 monotonic 时间戳（调试用）
                "next_execution_monotonic": next_run_monotonic,
                # 估算的 wall clock 时间（供 UI 显示）
                "next_execution_wall_clock": wall_clock_estimate,
            }

    def _rebuild_heap(self):
        """重建任务堆。

        🔧 BUG修复: 调用方(update_task_interval / resume)会原地修改堆内 ScheduledTask
        的 next_run_time(比较键),必须无条件重新 heapify 才能恢复堆序不变量。原实现用
        len(enabled)==len(heap) 做提前返回,在稳态(无 disabled 残留)下恰好跳过 heapify,
        导致堆顶不再是最早任务 → 任务时序错乱(被延迟或相对兄弟过早执行)。任务数很少,
        无条件重建的 O(n) 开销可忽略。
        """
        self._task_heap = [task for task in self._tasks.values() if task.enabled]
        heapq.heapify(self._task_heap)

    def _is_current_run_locked(self, run: _SchedulerRun) -> bool:
        return (
            self._active_run is run
            and self._running
            and not run.stop_event.is_set()
        )

    def _scheduler_loop(self, run: _SchedulerRun):
        """调度器主循环 - 优化版本"""
        disabled_task_cleanup_counter = 0
        max_disabled_tasks = 10  # 累积这么多个禁用任务后才清理

        try:
            while True:
                with self._condition:
                    # 世代身份和私有停止令牌都必须匹配。只检查共享 _running 会让 join
                    # 超时的旧线程在下一次 start() 后重新进入循环。
                    if not self._is_current_run_locked(run):
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
                    current_time = self._now()

                    # 如果还没到执行时间，等待
                    if next_task.next_run_time > current_time:
                        wait_time = min(next_task.next_run_time - current_time, 0.1)
                        self._condition.wait(timeout=wait_time)
                        continue

                    # 执行任务
                    task = heapq.heappop(self._task_heap)

                    # 重新安排下一次执行
                    if task.enabled and task.task_id in self._tasks:
                        task.next_run_time = current_time + task.interval
                        heapq.heappush(self._task_heap, task)

                # 在锁外执行回调，避免死锁。取出任务后再做一次世代检查，覆盖 stop()
                # 恰好发生在出锁与回调调用之间的常见竞态窗口。
                with self._condition:
                    callback_allowed = (
                        self._is_current_run_locked(run)
                        and task.enabled
                        and self._tasks.get(task.task_id) is task
                    )
                if not callback_allowed:
                    continue

                try:
                    task.callback(*task.args, **task.kwargs)
                except Exception as e:
                    LOG_ERROR(
                        f"[统一调度器] 任务 '{task.task_id}' 回调执行异常: {e}"
                    )
        finally:
            with self._condition:
                # 旧世代迟到退出时不得覆盖新世代的运行状态/线程引用。
                if self._active_run is run:
                    self._running = False
                    self._active_run = None
                    self._scheduler_thread = None
                self._condition.notify_all()

    def __del__(self):
        """析构函数，确保资源清理"""
        self.stop()
