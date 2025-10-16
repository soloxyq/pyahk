"""
自动寻路管理器 (Pathfinding Manager)

负责处理所有与自动寻路相关的功能，包括：
- 地图数据管理 (拼接、栅格化)
- 路径规划 (A* 算法)
- 角色移动执行
"""

import threading
import time
from typing import List, Dict, Any, Optional, Tuple

import cv2
import numpy as np

from .event_bus import event_bus
from .ahk_input_handler import AHKInputHandler
from ..utils.border_frame_manager import BorderFrameManager
from ..utils.debug_log import LOG_INFO, LOG_ERROR, LOG
from ..utils.a_star import astar


class PathfindingManager:
    """管理自动寻路的状态和流程"""

    def __init__(self, border_manager: BorderFrameManager, input_handler: AHKInputHandler):
        self.border_manager = border_manager
        self.input_handler = input_handler
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # 新增：暂停事件

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
        path_config = global_config.get("pathfinding_config", {})
        area = path_config.get("minimap_area", [0, 0, 0, 0])
        if len(area) == 4 and area[2] > 0 and area[3] > 0:
            self.minimap_capture_area = tuple(area)
            LOG_INFO(f"[寻路管理器] 小地图区域已更新: {self.minimap_capture_area}")
        else:
            self.minimap_capture_area = None

    def start(self):
        if self.is_running: return
        if not self.minimap_capture_area:
            LOG_ERROR("[寻路管理器] 未配置有效的小地图区域，无法启动。")
            return

        # 全局地图设置
        # 5000x5000地图可覆盖约625个小地图区域（25x25），对大多数游戏场景足够
        # 小地图通常只占屏幕右上角一小块区域（约200x200像素），不是整个1920x1080画面
        # 因此5000x5000足够覆盖25个全屏幕宽度的移动范围
        self.global_map = np.zeros((5000, 5000), dtype=np.uint8)
        self.player_global_pos = [2500, 2500]  # 玩家起始位置在地图中心
        self.last_minimap_mask = None
        self.exploration_direction = 1
        self.mode = "explore"
        self.path = None
        self.current_waypoint_index = 0

        self.is_running = True
        self._stop_event.clear()
        self._pause_event.clear()  # 初始化为非暂停状态
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        LOG_INFO("[寻路管理器] 自动寻路已启动")

    def stop(self):
        if not self.is_running: return
        self.is_running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        LOG_INFO("[寻路管理器] 自动寻路已停止")

    def pause(self):
        """暂停寻路执行"""
        if self.is_running:
            self._pause_event.set()
            LOG_INFO("[寻路管理器] 寻路已暂停")

    def resume(self):
        """恢复寻路执行"""
        if self.is_running:
            self._pause_event.clear()
            LOG_INFO("[寻路管理器] 寻路已恢复")

    def _main_loop(self):
        while not self._stop_event.is_set():
            try:
                # 检查是否被暂停
                if self._pause_event.is_set():
                    time.sleep(0.1)
                    continue

                # 检查必要条件
                if not self.minimap_capture_area or self.global_map is None:
                    time.sleep(0.1)
                    continue

                frame = self.border_manager.get_current_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue

                minimap_img, path_mask = self._process_map_image(frame)
                if minimap_img is None or path_mask is None: continue

                self._update_global_map(path_mask)

                if self.mode == "explore":
                    target_pos_local = self._find_target(minimap_img)
                    if target_pos_local:
                        LOG_INFO(f"[寻路管理器] 在小地图 {target_pos_local} 发现目标，开始规划路径！")
                        self._plan_path_to_target(target_pos_local)
                        self.mode = "pathing"
                    else:
                        self._execute_lawnmower_step(path_mask)

                elif self.mode == "pathing":
                    self._execute_waypoint_following()

                time.sleep(0.2)

            except Exception as e:
                LOG_ERROR(f"[寻路管理器] 主循环发生异常: {e}")
                self.stop()

    def _process_map_image(self, frame: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if not self.minimap_capture_area:
            return None, None
        mx, my, mw, mh = self.minimap_capture_area
        minimap_img = self.border_manager.get_region_from_frame(frame, mx, my, mw, mh)
        if minimap_img is None: return None, None
        path_mask = self._extract_path_mask(minimap_img)
        return minimap_img, path_mask

    def _update_global_map(self, path_mask: np.ndarray):
        if self.last_minimap_mask is not None:
            dx, dy = self._calculate_displacement(path_mask, self.last_minimap_mask)
            self.player_global_pos[0] -= int(dx)
            self.player_global_pos[1] -= int(dy)
        self._stitch_map(path_mask)
        self.last_minimap_mask = path_mask

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

    def _plan_path_to_target(self, target_local_pos: Tuple[int, int]):
        if not self.minimap_capture_area or self.global_map is None:
            return

        mw, mh = self.minimap_capture_area[2], self.minimap_capture_area[3]
        player_local_pos = (mw // 2, mh // 2)

        start_global = tuple(self.player_global_pos)
        end_global = (
            self.player_global_pos[0] - player_local_pos[0] + target_local_pos[0],
            self.player_global_pos[1] - player_local_pos[1] + target_local_pos[1]
        )

        LOG_INFO(f"[寻路管理器] A*规划路径: 从 {start_global} 到 {end_global}")
        # 注意：A*算法的输入是 (row, col)，即 (y, x)
        # 将numpy数组转换为列表格式供A*算法使用
        maze_list = self.global_map.tolist()
        path = astar(maze_list, (start_global[1], start_global[0]), (end_global[1], end_global[0]))

        if path:
            # 将路径从 (y, x) 转回 (x, y)
            self.path = [(p[1], p[0]) for p in path]
            self.current_waypoint_index = 0
            LOG_INFO(f"[寻路管理器] 路径规划成功，共 {len(self.path)} 个路点。")
        else:
            LOG_ERROR("[寻路管理器] A*未能找到路径！")
            self.mode = "explore" # 找不到路，继续探索

    def _execute_lawnmower_step(self, path_mask: np.ndarray):
        if not self.minimap_capture_area or self.global_map is None:
            return

        mw, mh = self.minimap_capture_area[2], self.minimap_capture_area[3]
        player_pos_local = (mw // 2, mh // 2)
        probe_x_local = player_pos_local[0] + (self.exploration_direction * 25)
        probe_y_local = player_pos_local[1]
        probe_x_global = self.player_global_pos[0] - player_pos_local[0] + probe_x_local
        probe_y_global = self.player_global_pos[1] - player_pos_local[1] + probe_y_local

        if not (0 <= probe_x_global < self.global_map.shape[1] and 0 <= probe_y_global < self.global_map.shape[0]) or \
           self.global_map[probe_y_global, probe_x_global] == 0:
            self._step_down()
            self.exploration_direction *= -1
        else:
            self._move_in_direction(self.exploration_direction, 0)

    def _execute_waypoint_following(self):
        if not self.path or self.current_waypoint_index >= len(self.path):
            LOG_INFO("[寻路管理器] 路径已走完或无路径，寻路结束。")
            self.stop()
            return

        target_waypoint_global = self.path[self.current_waypoint_index]
        distance = np.linalg.norm(np.array(self.player_global_pos) - np.array(target_waypoint_global))

        if distance < 15:
            LOG_INFO(f"[寻路管理器] 到达路点 {self.current_waypoint_index}: {target_waypoint_global}")
            self.current_waypoint_index += 1
            return

        move_vector = (target_waypoint_global[0] - self.player_global_pos[0], target_waypoint_global[1] - self.player_global_pos[1])
        self._move_in_direction(move_vector[0], move_vector[1])

    def _step_down(self):
        self._move_in_direction(0, 1, duration_ms=500)
        time.sleep(0.5)

    def _move_in_direction(self, dx: float, dy: float, duration_ms: int = 100):
        screen_center_x, screen_center_y = 960, 540
        click_distance = 150
        norm = np.linalg.norm([dx, dy])
        if norm > 0:
            click_offset = (np.array([dx, dy]) / norm) * click_distance
            click_pos = (int(screen_center_x + click_offset[0]), int(screen_center_y + click_offset[1]))
            self.input_handler.click_mouse_at(click_pos[0], click_pos[1], hold_time=duration_ms)

    def _extract_path_mask(self, minimap_img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(minimap_img, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        return mask

    def _calculate_displacement(self, img1: np.ndarray, img2: np.ndarray) -> Tuple[float, float]:
        try:
            img1_float = np.float32(img1)
            img2_float = np.float32(img2)
            displacement, _ = cv2.phaseCorrelate(img1_float, img2_float)
            return displacement[0], displacement[1]
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 位移计算失败: {e}")
            return 0.0, 0.0

    def _stitch_map(self, minimap_mask: np.ndarray):
        try:
            if self.global_map is None:
                return

            h, w = minimap_mask.shape
            map_h, map_w = self.global_map.shape
            top_left_x = self.player_global_pos[0] - w // 2
            top_left_y = self.player_global_pos[1] - h // 2

            # 确保坐标在有效范围内
            if top_left_x < 0:
                top_left_x = 0
            if top_left_y < 0:
                top_left_y = 0
            if top_left_x + w > map_w:
                w = map_w - top_left_x
            if top_left_y + h > map_h:
                h = map_h - top_left_y

            # 再次检查边界
            if w <= 0 or h <= 0 or not (0 <= top_left_x < map_w and 0 <= top_left_y < map_h):
                return

            roi = self.global_map[top_left_y:top_left_y+h, top_left_x:top_left_x+w]
            self.global_map[top_left_y:top_left_y+h, top_left_x:top_left_x+w] = cv2.add(roi, minimap_mask)
        except Exception as e:
            LOG_ERROR(f"[寻路管理器] 地图拼接失败: {e}")