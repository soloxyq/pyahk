"""资源管理器 - 被动式资源检测模块

资源百分比语义说明:
本模块所有 HP/MP 百分比（match_percentage）来自对模板 HSV / 当前帧 HSV 的逐像素容差匹配后，
通过“自底向上连续填充行段长度 / 总高度 (或半圆掩膜高度)”得到的近似填充度指标                match_percentage = self.border_frame_manager.compare_resource_circle(
                    frame, center_x, center_y, radius, resource_type, 0.0, config
                )它并非对真实血/魔球体积或像素面积的精确线性映射，可能与游戏内显示的精确数值存在偏差。
因此:
1. 该值适合作为阈值触发的相对判定（< threshold 触发补给），不适合作为精确读数展示。
2. 不同分辨率 / UI 主题 / 光照会改变 HSV 分布，需重新截取模板。
3. 若需要更精确表现，可在后续迭代中加入曲线校准或多点采样。
"""

import time
from typing import Dict, Any, Optional, Tuple
import numpy as np

from ..utils.border_frame_manager import BorderFrameManager
from .ahk_input_handler import AHKInputHandler
from ..utils.debug_log import LOG_INFO, LOG_ERROR, LOG


class ResourceManager:
    """被动式资源管理器 - 只提供检测功能，不独立运行"""

    def __init__(self, border_manager: BorderFrameManager, input_handler: AHKInputHandler, debug_display_manager=None):
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

        # Tesseract OCR 管理器（程序启动时预加载）
        self.tesseract_ocr_manager = None
        self._initialize_tesseract_ocr()
        
        # DeepAI 模块可用性检查（程序启动时检查一次）
        self.deepai_available = False
        self._deepai_get_recognizer = None
        self._check_deepai_availability()
    
    def _initialize_tesseract_ocr(self):
        """初始化Tesseract OCR管理器（程序启动时加载一次）"""
        try:
            from .config_manager import ConfigManager
            from ..utils.tesseract_ocr_manager import get_tesseract_ocr_manager
            
            config_manager = ConfigManager()
            global_config = config_manager.load_config("default.json")
            tesseract_config = global_config.get("global", {}).get("tesseract_ocr", {})
            
            # 如果配置为空，使用默认值
            if not tesseract_config:
                tesseract_config = {
                    "tesseract_cmd": "D:\\Program Files\\Tesseract-OCR\\tesseract.exe",
                    "lang": "eng",
                    "psm_mode": 7,
                    "char_whitelist": "0123456789/"
                }
                LOG_INFO("[ResourceManager] 使用默认 Tesseract OCR 配置")
            
            # 获取全局单例（只会初始化一次）
            self.tesseract_ocr_manager = get_tesseract_ocr_manager(tesseract_config)
            LOG_INFO("[ResourceManager] Tesseract OCR 已预加载")
        except Exception as e:
            LOG_ERROR(f"[ResourceManager] Tesseract OCR 初始化失败: {e}")
    
    def _check_deepai_availability(self):
        """检查 DeepAI 模块可用性（程序启动时检查一次）"""
        try:
            from deepai import get_recognizer
            self.deepai_available = True
            self._deepai_get_recognizer = get_recognizer
            LOG_INFO("[ResourceManager] DeepAI 模块可用")
        except ImportError as e:
            self.deepai_available = False
            self._deepai_get_recognizer = None
            LOG("[ResourceManager] DeepAI 模块不可用，Keras/Template引擎将无法使用")
            self.tesseract_ocr_manager = None

    def update_config(self, resource_config: Dict[str, Any]):
        """更新资源配置"""
        self.hp_config = resource_config.get("hp_config", {})
        self.mp_config = resource_config.get("mp_config", {})
        self.check_interval = resource_config.get("check_interval", 200)
        LOG_INFO(f"[ResourceManager] 配置已更新 - HP: {self.hp_config.get('enabled', False)}, MP: {self.mp_config.get('enabled', False)}")

    def check_and_execute_resources(self, cached_frame: Optional[np.ndarray] = None) -> bool:
        """检查并执行资源管理（被动调用）"""
        if not self._is_running or self._is_paused:
            return False

        executed = False
        if self.hp_config.get("enabled", False):
            if self._is_resource_low("hp", cached_frame):
                self._execute_resource("hp", self.hp_config)
                executed = True

        if self.mp_config.get("enabled", False):
            if self._is_resource_low("mp", cached_frame):
                self._execute_resource("mp", self.mp_config)
                executed = True

        return executed

    def _check_internal_cooldown(self, resource_type: str) -> bool:
        """检查内部冷却是否就绪"""
        config = self.hp_config if resource_type == "hp" else self.mp_config
        cooldown_ms = config.get("cooldown", 5000)
        cooldown_seconds = cooldown_ms / 1000.0

        # 用 monotonic 避免系统校时/休眠唤醒导致冷却异常
        current_time = time.monotonic()
        last_press_time = self._flask_cooldowns.get(resource_type, 0)

        return current_time - last_press_time >= cooldown_seconds

    def _is_resource_low(self, resource_type: str, cached_frame: Optional[np.ndarray]) -> bool:
        """检查资源是否低于阈值（使用统一的百分比检测接口）"""
        config = self.hp_config if resource_type == "hp" else self.mp_config

        # 检查内部冷却
        if not self._check_internal_cooldown(resource_type):
            return False

        threshold = config.get("threshold", 50)
        match_percentage = 100.0

        try:
            detection_mode = config.get("detection_mode", "rectangle")
            frame = cached_frame if cached_frame is not None else self.border_frame_manager.get_current_frame()
            if frame is None:
                raise ValueError("无法获取帧数据")

            if detection_mode == "text_ocr":
                # 文本OCR
                x1_raw = config.get("text_x1")
                y1_raw = config.get("text_y1")
                x2_raw = config.get("text_x2")
                y2_raw = config.get("text_y2")
                if x1_raw is None or y1_raw is None or x2_raw is None or y2_raw is None:
                    raise ValueError(f"{resource_type.upper()} 文本OCR检测配置不完整")
                x1, y1, x2, y2 = int(x1_raw), int(y1_raw), int(x2_raw), int(y2_raw)

                if self.debug_display_manager:
                    self.debug_display_manager.update_detection_region(
                        f"{resource_type}_text_ocr",
                        {
                            "type": "rectangle",
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                            "color": "yellow" if resource_type == "hp" else "magenta",
                            "threshold": threshold,
                        },
                    )

                roi = frame[y1:y2, x1:x2]
                engine = config.get("ocr_engine", "template")
                if engine in ("keras", "template"):
                    # 使用启动时检查的标志位，避免重复导入
                    if not self.deepai_available:
                        LOG_ERROR(f"[ResourceManager] DeepAI模块不可用，无法使用{engine}引擎")
                        match_percentage = 100.0
                    else:
                        recognizer = self._deepai_get_recognizer(engine)
                        if recognizer is None or roi is None or roi.size == 0:
                            match_percentage = 100.0
                        else:
                            current, maximum = recognizer.recognize_and_parse(roi)
                            if current is not None and maximum and maximum > 0:
                                match_percentage = (current / maximum) * 100.0
                            else:
                                match_percentage = 100.0
                else:
                    # Tesseract 默认
                    if self.tesseract_ocr_manager is None:
                        LOG_ERROR(f"[ResourceManager] Tesseract OCR 未初始化，无法进行{resource_type.upper()}文本识别")
                        match_percentage = 100.0
                    else:
                        _, match_percentage = self.tesseract_ocr_manager.recognize_and_parse(frame, (x1, y1, x2, y2))
                        if match_percentage < 0:
                            match_percentage = 100.0

            elif detection_mode == "circle":
                cx_raw = config.get("center_x")
                cy_raw = config.get("center_y")
                r_raw = config.get("radius")
                if cx_raw is None or cy_raw is None or r_raw is None:
                    raise ValueError(f"{resource_type.upper()} 圆形检测配置不完整")
                cx, cy, r = int(cx_raw), int(cy_raw), int(r_raw)

                if self.debug_display_manager:
                    self.debug_display_manager.update_detection_region(
                        f"{resource_type}_circle",
                        {
                            "type": "circle",
                            "center_x": cx,
                            "center_y": cy,
                            "radius": r,
                            "color": "green" if resource_type == "hp" else "cyan",
                            "threshold": threshold,
                        },
                    )

                match_percentage = self.border_frame_manager.compare_resource_circle(
                    frame, cx, cy, r, resource_type, threshold, config
                )

            else:
                # rectangle
                x1 = int(config.get("region_x1", 0))
                y1 = int(config.get("region_y1", 0))
                x2 = int(config.get("region_x2", 0))
                y2 = int(config.get("region_y2", 0))
                if not (x1 < x2 and y1 < y2):
                    raise ValueError(f"{resource_type.upper()} 未配置有效检测区域")

                if self.debug_display_manager:
                    self.debug_display_manager.update_detection_region(
                        f"{resource_type}_rectangle",
                        {
                            "type": "rectangle",
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                            "color": "blue" if resource_type == "hp" else "red",
                            "threshold": threshold,
                        },
                    )

                region_name = f"{resource_type}_region"
                width, height = x2 - x1, y2 - y1
                
                # 确保模板缓存存在
                if not self.border_frame_manager.has_template_cache(region_name):
                    LOG_ERROR(f"[ResourceManager] 未找到模板缓存: {region_name}，请先按F8初始化")
                    match_percentage = 100.0
                else:
                    match_percentage = self.border_frame_manager._compare_resource_hsv(
                        frame, x1, y1, width, height, region_name, threshold
                    )

        except Exception as e:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 检测失败: {e}")
            import traceback
            LOG_ERROR(f"[ResourceManager] 详细错误信息: {traceback.format_exc()}")
            match_percentage = 100.0

        # 上报OSD
        if self.debug_display_manager:
            if resource_type == "hp":
                self.debug_display_manager.update_health(match_percentage)
            elif resource_type == "mp":
                self.debug_display_manager.update_mana(match_percentage)

        return bool(isinstance(match_percentage, (int, float)) and match_percentage < threshold)

    def capture_template_hsv(self, frame: np.ndarray):
        """在F8准备阶段截取并保存模板区域的HSV数据到border_frame_manager的缓存"""
        if frame is None:
            LOG_ERROR("[ResourceManager] 无法获取帧数据用于模板截取")
            return

        try:
            import cv2
            import time

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
                        
                        # 从配置读取容差参数
                        h_tolerance = self.hp_config.get("tolerance_h", 10)
                        s_tolerance = self.hp_config.get("tolerance_s", 30)
                        v_tolerance = self.hp_config.get("tolerance_v", 50)
                        
                        # 保存到border_frame_manager的缓存
                        self.border_frame_manager.set_template_cache("hp_region", {
                            "image": hp_hsv.copy(),
                            "width": x2 - x1,
                            "height": y2 - y1,
                            "timestamp": time.time(),
                            "type": "resource_region",
                            "h_tolerance": h_tolerance,
                            "s_tolerance": s_tolerance,
                            "v_tolerance": v_tolerance
                        })
                        LOG_INFO(f"[ResourceManager] 已保存HP模板HSV数据到缓存，尺寸: {hp_hsv.shape}, 容差: H±{h_tolerance}, S±{s_tolerance}, V±{v_tolerance}")

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
                        
                        # 从配置读取容差参数
                        h_tolerance = self.mp_config.get("tolerance_h", 10)
                        s_tolerance = self.mp_config.get("tolerance_s", 30)
                        v_tolerance = self.mp_config.get("tolerance_v", 50)
                        
                        # 保存到border_frame_manager的缓存
                        self.border_frame_manager.set_template_cache("mp_region", {
                            "image": mp_hsv.copy(),
                            "width": x2 - x1,
                            "height": y2 - y1,
                            "timestamp": time.time(),
                            "type": "resource_region",
                            "h_tolerance": h_tolerance,
                            "s_tolerance": s_tolerance,
                            "v_tolerance": v_tolerance
                        })
                        LOG_INFO(f"[ResourceManager] 已保存MP模板HSV数据到缓存，尺寸: {mp_hsv.shape}, 容差: H±{h_tolerance}, S±{s_tolerance}, V±{v_tolerance}")

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

        # 🎯 使用语义化的紧急优先级接口
        if resource_type == "hp":
            self.input_handler.execute_hp_potion(key)
        elif resource_type == "mp":
            self.input_handler.execute_mp_potion(key)
        # 其他类型不处理

        # 记录按键时间(monotonic 与 _check_internal_cooldown 配对)
        self._flask_cooldowns[resource_type] = time.monotonic()

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
                frame, center_x, center_y, radius, resource_type, 0.0, config
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
                frame, region_x1, region_y1, region_width, region_height, region_name, 0.0
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

        current_time = time.monotonic()
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
        在屏幕底部 HUD 区域自动检测 HP/MP 圆球。

        ROI 覆盖底部 30% 高度的整个左/右半屏,适配多种 ARPG 布局:
        - 中央偏侧(D4): HP ~x=600, MP ~x=1300
        - 角落布局(PoE2): HP ~x=120, MP ~x=1790
        - Torchlight 等其他: 落在底部左/右半区均可

        Args:
            orb_type (str): 'hp' 或 'mp'

        Returns:
            Dict[str, Dict[str, Any]]: 检测结果,只包含指定类型的球体信息。
        """
        try:
            import cv2
            import numpy as np

            frame = self.border_frame_manager.capture_target_window_frame()
            if frame is None:
                LOG_ERROR("[ResourceManager] 无法截取图像用于圆形检测")
                return {}

            h, w = frame.shape[:2]
            # 底部 30% 高度作为 HUD 候选区(从 y=0.7h 到 y=h)
            # 左/右半屏分别给 HP/MP — 兼容中央和角落两种布局
            bottom_y = int(h * 0.7)
            mid_x = int(w * 0.5)

            if orb_type == 'hp':
                roi = frame[bottom_y:h, 0:mid_x]
                offset_x, offset_y = 0, bottom_y
            elif orb_type == 'mp':
                roi = frame[bottom_y:h, mid_x:w]
                offset_x, offset_y = mid_x, bottom_y
            else:
                LOG_ERROR(f"[ResourceManager] 无效的球体类型: {orb_type}")
                return {}

            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray_blurred = cv2.GaussianBlur(gray, (9, 9), 2)

            # 半径范围放宽以兼容多游戏:D4 实测 ~68, PoE2 ~78, 留余地至 45-115
            circles = cv2.HoughCircles(
                gray_blurred,
                cv2.HOUGH_GRADIENT,
                dp=1,
                minDist=500,
                param1=50,
                param2=40,
                minRadius=45,
                maxRadius=115
            )

            if circles is None:
                LOG_ERROR(f"[ResourceManager] 在 {orb_type} 区域未检测到任何圆形")
                return {}

            detected_circles = circles[0]
            LOG_INFO(f"[ResourceManager] 在 {orb_type} 区域检测到 {len(detected_circles)} 个圆形")

            # 选半径最大的圆 — HP/MP 球通常是 HUD 里最大的圆形,优于 Hough 投票顺序
            target_circle = max(detected_circles, key=lambda c: c[2])
            
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