"""资源管理器 - 被动式资源检测模块"""

import time
from typing import Dict, Any, Optional, Tuple
import numpy as np

from ..utils.border_frame_manager import BorderFrameManager
from .input_handler import InputHandler
from ..utils.debug_log import LOG_INFO, LOG_ERROR


class ResourceManager:
    """被动式资源管理器 - 只提供检测功能，不独立运行"""

    def __init__(self, border_manager: BorderFrameManager, input_handler: InputHandler):
        self.border_frame_manager = border_manager
        self.input_handler = input_handler

        # 资源配置
        self.hp_config: Dict[str, Any] = {}
        self.mp_config: Dict[str, Any] = {}
        self.check_interval: int = 200

        # 内部冷却管理
        self._flask_cooldowns: Dict[str, float] = {}

        # 状态管理
        self._is_running = False
        self._is_paused = False

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
            LOG_INFO(f"[ResourceManager] 检查HP资源 - 配置: {self.hp_config}")
            if self._should_use_hp_resource(cached_frame):
                self._execute_resource("hp", self.hp_config)
                executed = True

        # 检查MP
        if self.mp_config.get("enabled", False):
            LOG_INFO(f"[ResourceManager] 检查MP资源 - 配置: {self.mp_config}")
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
        """检查资源是否低于阈值"""
        config = self.hp_config if resource_type == "hp" else self.mp_config

        # 获取检测参数
        threshold = config.get("threshold", 50)
        
        # 检查是否配置了区域
        region_x1 = config.get("region_x1", 0)
        region_y1 = config.get("region_y1", 0)
        region_x2 = config.get("region_x2", 0)
        region_y2 = config.get("region_y2", 0)

        if region_x1 == 0 or region_y1 == 0 or region_x2 == 0 or region_y2 == 0:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 未配置检测区域")
            return False  # 未配置区域

        # 计算资源百分比
        resource_percentage = self._calculate_region_resource_percentage(
            cached_frame, region_x1, region_y1, region_x2, region_y2, config
        )

        # 判断是否需要补充
        needs_resource = resource_percentage < threshold

        if needs_resource:
            LOG_INFO(f"[ResourceManager] {resource_type.upper()} 资源不足 - 当前: {resource_percentage:.1f}%, 阈值: {threshold}%")

        return needs_resource



    def _calculate_region_resource_percentage(
        self, frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, config: Dict[str, Any]
    ) -> float:
        """计算区域内的资源百分比（支持多颜色检测）"""
        if frame is None:
            return 100.0

        try:
            # 确保坐标在图像范围内
            height, width = frame.shape[:2]
            x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
            y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))

            if x2 <= x1 or y2 <= y1:
                return 100.0

            # 提取区域
            region = frame[y1:y2, x1:x2]
            total_pixels = region.shape[0] * region.shape[1]
            if total_pixels == 0:
                return 100.0

            import cv2
            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

            # 获取颜色列表配置
            colors_to_check = self._get_colors_config(config)
            
            if not colors_to_check:
                LOG_ERROR(f"[ResourceManager] 没有配置任何检测颜色")
                return 100.0

            # 从全黑的mask开始
            final_mask = np.zeros(hsv_region.shape[:2], dtype=np.uint8)
            total_matching_pixels = 0

            # 循环处理颜色列表
            for i, color_profile in enumerate(colors_to_check):
                if not color_profile.get("enabled", True):
                    continue
                    
                current_mask = self._create_color_mask(hsv_region, color_profile)
                current_pixels = cv2.countNonZero(current_mask)
                
                # 使用位或(OR)操作，将当前mask合并到最终mask中
                final_mask = cv2.bitwise_or(final_mask, current_mask)
                
                LOG_INFO(f"[ResourceManager] 颜色{i+1}({color_profile.get('name', 'Unknown')}) - 匹配像素: {current_pixels}")

            # 计算合并后mask的总匹配像素
            matching_pixels = cv2.countNonZero(final_mask)
            percentage = (matching_pixels / total_pixels) * 100.0
            
            LOG_INFO(f"[ResourceManager] 区域检测结果 - 总匹配像素: {matching_pixels}/{total_pixels}, 百分比: {percentage:.1f}%")
            
            return min(100.0, max(0.0, percentage))

        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 区域资源计算异常: {e}")
            return 100.0

    def _get_colors_config(self, config: Dict[str, Any]) -> list:
        """获取颜色配置列表，兼容新旧格式"""
        # 新格式：直接使用colors列表
        if "colors" in config:
            return config["colors"]
        
        # 旧格式兼容：从单一颜色配置构建列表
        colors = []
        
        # 主颜色（必须）
        main_color = {
            "name": "Normal",
            "enabled": True,
            "target_h": config.get("target_h", 0),
            "target_s": config.get("target_s", 75),
            "target_v": config.get("target_v", 29),
            "tolerance_h": config.get("tolerance_h", 10),
            "tolerance_s": config.get("tolerance_s", 20),
            "tolerance_v": config.get("tolerance_v", 20)
        }
        colors.append(main_color)
        
        # 中毒状态颜色（可选）
        if config.get("poison_enabled", False):
            poison_color = {
                "name": "Poison",
                "enabled": True,
                "target_h": config.get("poison_h", 80),
                "target_s": config.get("poison_s", 84),
                "target_v": config.get("poison_v", 48),
                "tolerance_h": config.get("poison_tolerance_h", 20),
                "tolerance_s": config.get("poison_tolerance_s", 27),
                "tolerance_v": config.get("poison_tolerance_v", 27)
            }
            colors.append(poison_color)
        
        return colors

    def _create_color_mask(self, hsv_region: np.ndarray, color_profile: Dict[str, Any]) -> np.ndarray:
        """为单个颜色配置创建mask"""
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

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._is_running and not self._is_paused