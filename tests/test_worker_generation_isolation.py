import sys
import threading
import time
from types import SimpleNamespace

import numpy as np

from torchlight_assistant.core.pathfinding_manager import (
    PathfindingManager,
    _PathfindingRun,
)
from torchlight_assistant.core.unified_scheduler import UnifiedScheduler
from torchlight_assistant.utils.a_star import astar


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_scheduler_rejects_restart_until_timed_out_callback_exits():
    scheduler = UnifiedScheduler()
    scheduler.STOP_JOIN_TIMEOUT_SECONDS = 0.02
    old_callback_entered = threading.Event()
    release_old_callback = threading.Event()
    new_callback_entered = threading.Event()
    callback_threads = []

    def callback():
        thread = threading.current_thread()
        callback_threads.append(thread)
        if thread.name == "UnifiedScheduler-1":
            old_callback_entered.set()
            release_old_callback.wait(1.0)
        else:
            new_callback_entered.set()

    assert scheduler.add_task(
        "blocking", 0.005, callback, start_immediately=True
    )
    assert scheduler.start()
    assert old_callback_entered.wait(1.0)
    old_run = scheduler._active_run
    old_thread = scheduler._scheduler_thread

    assert scheduler.stop()
    assert old_thread.is_alive()  # join 确实走了超时分支
    assert old_run.stop_event.is_set()

    # 任意 callback 已经开始后无法被外部撤销；调度器必须 fail-closed，不能让新一代
    # callback 与它重叠。旧线程退出后才允许重新启动。
    assert not scheduler.start()
    assert scheduler.get_status()["running"] is False

    release_old_callback.set()
    old_thread.join(1.0)
    assert not old_thread.is_alive()
    assert callback_threads.count(old_thread) == 1

    assert scheduler.start()
    new_run = scheduler._active_run
    assert new_run is not old_run
    assert new_run.stop_event is not old_run.stop_event
    assert new_callback_entered.wait(1.0)

    time.sleep(0.03)
    assert callback_threads.count(old_thread) == 1
    assert all(
        thread is old_thread or thread is new_run.thread for thread in callback_threads
    )
    scheduler.stop()


def test_scheduler_rejects_non_finite_or_non_positive_intervals():
    scheduler = UnifiedScheduler()

    for interval in (0, -1, float("nan"), float("inf"), "invalid", None):
        assert not scheduler.add_task("bad", interval, lambda: None)

    assert scheduler.add_task("valid", "0.25", lambda: None)
    original = scheduler._tasks["valid"].interval
    for interval in (0, -1, float("nan"), float("inf"), "invalid", None):
        assert not scheduler.update_task_interval("valid", interval)
        assert scheduler._tasks["valid"].interval == original


class _BlockingBorderManager:
    def __init__(self):
        self.window_activation_config = {"ahk_exe": "game.exe"}
        self.old_capture_entered = threading.Event()
        self.release_old_capture = threading.Event()
        self._capture_calls = 0
        self._lock = threading.Lock()

    def get_current_frame(self):
        with self._lock:
            self._capture_calls += 1
            call_number = self._capture_calls
        if call_number == 1:
            self.old_capture_entered.set()
            self.release_old_capture.wait(1.0)
            return np.zeros((2, 2, 4), dtype=np.uint8)
        return None


def test_pathfinding_old_capture_result_is_discarded_after_restart():
    border = _BlockingBorderManager()
    input_handler = SimpleNamespace(click_mouse_at=lambda *args, **kwargs: True)
    manager = PathfindingManager(border, input_handler)
    manager.STOP_JOIN_TIMEOUT_SECONDS = 0.02
    manager.GLOBAL_MAP_SIZE = 20
    manager.minimap_capture_area = (0, 0, 1, 1)
    stale_actions = []

    manager._process_map_image = lambda frame: (
        np.zeros((1, 1, 3), dtype=np.uint8),
        np.ones((1, 1), dtype=np.uint8),
    )
    manager._update_global_map = lambda path_mask, run: True
    manager._find_target = lambda minimap: None
    manager._execute_lawnmower_step = lambda path_mask, run: stale_actions.append(
        threading.current_thread()
    )

    try:
        assert manager.start()
        assert border.old_capture_entered.wait(1.0)
        old_run = manager._active_run
        old_thread = manager._thread

        assert manager.stop()
        assert old_thread.is_alive()
        assert old_run.stop_event.is_set()

        assert manager.start()
        new_run = manager._active_run
        assert new_run is not old_run
        assert new_run.stop_event is not old_run.stop_event

        border.release_old_capture.set()
        old_thread.join(1.0)
        assert not old_thread.is_alive()
        assert stale_actions == []
    finally:
        border.release_old_capture.set()
        manager.cleanup()


def test_pathfinding_discards_blocked_capture_result_after_pause():
    border = _BlockingBorderManager()
    manager = PathfindingManager(
        border, SimpleNamespace(click_mouse_at=lambda *args, **kwargs: True)
    )
    manager.GLOBAL_MAP_SIZE = 20
    manager.minimap_capture_area = (0, 0, 1, 1)
    stale_actions = []
    manager._process_map_image = lambda frame: stale_actions.append("processed") or (
        np.zeros((1, 1, 3), dtype=np.uint8),
        np.ones((1, 1), dtype=np.uint8),
    )

    try:
        assert manager.start()
        assert border.old_capture_entered.wait(1.0)
        assert manager.pause()
        border.release_old_capture.set()
        time.sleep(0.05)
        assert stale_actions == []
    finally:
        border.release_old_capture.set()
        manager.cleanup()


def _make_click_manager(rect):
    clicks = []
    manager = object.__new__(PathfindingManager)
    manager.border_manager = object()
    manager.input_handler = SimpleNamespace(
        click_mouse_at=lambda x, y, hold_time: clicks.append((x, y, hold_time))
        or True
    )
    manager._run_lock = threading.RLock()
    manager.is_running = True
    run = _PathfindingRun(1, threading.Event(), threading.Event())
    manager._active_run = run
    manager._resolve_target_client_rect = lambda: rect
    return manager, run, clicks


def test_pathfinding_click_uses_target_client_center_not_1920x1080():
    manager, run, clicks = _make_click_manager((100, 200, 1100, 800))

    assert manager._move_in_direction(1, 0, duration_ms=125, run=run)

    assert clicks == [(750, 500, 125)]


def test_pathfinding_click_stays_inside_small_target_client_area():
    manager, run, clicks = _make_click_manager((10, 20, 110, 100))

    assert manager._move_in_direction(1, 0, run=run)

    assert clicks == [(109, 60, 100)]


def test_pathfinding_refuses_click_without_safe_target_rect_or_current_run():
    manager, run, clicks = _make_click_manager(None)
    assert not manager._move_in_direction(1, 0, run=run)

    manager._resolve_target_client_rect = lambda: (0, 0, 800, 600)
    manager._active_run = _PathfindingRun(2, threading.Event(), threading.Event())
    assert not manager._move_in_direction(1, 0, run=run)
    assert clicks == []


def test_pathfinding_refuses_click_while_current_run_is_paused():
    manager, run, clicks = _make_click_manager((0, 0, 800, 600))
    run.pause_event.set()

    assert not manager._move_in_direction(1, 0, run=run)
    assert clicks == []


def test_pathfinding_does_not_enter_pathing_mode_when_astar_finds_no_path(
    monkeypatch,
):
    manager, run, _ = _make_click_manager((0, 0, 800, 600))
    manager.minimap_capture_area = (0, 0, 4, 4)
    manager.global_map = np.zeros((10, 10), dtype=np.uint8)
    manager.player_global_pos = [5, 5]
    manager.mode = "explore"
    monkeypatch.setattr(
        "torchlight_assistant.core.pathfinding_manager.astar",
        lambda *args, **kwargs: None,
    )

    assert not manager._plan_path_to_target((2, 2), run)
    assert manager.mode == "explore"


def test_pathfinding_astar_uses_bounded_array_view(monkeypatch):
    manager, run, _ = _make_click_manager((0, 0, 800, 600))
    manager.ASTAR_MIN_MARGIN = 4
    manager.minimap_capture_area = (0, 0, 10, 8)
    manager.global_map = np.ones((200, 200), dtype=np.uint8)
    manager.player_global_pos = [100, 100]
    captured = {}

    def fake_astar(maze, start, end):
        captured["shape"] = maze.shape
        captured["is_view"] = np.shares_memory(maze, manager.global_map)
        return [start, end]

    monkeypatch.setattr(
        "torchlight_assistant.core.pathfinding_manager.astar", fake_astar
    )

    assert manager._plan_path_to_target((9, 7), run)
    assert captured["is_view"] is True
    assert captured["shape"][0] < manager.global_map.shape[0]
    assert captured["shape"][1] < manager.global_map.shape[1]
    assert manager.path[-1] == (104, 103)


def test_pathfinding_rejects_out_of_bounds_astar_endpoint(monkeypatch):
    manager, run, _ = _make_click_manager((0, 0, 800, 600))
    manager.minimap_capture_area = (0, 0, 10, 10)
    manager.global_map = np.ones((20, 20), dtype=np.uint8)
    manager.player_global_pos = [1, 1]
    called = []
    monkeypatch.setattr(
        "torchlight_assistant.core.pathfinding_manager.astar",
        lambda *args: called.append(args),
    )

    assert not manager._plan_path_to_target((0, 0), run)
    assert called == []


def test_phase_correlation_rejects_low_confidence_and_impossible_shift(monkeypatch):
    manager = object.__new__(PathfindingManager)
    image = np.ones((100, 100), dtype=np.uint8)
    monkeypatch.setattr(
        "torchlight_assistant.core.pathfinding_manager.cv2.phaseCorrelate",
        lambda a, b: ((2.0, 3.0), 0.01),
    )
    assert manager._calculate_displacement(image, image) is None

    monkeypatch.setattr(
        "torchlight_assistant.core.pathfinding_manager.cv2.phaseCorrelate",
        lambda a, b: ((49.0, 0.0), 0.9),
    )
    assert manager._calculate_displacement(image, image) is None


def test_global_player_position_follows_current_to_previous_map_shift(monkeypatch):
    manager, run, _ = _make_click_manager((0, 0, 800, 600))
    manager.global_map = np.zeros((30, 30), dtype=np.uint8)
    manager.player_global_pos = [15, 15]
    manager.last_minimap_mask = np.ones((3, 3), dtype=np.uint8)
    manager._stitch_map = lambda mask: None
    monkeypatch.setattr(manager, "_calculate_displacement", lambda a, b: (5.0, -2.0))

    assert manager._update_global_map(np.ones((3, 3), dtype=np.uint8), run)
    assert manager.player_global_pos == [20, 13]


def test_stitch_map_crops_source_when_player_is_near_negative_edge():
    manager = object.__new__(PathfindingManager)
    manager.global_map = np.zeros((4, 4), dtype=np.uint8)
    manager.player_global_pos = [0, 0]
    source = np.arange(1, 10, dtype=np.uint8).reshape(3, 3)

    manager._stitch_map(source)

    # top-left=(-1,-1): only source[1:,1:] intersects the global map.
    assert manager.global_map[:2, :2].tolist() == [[5, 6], [8, 9]]


def test_astar_validates_bounds_and_does_not_cut_blocked_corners():
    open_grid = np.ones((3, 3), dtype=np.uint8)
    assert astar(open_grid, (-1, 0), (2, 2)) is None
    assert astar(open_grid, (0, 0), (3, 2)) is None

    blocked_corner = np.array([[1, 0], [0, 1]], dtype=np.uint8)
    assert astar(blocked_corner, (0, 0), (1, 1)) is None

    path = astar(open_grid, (0, 0), (2, 2))
    assert path == [(0, 0), (1, 1), (2, 2)]


def test_target_client_rect_requires_target_to_be_foreground(monkeypatch):
    from torchlight_assistant.utils.window_utils import WindowUtils

    manager = object.__new__(PathfindingManager)
    manager.border_manager = SimpleNamespace(
        window_activation_config={"ahk_exe": "game.exe"}
    )
    monkeypatch.setattr(
        WindowUtils, "find_target_window", staticmethod(lambda config: 123)
    )
    fake_win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: True,
        GetForegroundWindow=lambda: 456,
        GetClientRect=lambda hwnd: (0, 0, 800, 600),
        ClientToScreen=lambda hwnd, point: point,
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)

    assert manager._resolve_target_client_rect() is None


def test_target_client_rect_never_falls_back_to_unconfigured_foreground():
    manager = object.__new__(PathfindingManager)
    manager.border_manager = SimpleNamespace(window_activation_config={})

    assert manager._resolve_target_client_rect() is None
