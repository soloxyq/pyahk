"""资源管理器 - 被动式资源检测模块"""

import time
from typing import Dict, Any, Optional, Tuple
import numpy as np

from ..utils.border_frame_manager import BorderFrameManager
from .input_handler import InputHandler
from ..utils.debug_log import LOG_INFO, LOG_ERROR


class ResourceManager:
    """被动式资源管理器 - 只提供检测功能，不独立运行"""

    def __init__(self, border_manager: BorderFrameManager, input_handler: InputHandler, debug_display_manager=None):
        self.border_frame_manager = border_manager
        self.input_handler = input_handler
        self.debug_display_manager = debug_display_manager

        # 资源配置
        self.hp_config: Dict[str, Any] = {}
        self.mp_config: Dict[str, Any] = {}
        self.check_interval: int = 200

        # 内部冷却管理
        self._flask_cooldowns: Dict[str, float] = {}

        # 状态管理
        self._is_running = False
        self._is_paused = False

        # 模板HSV数据存储（每个像素的HSV值）
        self.hp_template_hsv = None
        self.mp_template_hsv = None

    def update_config(self, resource_config: Dict[str, Any]):
        """更新资源配置"""
        self.hp_config = resource_config.get("hp_config", {})
        self.mp_config = resource_config.get("mp_config", {})
        self.check_interval = resource_config.get("check_interval", 200)
        LOG_INFO(f"[ResourceManager] 配置已更新 - HP: {self.hp_config.get('enabled', False)}, MP: {self.mp_config.get('enabled', False)}")

    def check_and_execute_resources(self, cached_frame: Optional[np.ndarray] = None) -> bool:
        """
        检查并执行资源管理（被动调用）

        Args:
            cached_frame: 缓存的屏幕帧数据

        Returns:
            bool: 是否执行了任何资源操作
        """
        if not self._is_running or self._is_paused:
            return False

        executed = False

        # 检查HP
        if self.hp_config.get("enabled", False):
            if self._should_use_hp_resource(cached_frame):
                self._execute_resource("hp", self.hp_config)
                executed = True

        # 检查MP
        if self.mp_config.get("enabled", False):
            if self._should_use_mp_resource(cached_frame):
                self._execute_resource("mp", self.mp_config)
                executed = True

        return executed

    def _should_use_hp_resource(self, cached_frame: Optional[np.ndarray]) -> bool:
        """判断是否应该使用HP资源"""
        if not self.hp_config.get("enabled", False):
            return False

        # 检查内部冷却
        if not self._check_internal_cooldown("hp"):
            return False

        # 检查HP是否低于阈值
        return self._is_resource_low("hp", cached_frame)

    def _should_use_mp_resource(self, cached_frame: Optional[np.ndarray]) -> bool:
        """判断是否应该使用MP资源"""
        if not self.mp_config.get("enabled", False):
            return False

        # 检查内部冷却
        if not self._check_internal_cooldown("mp"):
            return False

        # 检查MP是否低于阈值
        return self._is_resource_low("mp", cached_frame)

    def _check_internal_cooldown(self, resource_type: str) -> bool:
        """检查内部冷却是否就绪"""
        config = self.hp_config if resource_type == "hp" else self.mp_config
        cooldown_ms = config.get("cooldown", 5000)
        cooldown_seconds = cooldown_ms / 1000.0

        current_time = time.time()
        last_press_time = self._flask_cooldowns.get(resource_type, 0)

        return current_time - last_press_time >= cooldown_seconds

    def _is_resource_low(self, resource_type: str, cached_frame: Optional[np.ndarray]) -> bool:
        """检查资源是否低于阈值（使用统一的百分比检测接口）"""
        config = self.hp_config if resource_type == "hp" else self.mp_config

        # 获取检测参数
        threshold = config.get("threshold", 50)
        match_percentage = 100.0

        try:
            # 根据检测模式选择检测方法
            detection_mode = config.get("detection_mode", "rectangle")

            if detection_mode == "circle":
                center_x, center_y, radius = config.get("center_x"), config.get("center_y"), config.get("radius")
                LOG_INFO(f"[DEBUG] Circle coords: x={center_x}, y={center_y}, r={radius}, types: {type(center_x)}, {type(center_y)}, {type(radius)}")
                if center_x is None or center_y is None or radius is None:
                    raise ValueError(f"{resource_type.upper()} 圆形检测配置不完整")
                
                # 修复数组比较问题：明确检查帧数据
                if cached_frame is None:
                    frame = self.border_frame_manager.get_current_frame()
                else:
                    frame = cached_frame
                    
                if frame is None: 
                    raise ValueError("无法获取帧数据")

                match_percentage = self.border_frame_manager.compare_resource_circle(
                    frame, center_x, center_y, radius, resource_type, threshold
                )
            else:  # rectangle
                x1, y1, x2, y2 = config.get("region_x1", 0), config.get("region_y1", 0), config.get("region_x2", 0), config.get("region_y2", 0)
                LOG_INFO(f"[DEBUG] Rect coords: x1={x1}, y1={y1}, x2={x2}, y2={y2}, types: {type(x1)}, {type(y1)}, {type(x2)}, {type(y2)}")
                if not (x1 < x2 and y1 < y2):
                    raise ValueError(f"{resource_type.upper()} 未配置有效检测区域")

                # 修复数组比较问题：明确检查帧数据
                if cached_frame is None:
                    frame = self.border_frame_manager.get_current_frame()
                else:
                    frame = cached_frame
                    
                if frame is None: 
                    raise ValueError("无法获取帧数据")

                region_name = f"{resource_type}_region"
                width, height = x2 - x1, y2 - y1
                match_percentage = self.border_frame_manager._compare_resource_hsv(
                    frame, x1, y1, max(width, height), region_name, threshold
                )
        except Exception as e:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 检测失败: {e}")
            import traceback
            LOG_ERROR(f"[ResourceManager] 详细错误信息: {traceback.format_exc()}")
            # 发生错误时，不触发资源补充，并报告100%以避免误触发
            match_percentage = 100.0

        # 无论成功与否，都向Debug Manager报告最新状态
        if self.debug_display_manager:
            if resource_type == 'hp':
                self.debug_display_manager.update_health(match_percentage)
            elif resource_type == 'mp':
                self.debug_display_manager.update_mana(match_percentage)

        # 判断是否需要补充资源（百分比低于阈值）
        # 使用明确的比较避免数组比较错误
        needs_resource = bool(match_percentage < threshold) if isinstance(match_percentage, (int, float)) else False
        return needs_resource

    def capture_template_hsv(self, frame: np.ndarray):
        """在F8准备阶段截取并保存模板区域的HSV数据"""
        if frame is None:
            LOG_ERROR("[ResourceManager] 无法获取帧数据用于模板截取")
            return

        try:
            import cv2

            # 截取HP区域模板
            if self.hp_config.get("enabled", False):
                hp_region = self._get_region_from_config(self.hp_config)
                if hp_region:
                    x1, y1, x2, y2 = hp_region
                    if (0 <= x1 < x2 <= frame.shape[1] and
                        0 <= y1 < y2 <= frame.shape[0]):
                        hp_region_img = frame[y1:y2, x1:x2]
                        # 转换为HSV并保存
                        if hp_region_img.shape[2] == 4:  # BGRA
                            hp_region_img = cv2.cvtColor(hp_region_img, cv2.COLOR_BGRA2BGR)
                        hp_hsv = cv2.cvtColor(hp_region_img, cv2.COLOR_BGR2HSV)
                        self.hp_template_hsv = hp_hsv.copy()
                        LOG_INFO(f"[ResourceManager] 已保存HP模板HSV数据，尺寸: {hp_hsv.shape}")

            # 截取MP区域模板
            if self.mp_config.get("enabled", False):
                mp_region = self._get_region_from_config(self.mp_config)
                if mp_region:
                    x1, y1, x2, y2 = mp_region
                    if (0 <= x1 < x2 <= frame.shape[1] and
                        0 <= y1 < y2 <= frame.shape[0]):
                        mp_region_img = frame[y1:y2, x1:x2]
                        # 转换为HSV并保存
                        if mp_region_img.shape[2] == 4:  # BGRA
                            mp_region_img = cv2.cvtColor(mp_region_img, cv2.COLOR_BGRA2BGR)
                        mp_hsv = cv2.cvtColor(mp_region_img, cv2.COLOR_BGR2HSV)
                        self.mp_template_hsv = mp_hsv.copy()
                        LOG_INFO(f"[ResourceManager] 已保存MP模板HSV数据，尺寸: {mp_hsv.shape}")

        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 模板HSV数据截取失败: {e}")

    def _get_region_from_config(self, config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """从配置中获取区域坐标"""
        try:
            x1 = config.get("region_x1", 0)
            y1 = config.get("region_y1", 0)
            x2 = config.get("region_x2", 0)
            y2 = config.get("region_y2", 0)

            if x1 < x2 and y1 < y2:
                return (x1, y1, x2, y2)
            else:
                LOG_ERROR(f"[ResourceManager] 无效的区域坐标: ({x1},{y1}) -> ({x2},{y2})")
                return None
        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 获取区域坐标失败: {e}")
            return None

    def _create_color_mask(self, hsv_region: np.ndarray, color_profile: Dict[str, Any]) -> np.ndarray:
        """为单个颜色配置创建mask (保留兼容性)"""
        import cv2

        target_h = color_profile.get("target_h", 0)
        target_s = color_profile.get("target_s", 75)
        target_v = color_profile.get("target_v", 29)
        tolerance_h = color_profile.get("tolerance_h", 10)
        tolerance_s = color_profile.get("tolerance_s", 20)
        tolerance_v = color_profile.get("tolerance_v", 20)

        # 将Qt的H值(0-359)转换为OpenCV的H值(0-179)
        opencv_h = int(target_h / 2) if target_h > 0 else 0
        opencv_h_tolerance = int(tolerance_h / 2)

        lower_bound = np.array([
            max(0, opencv_h - opencv_h_tolerance),
            max(0, target_s - tolerance_s),
            max(0, target_v - tolerance_v)
        ], dtype=np.uint8)

        upper_bound = np.array([
            min(179, opencv_h + opencv_h_tolerance),
            min(255, target_s + tolerance_s),
            min(255, target_v + tolerance_v)
        ], dtype=np.uint8)

        mask = cv2.inRange(hsv_region, lower_bound, upper_bound)

        LOG_INFO(f"[ResourceManager] {color_profile.get('name', 'Unknown')}颜色 - HSV: H={opencv_h}±{opencv_h_tolerance}, S={target_s}±{tolerance_s}, V={target_v}±{tolerance_v}")

        return mask

    def _execute_resource(self, resource_type: str, config: Dict[str, Any]):
        """执行资源操作"""
        key = config.get("key", "1" if resource_type == "hp" else "2")

        # 执行按键
        self.input_handler.execute_key(key)

        # 记录按键时间
        self._flask_cooldowns[resource_type] = time.time()

        LOG_INFO(f"[ResourceManager] 已执行{resource_type.upper()}资源 - 按键: {key}")

    def clear_cooldowns(self):
        """清理所有冷却时间戳（用于重置）"""
        self._flask_cooldowns.clear()
        LOG_INFO("[ResourceManager] 冷却时间戳已清理")

    def get_current_resource_percentage(self, resource_type: str, cached_frame: Optional[np.ndarray] = None) -> float:
        """获取当前资源百分比，用于OSD显示（使用统一的检测接口）"""
        if resource_type not in ["hp", "mp"]:
            return 100.0

        config = self.hp_config if resource_type == "hp" else self.mp_config

        if not config.get("enabled", False):
            return 100.0

        # 根据检测模式选择检测方法
        detection_mode = config.get("detection_mode", "rectangle")

        if detection_mode == "circle":
            # 使用圆形检测
            center_x = config.get("center_x")
            center_y = config.get("center_y")
            radius = config.get("radius")

            if center_x is None or center_y is None or radius is None:
                return 100.0

            frame = cached_frame
            if frame is None:
                try:
                    frame = self.border_frame_manager.get_current_frame()
                except:
                    return 100.0

            if frame is None:
                return 100.0

            # 使用圆形检测接口
            match_percentage = self.border_frame_manager.compare_resource_circle(
                frame, center_x, center_y, radius, resource_type, 0.0
            )
        else:
            # 使用矩形检测
            region_x1 = config.get("region_x1", 0)
            region_y1 = config.get("region_y1", 0)
            region_x2 = config.get("region_x2", 0)
            region_y2 = config.get("region_y2", 0)

            if region_x1 == 0 or region_y1 == 0 or region_x2 == 0 or region_y2 == 0:
                return 100.0

            # 确保有帧数据
            frame = cached_frame
            if frame is None:
                try:
                    frame = self.border_frame_manager.get_current_frame()
                except:
                    return 100.0

            if frame is None:
                return 100.0

            # 使用矩形资源检测接口获取精确百分比
            region_name = f"{resource_type}_region"
            region_width = region_x2 - region_x1
            region_height = region_y2 - region_y1

            # 调用矩形资源检测接口，返回匹配百分比
            match_percentage = self.border_frame_manager._compare_resource_hsv(
                frame, region_x1, region_y1, max(region_width, region_height), region_name, 0.0
            )

        # 确保返回值是数值类型
        if isinstance(match_percentage, (int, float)):
            return float(match_percentage)
        else:
            return 100.0

    def get_status(self) -> Dict[str, Any]:
        """获取状态信息"""
        return {
            "hp_enabled": self.hp_config.get("enabled", False),
            "mp_enabled": self.mp_config.get("enabled", False),
            "check_interval": self.check_interval,
            "hp_cooldown_remaining": self._get_cooldown_remaining("hp"),
            "mp_cooldown_remaining": self._get_cooldown_remaining("mp"),
        }

    def _get_cooldown_remaining(self, resource_type: str) -> float:
        """获取剩余冷却时间（秒）"""
        config = self.hp_config if resource_type == "hp" else self.mp_config
        cooldown_ms = config.get("cooldown", 5000)
        cooldown_seconds = cooldown_ms / 1000.0

        current_time = time.time()
        last_press_time = self._flask_cooldowns.get(resource_type, 0)

        remaining = cooldown_seconds - (current_time - last_press_time)
        return max(0.0, remaining)

    def start(self):
        """启动资源管理器"""
        if not self._is_running:
            self._is_running = True
            self._is_paused = False
            LOG_INFO("[ResourceManager] 已启动")

    def stop(self):
        """停止资源管理器"""
        if self._is_running:
            self._is_running = False
            self._is_paused = False
            LOG_INFO("[ResourceManager] 已停止")

    def pause(self):
        """暂停资源管理器"""
        if self._is_running and not self._is_paused:
            self._is_paused = True
            LOG_INFO("[ResourceManager] 已暂停")

    def resume(self):
        """恢复资源管理器"""
        if self._is_running and self._is_paused:
            self._is_paused = False
            LOG_INFO("[ResourceManager] 已恢复")

    def auto_detect_orbs(self, orb_type: str) -> Dict[str, Dict[str, Any]]:
        """
        根据指定的球体类型（'hp'或'mp'），在屏幕的特定角落区域内自动检测球体。

        Args:
            orb_type (str): 要检测的球体类型，'hp' 或 'mp'。

        Returns:
            Dict[str, Dict[str, Any]]: 检测结果，只包含指定类型的球体信息。
        """
        try:
            import cv2
            import numpy as np

            frame = self.border_frame_manager.capture_target_window_frame()
            if frame is None:
                LOG_ERROR("[ResourceManager] 无法截取图像用于圆形检测")
                return {}

            h, w = frame.shape[:2]
            roi_size = 400  # 定义我们关心的角落区域大小

            if orb_type == 'hp':
                # 左下角区域
                roi = frame[h - roi_size:h, 0:roi_size]
                offset_x, offset_y = 0, h - roi_size
            elif orb_type == 'mp':
                # 右下角区域
                roi = frame[h - roi_size:h, w - roi_size:w]
                offset_x, offset_y = w - roi_size, h - roi_size
            else:
                LOG_ERROR(f"[ResourceManager] 无效的球体类型: {orb_type}")
                return {}

            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray_blurred = cv2.GaussianBlur(gray, (9, 9), 2)

            circles = cv2.HoughCircles(
                gray_blurred,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=500,
                param1=50,
                param2=40,
                minRadius=75,
                maxRadius=95
            )

            if circles is None:
                LOG_ERROR(f"[ResourceManager] 在 {orb_type} 区域未检测到任何圆形")
                return {}

            detected_circles = circles[0]
            LOG_INFO(f"[ResourceManager] 在 {orb_type} 区域检测到 {len(detected_circles)} 个圆形")

            # 在小区域内，我们通常只需要找到最清晰的那个圆即可
            # 这里我们假设第一个被找到的圆就是目标
            target_circle = detected_circles[0]
            
            # 将ROI内的相对坐标转换回全屏绝对坐标
            roi_cx, roi_cy, roi_r = target_circle
            abs_cx = int(roi_cx + offset_x)
            abs_cy = int(roi_cy + offset_y)
            abs_r = int(roi_r)

            result = {
                orb_type: {
                    "center_x": abs_cx,
                    "center_y": abs_cy,
                    "radius": abs_r
                }
            }
            LOG_INFO(f"[ResourceManager] {orb_type.upper()} 球体检测完成: 绝对坐标(圆心({abs_cx}, {abs_cy}), 半径{abs_r})")
            
            return result

        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 自动检测球体失败: {e}")
            import traceback
            LOG_ERROR(traceback.format_exc())
            return {}

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._is_running and not self._is_paused