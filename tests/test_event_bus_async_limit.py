import threading
from collections import defaultdict

from torchlight_assistant.core.event_bus import EventBus


class _HoldingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


class _FailingExecutor:
    def submit(self, fn, *args, **kwargs):
        raise RuntimeError("executor closed")


def _isolated_bus():
    bus = object.__new__(EventBus)
    bus.subscribers = {}
    bus.subscribers_lock = threading.RLock()
    bus._async_event_counts = defaultdict(int)
    bus._async_lock = threading.Lock()
    bus._max_async_per_event = 8
    bus._executor = _HoldingExecutor()
    return bus


def test_async_limit_counts_every_handler_before_submitting():
    bus = _isolated_bus()
    bus.subscribers["tick"] = [lambda: None for _ in range(5)]

    bus.publish_async("tick")
    bus.publish_async("tick")

    assert len(bus._executor.calls) == 5
    assert bus._async_event_counts["tick"] == 5


def test_async_limit_check_and_reservation_are_atomic():
    bus = _isolated_bus()
    bus.subscribers["tick"] = [lambda: None]
    barrier = threading.Barrier(10)

    def publish():
        barrier.wait()
        bus.publish_async("tick")

    threads = [threading.Thread(target=publish) for _ in range(9)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(bus._executor.calls) == 8
    assert bus._async_event_counts["tick"] == 8


def test_submit_failure_releases_reserved_capacity():
    bus = _isolated_bus()
    bus.subscribers["tick"] = [lambda: None, lambda: None]
    bus._executor = _FailingExecutor()

    bus.publish_async("tick")

    assert "tick" not in bus._async_event_counts
