"""主模式共有的 HP/MP 调度，不依附技能或寻路的业务循环。"""

from .unified_scheduler import UnifiedScheduler


class ResourceScheduler:
    def __init__(self, manager):
        self.manager = manager
        self.scheduler = UnifiedScheduler()
        self.scheduler.add_task(
            "resource_checker", self._interval(), manager.check_and_execute_resources
        )

    def _interval(self):
        return max(1, self.manager.check_interval) / 1000.0

    def update_interval(self):
        return self.scheduler.update_task_interval("resource_checker", self._interval())

    def start(self):
        if self.scheduler.get_status()["running"]:
            return self.is_running()
        self.update_interval()
        self.manager.start()
        try:
            started = self.scheduler.start()
        except Exception:
            self.manager.stop()
            raise
        if not started:
            self.manager.stop()
            return False
        return True

    def stop(self):
        # 先作废 token，之后才有界等待：迟到 OCR 不能越过 READY 开闸。
        self.manager.stop()
        self.scheduler.stop()
        return True

    def pause(self):
        self.manager.pause()
        self.scheduler.pause()
        return True

    def resume(self):
        self.manager.resume()
        if not self.scheduler.resume():
            self.manager.pause()
            return False
        return True

    def is_running(self):
        status = self.scheduler.get_status()
        return self.manager.is_running() and status["running"] and not status["paused"]

    def cleanup(self):
        self.stop()
        self.scheduler.clear_all_tasks()
