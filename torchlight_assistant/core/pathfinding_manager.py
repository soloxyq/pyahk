"""
自动寻路管理器 (Pathfinding Manager)

负责处理所有与自动寻路相关的功能，包括：
- 地图数据管理 (拼接、栅格化)
- 路径规划 (A* 算法)
- 角色移动执行
"""

import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .event_bus import event_bus
from .ahk_input_handler import AHKInputHandler
from ..utils.border_frame_manager import BorderFrameManager
from ..utils.debug_log import LOG_INFO, LOG_ERROR, LOG
from ..utils.a_star import astar
from ..utils.config_values import config_int


@dataclass
class _PathfindingRun:
    """一次寻路运行的私有控制令牌。"""

    generation: int
    stop_event: threading.Event
    pause_event: threading.Event
    thread: Optional[threading.Thread] = None


class PathfindingManager:
    """管理自动寻路的状态和流程"""

    STOP_JOIN_TIMEOUT_SECONDS = 1.0
    MOVEMENT_CLICK_DISTANCE = 150.0
    GLOBAL_MAP_SIZE = 5000
    ASTAR_MIN_MARGIN = 64
    MIN_PHASE_CORRELATION_RESPONSE = 0.10
    MAX_PHASE_SHIFT_RATIO = 0.45

    def __init__(self, border_manager: BorderFrameManager, input_handler: AHKInputHandler):
        self.border_manager = border_manager
        self.input_handler = input_handler
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._run_lock = threading.RLock()
        self._active_run: Optional[_PathfindingRun] = None
        self._next_generation = 0
        # 兼容 MacroEngine 的暂停状态检查；每次 start 都替换为新世代私有事件。
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._cleanup_done = False

        # 地图与探索状态
        self.global_map: Optional[np.ndarray] = None
        self.player_global_pos: List[int] = [0, 0]
        self.last_minimap_mask: Optional[np.ndarray] = None
        self.minimap_capture_area: Optional[Tuple[int, int, int, int]] = None
        
        # 寻路状态
        self.mode = "explore"  # 'explore' or 'pathing'
        self.path: Optional[List[Tuple[int, int]]] = None
        self.current_waypoint_index: int = 0

        # 探索算法状态
        self.exploration_direction = 1
        self.step_down_distance = 40

        # 移除了pathfinding_toggle事件订阅，现在由MacroEngine直接控制
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

        LOG_INFO("[寻路管理器] 初始化完成")

    def _on_config_updated(self, skills_config, global_config):
        if getattr(self, "_cleanup_done", False):
            return
        raw_path_config = (
            global_config.get("pathfinding_config", {})
            if isinstance(global_config, dict)
            else {}
        )
        path_config = raw_path_config if isinstance(raw_path_config, dict) else {}
        area = path_config.get("minimap_area", [0, 0, 0, 0])
        valid_area = None
        if isinstance(area, (list, tuple)) and len(area) == 4:
            try:
                values = tuple(config_int(value) for value in area)
                if values[2] > 0 and values[3] > 0:
                    valid_area = values
            except ValueError:
                pass
        with self._run_lock:
            if valid_area is not None:
                self.minimap_capture_area = valid_area
                LOG_INFO(
                    f"[寻路管理器] 小地图区域已更新: {self.minimap_capture_area}"
                )
            else:
                self.minimap_capture_area = None

    def start(self):
        from ..utils.window_utils import WindowUtils

        window_config = getattr(
            self.border_manager, "window_activation_config", {}
        ) or {}
        if not WindowUtils.build_ahk_target(window_config):
            LOG_ERROR("[寻路管理器] 未配置显式目标窗口，拒绝启动坐标模式。")
            return False

        with self._run_lock:
            if self.is_running:
                return False
            if not self.minimap_capture_area:
                LOG_ERROR("[寻路管理器] 未配置有效的小地图区域，无法启动。")
                return False

            # 每代使用全新的停止/暂停事件。stop() 的 join 有超时，旧 worker 可能仍卡在
            # 捕获或 OpenCV 中；复用并 clear 同一个 Event 会让它在新一代重新运行。
            self._next_generation += 1
            run = _PathfindingRun(
                generation=self._next_generation,
                stop_event=threading.Event(),
                pause_event=threading.Event(),
            )

            # 全局地图设置。5000x5000 可覆盖约 625 个 200x200 小地图区域。
            map_size = self.GLOBAL_MAP_SIZE
            self.global_map = np.zeros((map_size, map_size), dtype=np.uint8)
            self.player_global_pos = [map_size // 2, map_size // 2]
            self.last_minimap_mask = None
            self.exploration_direction = 1
            self.mode = "explore"
            self.path = None
            self.current_waypoint_index = 0

            self.is_running = True
            self._active_run = run
            self._stop_event = run.stop_event
            self._pause_event = run.pause_event
            run.thread = threading.Thread(
                target=self._main_loop,
                args=(run,),
                name=f"PathfindingManager-{run.generation}",
                daemon=True,
            )
            self._thread = run.thread
            try:
                run.thread.start()
            except Exception as exc:
                run.stop_event.set()
                self.is_running = False
                self._active_run = None
                self._thread = None
                LOG_ERROR(f"[寻路管理器] 工作线程启动失败: {exc}")
                return False
        LOG_INFO("[寻路管理器] 自动寻路已启动")
        return True

    def stop(self):
        with self._run_lock:
            run = self._active_run
            if run is None:
                return False
            was_running = self.is_running
            self.is_running = False
            run.stop_event.set()

        thread = run.thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=self.STOP_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                LOG_ERROR(
                    f"[寻路管理器] 第 {run.generation} 代线程停止超时，"
                    "已隔离；迟到结果和点击都会被拒绝"
                )
        with self._run_lock:
            # Thread.start() 失败或测试替身没有真正启动线程时，不会进入
            # _main_loop/finally；仍需清掉本代句柄，保持 stop() 幂等。
            if self._active_run is run and (thread is None or not thread.is_alive()):
                self._active_run = None
                self._thread = None
        LOG_INFO("[寻路管理器] 自动寻路已停止")
        return was_running or bool(thread and thread.is_alive())

    def cleanup(self):
        """停止工作线程并对称解除全局 EventBus 订阅。"""
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        try:
            self.stop()
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 清理时停止失败: {e}")
        finally:
            event_bus.unsubscribe("engine:config_updated", self._on_config_updated)

    def pause(self):
        """暂停寻路执行"""
        with self._run_lock:
            run = self._active_run
            if not self.is_running or run is None:
                return False
            run.pause_event.set()
        LOG_INFO("[寻路管理器] 寻路已暂停")
        return True

    def resume(self):
        """恢复寻路执行"""
        with self._run_lock:
            run = self._active_run
            if not self.is_running or run is None:
                return False
            run.pause_event.clear()
        LOG_INFO("[寻路管理器] 寻路已恢复")
        return True

    def _is_current_run_locked(self, run: _PathfindingRun) -> bool:
        return (
            self._active_run is run
            and self.is_running
            and not run.stop_event.is_set()
        )

    def _is_current_run(self, run: _PathfindingRun) -> bool:
        with self._run_lock:
            return self._is_current_run_locked(run)

    def _is_runnable_run_locked(self, run: _PathfindingRun) -> bool:
        return self._is_current_run_locked(run) and not run.pause_event.is_set()

    def _is_runnable_run(self, run: _PathfindingRun) -> bool:
        with self._run_lock:
            return self._is_runnable_run_locked(run)

    def _wait_for_run(self, run: _PathfindingRun, seconds: float) -> bool:
        """可中断等待，并在返回时重新验证世代。"""
        run.stop_event.wait(seconds)
        return self._is_current_run(run)

    def _request_run_stop(self, run: _PathfindingRun):
        """worker 内部请求停止，不 join 自己，也不影响更新的世代。"""
        with self._run_lock:
            run.stop_event.set()
            if self._active_run is run:
                self.is_running = False

    def _main_loop(self, run: _PathfindingRun):
        try:
            while self._is_current_run(run):
                try:
                    if run.pause_event.is_set():
                        if not self._wait_for_run(run, 0.1):
                            break
                        continue

                    with self._run_lock:
                        ready = (
                            self._is_current_run_locked(run)
                            and self.minimap_capture_area is not None
                            and self.global_map is not None
                        )
                    if not ready:
                        if not self._wait_for_run(run, 0.1):
                            break
                        continue

                    # 捕获、图像处理和 A* 都可能阻塞。每个阻塞边界返回后必须重新核对
                    # run，旧世代不能读取新状态，更不能产生点击。
                    frame = self.border_manager.get_current_frame()
                    if not self._is_current_run(run):
                        break
                    if run.pause_event.is_set():
                        continue
                    if frame is None:
                        if not self._wait_for_run(run, 0.1):
                            break
                        continue

                    minimap_img, path_mask = self._process_map_image(frame)
                    if not self._is_current_run(run):
                        break
                    if run.pause_event.is_set():
                        continue
                    if minimap_img is None or path_mask is None:
                        continue

                    if not self._update_global_map(path_mask, run):
                        if self._is_current_run(run):
                            continue
                        break

                    with self._run_lock:
                        if not self._is_current_run_locked(run):
                            break
                        mode = self.mode

                    if mode == "explore":
                        target_pos_local = self._find_target(minimap_img)
                        if not self._is_current_run(run):
                            break
                        if run.pause_event.is_set():
                            continue
                        if target_pos_local:
                            LOG_INFO(
                                f"[寻路管理器] 在小地图 {target_pos_local} "
                                "发现目标，开始规划路径！"
                            )
                            if self._plan_path_to_target(target_pos_local, run):
                                with self._run_lock:
                                    if self._is_current_run_locked(run):
                                        self.mode = "pathing"
                        else:
                            self._execute_lawnmower_step(path_mask, run)
                    elif mode == "pathing":
                        self._execute_waypoint_following(run)

                    if not self._wait_for_run(run, 0.2):
                        break

                except Exception as e:
                    LOG_ERROR(f"[寻路管理器] 主循环发生异常: {e}")
                    self._request_run_stop(run)
                    break
        finally:
            with self._run_lock:
                # join 超时的旧 worker 迟到退出时，不得关闭或覆盖新世代。
                if self._active_run is run:
                    self.is_running = False
                    self._active_run = None
                    self._thread = None

    def _process_map_image(self, frame: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        # 热更新可能与 worker 并发；一次处理必须使用同一份完整区域快照，
        # 不能在 ``if`` 与解包之间被改成 None/另一份坐标。
        with self._run_lock:
            area = self.minimap_capture_area
        if not area:
            return None, None
        mx, my, mw, mh = area
        minimap_img = self.border_manager.get_region_from_frame(frame, mx, my, mw, mh)
        if minimap_img is None: return None, None
        path_mask = self._extract_path_mask(minimap_img)
        return minimap_img, path_mask

    def _update_global_map(
        self, path_mask: np.ndarray, run: _PathfindingRun
    ) -> bool:
        with self._run_lock:
            if not self._is_runnable_run_locked(run):
                return False
            previous_mask = self.last_minimap_mask

        displacement = None
        if previous_mask is not None:
            displacement = self._calculate_displacement(path_mask, previous_mask)

        # phaseCorrelate 可能较慢；结果提交和地图修改必须在同一个世代临界区内。
        with self._run_lock:
            if not self._is_runnable_run_locked(run):
                return False
            if displacement is not None:
                dx, dy = displacement
                # phaseCorrelate(current, previous) 返回 current→previous 的
                # 图像位移；地图相对角色左移时该值为正，正好就是角色的全局
                # 位移方向，因此这里应相加。round 避免 int 对负小数向 0 偏置。
                self.player_global_pos[0] += int(round(dx))
                self.player_global_pos[1] += int(round(dy))
                map_h, map_w = self.global_map.shape
                self.player_global_pos[0] = min(
                    max(self.player_global_pos[0], 0), map_w - 1
                )
                self.player_global_pos[1] = min(
                    max(self.player_global_pos[1], 0), map_h - 1
                )
            self._stitch_map(path_mask)
            self.last_minimap_mask = path_mask
            return True

    def _find_target(self, minimap_img: np.ndarray) -> Optional[Tuple[int, int]]:
        # 寻找亮蓝色目标 (Diablo IV 任务/传送门颜色)
        hsv = cv2.cvtColor(minimap_img, cv2.COLOR_BGR2HSV)
        lower_blue = np.array([100, 150, 150])
        upper_blue = np.array([130, 255, 255])
        mask = cv2.inRange(hsv, lower_blue, upper_blue)
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            c = max(contours, key=cv2.contourArea)
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                return (cx, cy)
        return None

    def _plan_path_to_target(
        self, target_local_pos: Tuple[int, int], run: _PathfindingRun
    ) -> bool:
        with self._run_lock:
            if (
                not self._is_runnable_run_locked(run)
                or not self.minimap_capture_area
                or self.global_map is None
            ):
                return False

            mw, mh = self.minimap_capture_area[2], self.minimap_capture_area[3]
            player_local_pos = (mw // 2, mh // 2)
            start_global = tuple(self.player_global_pos)
            end_global = (
                self.player_global_pos[0]
                - player_local_pos[0]
                + target_local_pos[0],
                self.player_global_pos[1]
                - player_local_pos[1]
                + target_local_pos[1],
            )
            # start() 为每一代创建新数组；保留本代引用，旧 worker 即使迟到也不会读取
            # 下一代刚初始化的地图。
            global_map = self.global_map

        map_h, map_w = global_map.shape
        if not (
            0 <= start_global[0] < map_w
            and 0 <= start_global[1] < map_h
            and 0 <= end_global[0] < map_w
            and 0 <= end_global[1] < map_h
        ):
            LOG_ERROR(
                f"[寻路管理器] A* 起终点超出全局地图: "
                f"start={start_global}, end={end_global}, size={map_w}x{map_h}"
            )
            return False

        # 目标来自当前小地图，没必要把 5000×5000 全局数组转换成 2500 万个
        # Python int（一次规划就可能占用数百 MB）。只截取包含起终点的有界窗口，
        # 周围保留至少一个小地图宽度供 A* 绕障；astar 可直接索引 ndarray 视图。
        margin = max(self.ASTAR_MIN_MARGIN, mw, mh)
        min_x = max(0, min(start_global[0], end_global[0]) - margin)
        min_y = max(0, min(start_global[1], end_global[1]) - margin)
        max_x = min(
            global_map.shape[1], max(start_global[0], end_global[0]) + margin + 1
        )
        max_y = min(
            global_map.shape[0], max(start_global[1], end_global[1]) + margin + 1
        )
        if min_x >= max_x or min_y >= max_y:
            LOG_ERROR("[寻路管理器] A* 起终点超出全局地图")
            return False

        local_map = global_map[min_y:max_y, min_x:max_x]
        start_local = (start_global[1] - min_y, start_global[0] - min_x)
        end_local = (end_global[1] - min_y, end_global[0] - min_x)
        LOG_INFO(
            f"[寻路管理器] A*规划路径: 从 {start_global} 到 {end_global}, "
            f"局部窗口={local_map.shape[1]}x{local_map.shape[0]}"
        )
        path = astar(local_map, start_local, end_local)

        with self._run_lock:
            if not self._is_runnable_run_locked(run):
                return False
            if path:
                # 将路径从 (y, x) 转回 (x, y)
                self.path = [(p[1] + min_x, p[0] + min_y) for p in path]
                self.current_waypoint_index = 0
                LOG_INFO(f"[寻路管理器] 路径规划成功，共 {len(self.path)} 个路点。")
                return True
            else:
                LOG_ERROR("[寻路管理器] A*未能找到路径！")
                self.mode = "explore"  # 找不到路，继续探索
                return False

    def _execute_lawnmower_step(
        self, path_mask: np.ndarray, run: _PathfindingRun
    ):
        with self._run_lock:
            if (
                not self._is_runnable_run_locked(run)
                or not self.minimap_capture_area
                or self.global_map is None
            ):
                return

            mw, mh = self.minimap_capture_area[2], self.minimap_capture_area[3]
            player_pos_local = (mw // 2, mh // 2)
            direction = self.exploration_direction
            probe_x_local = player_pos_local[0] + (direction * 25)
            probe_y_local = player_pos_local[1]
            probe_x_global = (
                self.player_global_pos[0] - player_pos_local[0] + probe_x_local
            )
            probe_y_global = (
                self.player_global_pos[1] - player_pos_local[1] + probe_y_local
            )
            blocked = (
                not (
                    0 <= probe_x_global < self.global_map.shape[1]
                    and 0 <= probe_y_global < self.global_map.shape[0]
                )
                or self.global_map[probe_y_global, probe_x_global] == 0
            )

        if blocked:
            if self._step_down(run):
                with self._run_lock:
                    if self._is_runnable_run_locked(run):
                        self.exploration_direction *= -1
        else:
            self._move_in_direction(direction, 0, run=run)

    def _execute_waypoint_following(self, run: _PathfindingRun):
        with self._run_lock:
            if not self._is_runnable_run_locked(run):
                return
            if not self.path or self.current_waypoint_index >= len(self.path):
                LOG_INFO("[寻路管理器] 路径已走完或无路径，寻路结束。")
                self._request_run_stop(run)
                return

            target_waypoint_global = self.path[self.current_waypoint_index]
            player_pos = tuple(self.player_global_pos)
            distance = np.linalg.norm(
                np.array(player_pos) - np.array(target_waypoint_global)
            )

            if distance < 15:
                LOG_INFO(
                    f"[寻路管理器] 到达路点 {self.current_waypoint_index}: "
                    f"{target_waypoint_global}"
                )
                self.current_waypoint_index += 1
                return

            move_vector = (
                target_waypoint_global[0] - player_pos[0],
                target_waypoint_global[1] - player_pos[1],
            )

        self._move_in_direction(move_vector[0], move_vector[1], run=run)

    def _step_down(self, run: _PathfindingRun) -> bool:
        if not self._move_in_direction(0, 1, duration_ms=500, run=run):
            return False
        return self._wait_for_run(run, 0.5)

    def _resolve_target_client_rect(self) -> Optional[Tuple[int, int, int, int]]:
        """返回目标窗口客户区的屏幕坐标，并拒绝向后台窗口做物理点击。"""
        try:
            import win32gui
            from ..utils.window_utils import WindowUtils

            window_config = getattr(
                self.border_manager, "window_activation_config", {}
            )
            ahk_class, ahk_exe = WindowUtils.normalize_target_config(window_config)
            if not ahk_class and not ahk_exe:
                LOG_ERROR("[寻路管理器] 未配置目标窗口，拒绝点击")
                return None

            # 物理鼠标点击绝不能沿用 BorderFrameManager 为预览准备的“前台窗口”
            # fallback；配置目标不存在时必须失败关闭，否则会点击当前其他应用。
            hwnd = WindowUtils.find_target_window(window_config)
            if not hwnd or not win32gui.IsWindow(hwnd):
                LOG_ERROR("[寻路管理器] 目标窗口无效，拒绝点击")
                return None
            if win32gui.GetForegroundWindow() != hwnd:
                LOG("[寻路管理器] 目标窗口不在前台，跳过本次移动点击")
                return None

            client_left, client_top, client_right, client_bottom = (
                win32gui.GetClientRect(hwnd)
            )
            left, top = win32gui.ClientToScreen(hwnd, (client_left, client_top))
            right, bottom = win32gui.ClientToScreen(
                hwnd, (client_right, client_bottom)
            )
            rect = (int(left), int(top), int(right), int(bottom))
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                LOG_ERROR(f"[寻路管理器] 目标客户区无效 {rect}，拒绝点击")
                return None
            return rect
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 解析目标客户区失败，拒绝点击: {e}")
            return None

    @classmethod
    def _calculate_movement_click(
        cls,
        dx: float,
        dy: float,
        client_rect: Tuple[int, int, int, int],
    ) -> Optional[Tuple[int, int]]:
        """在目标客户区内计算移动点击点；窄窗口会自动缩短半径。"""
        norm = float(np.linalg.norm([dx, dy]))
        if not np.isfinite(norm) or norm <= 0:
            return None

        left, top, right, bottom = client_rect
        if right <= left or bottom <= top:
            return None

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        unit_x = float(dx) / norm
        unit_y = float(dy) / norm

        # 右/下是半开边界，留 1px 安全边距。点击半径最大 150px；客户区较小时
        # 按方向缩短，而不是把点 clamp 到边缘后改变运动方向。
        max_distance = cls.MOVEMENT_CLICK_DISTANCE
        if unit_x > 0:
            max_distance = min(max_distance, (right - 1 - center_x) / unit_x)
        elif unit_x < 0:
            max_distance = min(max_distance, (left + 1 - center_x) / unit_x)
        if unit_y > 0:
            max_distance = min(max_distance, (bottom - 1 - center_y) / unit_y)
        elif unit_y < 0:
            max_distance = min(max_distance, (top + 1 - center_y) / unit_y)

        if not np.isfinite(max_distance) or max_distance <= 0:
            return None

        click_x = int(round(center_x + unit_x * max_distance))
        click_y = int(round(center_y + unit_y * max_distance))
        if not (left <= click_x < right and top <= click_y < bottom):
            return None
        return click_x, click_y

    def _move_in_direction(
        self,
        dx: float,
        dy: float,
        duration_ms: int = 100,
        run: Optional[_PathfindingRun] = None,
    ) -> bool:
        with self._run_lock:
            current_run = run or self._active_run
            if current_run is None or not self._is_runnable_run_locked(current_run):
                return False

            client_rect = self._resolve_target_client_rect()
            if client_rect is None:
                return False
            click_pos = self._calculate_movement_click(dx, dy, client_rect)
            if click_pos is None:
                return False

            # 世代验证与发送在同一个临界区中线性化：stop() 一旦返回并关闭本代，
            # 迟到 worker 就不可能越过这里给新一代补发点击。
            sent = self.input_handler.click_mouse_at(
                click_pos[0], click_pos[1], hold_time=duration_ms
            )
            return sent is not False

    def _extract_path_mask(self, minimap_img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(minimap_img, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        return mask

    def _calculate_displacement(
        self, img1: np.ndarray, img2: np.ndarray
    ) -> Optional[Tuple[float, float]]:
        try:
            img1_float = np.float32(img1)
            img2_float = np.float32(img2)
            displacement, response = cv2.phaseCorrelate(img1_float, img2_float)
            dx, dy = float(displacement[0]), float(displacement[1])
            max_shift = min(img1.shape[:2]) * self.MAX_PHASE_SHIFT_RATIO
            if (
                not np.isfinite(dx)
                or not np.isfinite(dy)
                or not np.isfinite(response)
                or response < self.MIN_PHASE_CORRELATION_RESPONSE
                or abs(dx) > max_shift
                or abs(dy) > max_shift
            ):
                LOG(
                    f"[寻路管理器] 忽略不可信位移: "
                    f"dx={dx:.2f}, dy={dy:.2f}, response={response:.3f}"
                )
                return None
            return dx, dy
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 位移计算失败: {e}")
            return None

    def _stitch_map(self, minimap_mask: np.ndarray):
        try:
            if self.global_map is None:
                return

            mask_h, mask_w = minimap_mask.shape
            map_h, map_w = self.global_map.shape
            top_left_x = self.player_global_pos[0] - mask_w // 2
            top_left_y = self.player_global_pos[1] - mask_h // 2

            dst_x1 = max(0, top_left_x)
            dst_y1 = max(0, top_left_y)
            dst_x2 = min(map_w, top_left_x + mask_w)
            dst_y2 = min(map_h, top_left_y + mask_h)
            if dst_x1 >= dst_x2 or dst_y1 >= dst_y2:
                return

            src_x1 = dst_x1 - top_left_x
            src_y1 = dst_y1 - top_left_y
            src_x2 = src_x1 + (dst_x2 - dst_x1)
            src_y2 = src_y1 + (dst_y2 - dst_y1)
            roi = self.global_map[dst_y1:dst_y2, dst_x1:dst_x2]
            source = minimap_mask[src_y1:src_y2, src_x1:src_x2]
            self.global_map[dst_y1:dst_y2, dst_x1:dst_x2] = cv2.add(
                roi, source
            )
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 地图拼接失败: {e}")
