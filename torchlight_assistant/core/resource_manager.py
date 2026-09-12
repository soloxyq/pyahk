"""资源管理器 - 被动式资源检测模块

资源百分比语义说明:
- rectangle:模板 HSV / 当前帧 HSV 逐像素容差匹配后，统计全部有效填充行 / 总高度；
- circle:在圆形或半圆形蒙版内，统计自底向上的最长连续有效行段 / 总高度；
- text_ocr:解析“当前值/最大值”后直接计算比例。
前两种是近似填充度指标，并非对真实血/魔球体积的精确线性映射，可能与游戏内显示值存在偏差。
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
from ..utils.region_utils import parse_screen_rect
from ..utils.config_values import config_float, config_int


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
        # 时间戳必须与它生效时的药剂动作身份绑定。否则热更新
        # enabled/key 后，新动作会被旧按键的冷却时间戳错误压制。
        self._flask_cooldown_identities: Dict[str, tuple] = {}

        # 状态管理
        self._is_running = False
        self._is_paused = False

        # 当前配置的 Tesseract OCR 管理器。MacroEngine 首次发布完整配置时初始化，
        # 后续按 global.tesseract_ocr 的签名变化原子替换。
        self.tesseract_ocr_manager = None
        self._tesseract_config_signature = None
        
        # DeepAI 模块可用性检查（程序启动时检查一次）
        self.deepai_available = False
        self._deepai_get_recognizer = None
        self._check_deepai_availability()

        # PaddleOCR rec-only（资源数字识别）：F8 锁定的检测框 + 惰性管理器引用
        # 仅 detection_mode=="text_ocr" 且 ocr_engine=="paddle" 时使用；未在 F8 锁定则不触发
        self._ocr_number_box: Dict[str, Tuple[int, int, int, int]] = {}
        self.paddle_ocr_manager = None
    
    def _update_tesseract_ocr_config(self, tesseract_config: Dict[str, Any]):
        """使识别器与当前运行配置一致；构造成功后才替换实例。"""
        try:
            from ..utils.tesseract_ocr_manager import (
                get_tesseract_ocr_manager,
                tesseract_config_signature,
            )

            signature = tesseract_config_signature(tesseract_config)
            if (
                self.tesseract_ocr_manager is not None
                and signature == self._tesseract_config_signature
            ):
                return

            replacement = get_tesseract_ocr_manager(tesseract_config)
            # 单次识别先抓本地引用；这里的指针替换对并发调度线程是原子的，旧实例
            # 即使仍在执行也保持不可变，不会看到一半新一半旧的字段。
            self.tesseract_ocr_manager = replacement
            self._tesseract_config_signature = signature
            LOG_INFO("[ResourceManager] Tesseract OCR 配置已同步")
        except Exception as e:
            # 不沿用与当前配置不符的旧识别器；失败后该引擎本轮 fail-closed。
            self.tesseract_ocr_manager = None
            LOG_ERROR(f"[ResourceManager] Tesseract OCR 配置同步失败: {e}")
    
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

    def update_config(
        self,
        resource_config: Dict[str, Any],
        tesseract_config: Optional[Dict[str, Any]] = None,
    ):
        """更新资源配置"""
        if not isinstance(resource_config, dict):
            LOG_ERROR("[ResourceManager] resource_management 必须是对象，已禁用资源输入")
            resource_config = {}
        old_configs = {"hp": self.hp_config, "mp": self.mp_config}
        raw_hp_config = resource_config.get("hp_config", {})
        raw_mp_config = resource_config.get("mp_config", {})
        self.hp_config = raw_hp_config if isinstance(raw_hp_config, dict) else {}
        self.mp_config = raw_mp_config if isinstance(raw_mp_config, dict) else {}
        try:
            check_interval = config_int(resource_config.get("check_interval", 200))
        except ValueError:
            check_interval = 200
        self.check_interval = max(check_interval, 1)
        if tesseract_config is not None:
            self._update_tesseract_ocr_config(tesseract_config)
        for resource_type, new_config in (
            ("hp", self.hp_config),
            ("mp", self.mp_config),
        ):
            self._invalidate_stale_flask_cooldown(resource_type, new_config)
            if self._paddle_lock_signature(old_configs[resource_type]) != (
                self._paddle_lock_signature(new_config)
            ):
                # F8 锁定属于当前配置世代。热更新坐标/模式后不能继续沿用旧框，
                # 否则日志显示新配置，实际 OCR 却仍读取旧屏幕区域。
                self._ocr_number_box.pop(resource_type, None)
        LOG_INFO(f"[ResourceManager] 配置已更新 - HP: {self.hp_config.get('enabled', False)}, MP: {self.mp_config.get('enabled', False)}")

    @staticmethod
    def _match_threshold(config: Dict[str, Any]) -> float:
        """读取 Paddle 置信阈值；非法值抛出并由调用路径 fail-closed。"""
        value = config_float(config.get("match_threshold", 0.70))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"match_threshold 必须是 0..1 的有限数，实际为 {value!r}")
        return value

    @staticmethod
    def _paddle_lock_signature(config: Dict[str, Any]) -> tuple:
        return (
            config.get("enabled") is True,
            str(config.get("detection_mode", "rectangle")).lower(),
            str(config.get("ocr_engine", "template")).lower(),
            config.get("text_x1"),
            config.get("text_y1"),
            config.get("text_x2"),
            config.get("text_y2"),
        )

    @staticmethod
    def _flask_action_identity(
        resource_type: str, config: Dict[str, Any]
    ) -> tuple:
        """返回会改变药剂动作语义的最小身份。

        冷却、阈值和检测坐标的热更新不代表另一个按键动作，因此
        应沿用已发生的冷却；只有 enabled 或实际 key 身份变化才失效。
        """
        if not isinstance(config, dict):
            config = {}
        default_key = "1" if resource_type == "hp" else "2"
        key = config.get("key", default_key)
        # 坏配置不是可执行的按键身份，也不能把 list/dict 引用
        # 存进签名后再被外部原地修改。
        key_identity = key if isinstance(key, str) else None
        return (config.get("enabled") is True, key_identity)

    def _invalidate_stale_flask_cooldown(
        self, resource_type: str, config: Dict[str, Any]
    ) -> None:
        """药剂动作身份变化时丢弃仅属于旧动作的冷却。"""
        cooldowns = getattr(self, "_flask_cooldowns", None)
        if cooldowns is None:
            cooldowns = {}
            self._flask_cooldowns = cooldowns
        if resource_type not in cooldowns:
            return
        identities = getattr(self, "_flask_cooldown_identities", None)
        if identities is None:
            identities = {}
            self._flask_cooldown_identities = identities
        current_identity = self._flask_action_identity(resource_type, config)
        if identities.get(resource_type) == current_identity:
            return
        cooldowns.pop(resource_type, None)
        identities.pop(resource_type, None)
        LOG_INFO(
            f"[ResourceManager] {resource_type.upper()} 药剂动作已变更，"
            "旧冷却时间戳已失效"
        )

    def check_and_execute_resources(self, cached_frame: Optional[np.ndarray] = None) -> bool:
        """检查并执行资源管理（被动调用）"""
        if not self._is_running or self._is_paused:
            return False

        executed = False
        if self.hp_config.get("enabled") is True:
            if self._is_resource_low("hp", cached_frame):
                executed = self._execute_resource("hp", self.hp_config) or executed

        if self.mp_config.get("enabled") is True:
            if self._is_resource_low("mp", cached_frame):
                executed = self._execute_resource("mp", self.mp_config) or executed

        return executed

    def _check_internal_cooldown(self, resource_type: str) -> bool:
        """检查内部冷却是否就绪"""
        config = self.hp_config if resource_type == "hp" else self.mp_config
        try:
            cooldown_ms = config_float(config.get("cooldown", 5000))
        except ValueError:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 冷却配置无效")
            return False
        if cooldown_ms < 0:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 冷却配置越界")
            return False
        # update_config 之外的原地修改也不得沿用旧身份冷却。
        self._invalidate_stale_flask_cooldown(resource_type, config)
        cooldown_seconds = cooldown_ms / 1000.0

        # 用 monotonic 避免系统校时/休眠唤醒导致冷却异常
        current_time = time.monotonic()
        last_press_time = self._flask_cooldowns.get(resource_type)
        # 未成功使用过该动作时不存在冷却，不能把单调时钟原点当作一次按键。
        if last_press_time is None:
            return True

        return current_time - last_press_time >= cooldown_seconds

    def _is_resource_low(self, resource_type: str, cached_frame: Optional[np.ndarray]) -> bool:
        """检查资源是否低于阈值（使用统一的百分比检测接口）"""
        config = self.hp_config if resource_type == "hp" else self.mp_config

        # 检查内部冷却
        if not self._check_internal_cooldown(resource_type):
            return False

        try:
            threshold = config_float(config.get("threshold", 50))
        except ValueError:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 阈值配置无效，跳过")
            return False
        if not 0 <= threshold <= 100:
            LOG_ERROR(f"[ResourceManager] {resource_type.upper()} 阈值超出 0..100，跳过")
            return False
        match_percentage = 100.0

        try:
            detection_mode = str(
                config.get("detection_mode", "rectangle")
            ).lower()
            if detection_mode not in {"rectangle", "circle", "text_ocr"}:
                raise ValueError(
                    f"{resource_type.upper()} 未知检测模式: {detection_mode!r}"
                )
            frame = cached_frame if cached_frame is not None else self.border_frame_manager.get_current_frame()
            if frame is None:
                raise ValueError("无法获取帧数据")

            if detection_mode == "text_ocr":
                # 文本OCR
                rect = parse_screen_rect(
                    config,
                    ("text_x1", "text_y1", "text_x2", "text_y2"),
                )
                if rect is None:
                    raise ValueError(
                        f"{resource_type.upper()} 未配置有效的文本OCR检测区域"
                    )
                x1, y1, x2, y2 = rect

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

                roi = self._get_frame_region(
                    frame, x1, y1, x2 - x1, y2 - y1
                )
                if roi is None:
                    raise ValueError(f"{resource_type.upper()} 文本OCR检测区域超出当前帧")
                engine = str(config.get("ocr_engine", "template")).lower()
                if engine == "paddle":
                    # PaddleOCR rec-only：使用 F8 锁定的数字框；未锁定则不触发（兜底100%）
                    box = self._ocr_number_box.get(resource_type)
                    if box is None:
                        LOG_ERROR(f"[ResourceManager] {resource_type.upper()} PaddleOCR 数字框未在F8锁定，跳过(不触发)")
                        match_percentage = 100.0
                    else:
                        bx1, by1, bx2, by2 = box
                        locked_rect = parse_screen_rect(
                            {
                                "text_x1": bx1,
                                "text_y1": by1,
                                "text_x2": bx2,
                                "text_y2": by2,
                            },
                            ("text_x1", "text_y1", "text_x2", "text_y2"),
                        )
                        if locked_rect is None:
                            raise ValueError(
                                f"{resource_type.upper()} 已锁定的PaddleOCR区域超出当前帧"
                            )
                        bx1, by1, bx2, by2 = locked_rect
                        locked_roi = self._get_frame_region(
                            frame, bx1, by1, bx2 - bx1, by2 - by1
                        )
                        if locked_roi is None:
                            raise ValueError(f"{resource_type.upper()} 已锁定的PaddleOCR区域超出当前帧")
                        if self.paddle_ocr_manager is None:
                            from ..utils.paddle_ocr_manager import get_paddle_ocr_manager
                            self.paddle_ocr_manager = get_paddle_ocr_manager()
                        model_name = config.get("ocr_model", "PP-OCRv6_small_rec")
                        device = config.get("ocr_device", "cpu")
                        min_score = self._match_threshold(config)
                        cur, mx, pct = self.paddle_ocr_manager.recognize_and_parse(locked_roi, model_name, device, min_score)
                        if pct is not None:
                            match_percentage = pct
                            LOG_INFO(f"[ResourceManager] {resource_type.upper()} OCR识别: {cur}/{mx} = {pct:.1f}%")
                        else:
                            # 解析失败/当前>最大 等无效情况 → 不触发
                            match_percentage = 100.0
                elif engine in ("keras", "template"):
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
                elif engine == "tesseract":
                    tesseract_manager = self.tesseract_ocr_manager
                    if tesseract_manager is None:
                        LOG_ERROR(f"[ResourceManager] Tesseract OCR 未初始化，无法进行{resource_type.upper()}文本识别")
                        match_percentage = 100.0
                    else:
                        # 配置坐标是虚拟桌面绝对坐标，而 frame 通常只是目标
                        # window/output 的局部帧。上面已经通过 BorderFrameManager
                        # 换算并安全裁出了 roi；Tesseract 必须消费这份局部 ROI，
                        # 不能再拿绝对坐标直接切 frame（副屏/窗口捕获会切错位置）。
                        _, match_percentage = tesseract_manager.recognize_and_parse(
                            roi, (0, 0, roi.shape[1], roi.shape[0])
                        )
                        if match_percentage < 0:
                            match_percentage = 100.0
                else:
                    LOG_ERROR(
                        f"[ResourceManager] 未知 OCR 引擎 {engine!r}，"
                        f"跳过 {resource_type.upper()} 检测"
                    )
                    match_percentage = 100.0

            elif detection_mode == "circle":
                cx_raw = config.get("center_x")
                cy_raw = config.get("center_y")
                r_raw = config.get("radius")
                if cx_raw is None or cy_raw is None or r_raw is None:
                    raise ValueError(f"{resource_type.upper()} 圆形检测配置不完整")
                cx, cy, r = (
                    config_int(cx_raw),
                    config_int(cy_raw),
                    config_int(r_raw),
                )
                if r <= 0 or r > 32767:
                    raise ValueError(
                        f"{resource_type.upper()} 圆形半径必须在 1..32767，实际为 {r}"
                    )

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

            elif detection_mode == "rectangle":
                rect = parse_screen_rect(
                    config,
                )
                if rect is None:
                    raise ValueError(f"{resource_type.upper()} 未配置有效检测区域")
                x1, y1, x2, y2 = rect

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

        # 🔧 检测失败(compare_resource_circle 返回 None)= 本轮状态未知,必须跳过:
        # 既不触发药剂(否则模板/区域异常会被当成"血量为 0"而无限狂按),也不上报 OSD
        # (避免把 None 当成 0% 显示成"血量耗尽")。
        if not isinstance(match_percentage, (int, float)):
            LOG_ERROR(
                f"[ResourceManager] {resource_type.upper()} 本轮检测无效(None),跳过判定与上报"
            )
            return False

        # 上报OSD
        if self.debug_display_manager:
            if resource_type == "hp":
                self.debug_display_manager.update_health(match_percentage)
            elif resource_type == "mp":
                self.debug_display_manager.update_mana(match_percentage)

        return bool(match_percentage < threshold)

    def capture_template_hsv(self, frame: np.ndarray):
        """在F8准备阶段截取并保存模板区域的HSV数据到border_frame_manager的缓存"""
        if frame is None:
            LOG_ERROR("[ResourceManager] 无法获取帧数据用于模板截取")
            return

        try:
            import cv2
            import time

            for resource_type, config in (
                ("hp", self.hp_config),
                ("mp", self.mp_config),
            ):
                if config.get("enabled") is not True:
                    continue
                if config.get("detection_mode", "rectangle") != "rectangle":
                    continue
                rect = self._get_region_from_config(config)
                if rect is None:
                    continue
                x1, y1, x2, y2 = rect
                region_image = self._get_frame_region(
                    frame, x1, y1, x2 - x1, y2 - y1
                )
                if region_image is None or region_image.size == 0:
                    LOG_ERROR(
                        f"[ResourceManager] {resource_type.upper()} 模板区域"
                        "不在当前捕获帧内，跳过缓存"
                    )
                    continue
                if region_image.shape[2] == 4:  # BGRA
                    region_image = cv2.cvtColor(
                        region_image, cv2.COLOR_BGRA2BGR
                    )
                region_hsv = cv2.cvtColor(region_image, cv2.COLOR_BGR2HSV)
                h_tolerance = config.get("tolerance_h", 10)
                s_tolerance = config.get("tolerance_s", 30)
                v_tolerance = config.get("tolerance_v", 50)
                self.border_frame_manager.set_template_cache(
                    f"{resource_type}_region",
                    {
                        "image": region_hsv.copy(),
                        "width": x2 - x1,
                        "height": y2 - y1,
                        "timestamp": time.time(),
                        "type": "resource_region",
                        "h_tolerance": h_tolerance,
                        "s_tolerance": s_tolerance,
                        "v_tolerance": v_tolerance,
                    },
                )
                LOG_INFO(
                    f"[ResourceManager] 已保存{resource_type.upper()}模板HSV，"
                    f"尺寸: {region_hsv.shape}, 容差: H±{h_tolerance}, "
                    f"S±{s_tolerance}, V±{v_tolerance}"
                )

        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 模板HSV数据截取失败: {e}")

    def lock_ocr_number_position(self, frame: np.ndarray):
        """F8 准备阶段：锁定 PaddleOCR 数字检测框并预热 rec 模型。

        仅对 detection_mode=='text_ocr' 且 ocr_engine=='paddle' 且 enabled 的资源生效；
        其它检测模式/OCR 引擎完全不受影响。锁定后 RUNNING 才会用 OCR 检测（未锁定=不触发）。
        框 = 用户框选的 text_x1/y1/x2/y2；此处顺带预读一次做校验 + 预热(避免战斗中首帧卡顿)。"""
        # 清除上一次 F8 的旧框，避免残留
        self._ocr_number_box = {}
        if frame is None:
            return
        for resource_type in ("hp", "mp"):
            config = self.hp_config if resource_type == "hp" else self.mp_config
            if config.get("enabled") is not True:
                continue
            if config.get("detection_mode") != "text_ocr" or config.get("ocr_engine") != "paddle":
                continue
            try:
                rect = parse_screen_rect(
                    config,
                    ("text_x1", "text_y1", "text_x2", "text_y2"),
                )
                if rect is None:
                    LOG_ERROR(
                        f"[OCR锁定] {resource_type.upper()} 文本ROI无效或超出当前帧"
                    )
                    continue
                x1, y1, x2, y2 = rect
                if self.paddle_ocr_manager is None:
                    from ..utils.paddle_ocr_manager import get_paddle_ocr_manager
                    self.paddle_ocr_manager = get_paddle_ocr_manager()
                model_name = config.get("ocr_model", "PP-OCRv6_small_rec")
                device = config.get("ocr_device", "cpu")
                min_score = self._match_threshold(config)
                roi = self._get_frame_region(
                    frame, x1, y1, x2 - x1, y2 - y1
                )
                if roi is None:
                    raise ValueError(f"{resource_type.upper()} 文本ROI超出当前帧")
                LOG_INFO(f"[OCR锁定] {resource_type.upper()} 预热/锁定 PaddleOCR({model_name}@{device})…首次可能加载模型")
                cur, mx, pct = self.paddle_ocr_manager.recognize_and_parse(roi, model_name, device, min_score)
                # 始终锁定用户框选位置；预读失败仅告警(战斗中实际帧再读)
                self._ocr_number_box[resource_type] = (x1, y1, x2, y2)
                if pct is not None:
                    LOG_INFO(f"[OCR锁定] {resource_type.upper()} 已锁定({x1},{y1},{x2},{y2}) 预读={cur}/{mx}={pct:.1f}%")
                else:
                    LOG_INFO(f"[OCR锁定] {resource_type.upper()} 已锁定({x1},{y1},{x2},{y2})，但预读未解析出有效数字，请确认框选位置/模型")
            except Exception as e:
                LOG_ERROR(f"[OCR锁定] {resource_type.upper()} 锁定失败: {e}")

    def _get_region_from_config(self, config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """从配置中获取区域坐标"""
        try:
            rect = parse_screen_rect(config)
            if rect is not None:
                return rect
            LOG_ERROR(
                "[ResourceManager] 无效的区域坐标: "
                f"({config.get('region_x1')},{config.get('region_y1')}) -> "
                f"({config.get('region_x2')},{config.get('region_y2')})"
            )
            return None
        except Exception as e:
            LOG_ERROR(f"[ResourceManager] 获取区域坐标失败: {e}")
            return None

    def _get_frame_region(
        self, frame: np.ndarray, x: int, y: int, width: int, height: int
    ) -> Optional[np.ndarray]:
        """Slice an absolute desktop rectangle from a captured frame.

        Real managers delegate coordinate conversion to BorderFrameManager.
        The local fallback keeps small unit-test stubs and legacy standalone
        callers usable when their frame already starts at virtual origin (0, 0).
        """
        border = getattr(self, "border_frame_manager", None)
        getter = getattr(border, "get_region_from_frame", None)
        if getter is not None:
            return getter(frame, x, y, width, height)
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > frame.shape[1]
            or y + height > frame.shape[0]
        ):
            return None
        return frame[y:y + height, x:x + width]

    def _execute_resource(self, resource_type: str, config: Dict[str, Any]) -> bool:
        """执行资源操作"""
        key = config.get("key", "1" if resource_type == "hp" else "2")

        # 🎯 使用语义化的紧急优先级接口
        if resource_type == "hp":
            sent = self.input_handler.execute_hp_potion(key)
        elif resource_type == "mp":
            sent = self.input_handler.execute_mp_potion(key)
        else:
            return False

        if sent is False:
            LOG_ERROR(
                f"[ResourceManager] {resource_type.upper()}资源按键发送失败: {key}"
            )
            return False

        # 记录按键时间(monotonic 与 _check_internal_cooldown 配对)
        self._flask_cooldowns[resource_type] = time.monotonic()
        identities = getattr(self, "_flask_cooldown_identities", None)
        if identities is None:
            identities = {}
            self._flask_cooldown_identities = identities
        identities[resource_type] = self._flask_action_identity(
            resource_type, config
        )

        LOG_INFO(f"[ResourceManager] 已执行{resource_type.upper()}资源 - 按键: {key}")
        return True

    def clear_cooldowns(self):
        """清理所有冷却时间戳（用于重置）"""
        self._flask_cooldowns.clear()
        self._flask_cooldown_identities.clear()
        LOG_INFO("[ResourceManager] 冷却时间戳已清理")

    def get_status(self) -> Dict[str, Any]:
        """获取状态信息"""
        return {
            "hp_enabled": self.hp_config.get("enabled") is True,
            "mp_enabled": self.mp_config.get("enabled") is True,
            "check_interval": self.check_interval,
            "hp_cooldown_remaining": self._get_cooldown_remaining("hp"),
            "mp_cooldown_remaining": self._get_cooldown_remaining("mp"),
        }

    def _get_cooldown_remaining(self, resource_type: str) -> float:
        """获取剩余冷却时间（秒）"""
        config = self.hp_config if resource_type == "hp" else self.mp_config
        try:
            cooldown_ms = config_float(config.get("cooldown", 5000))
        except ValueError:
            return 0.0
        if cooldown_ms < 0:
            return 0.0
        self._invalidate_stale_flask_cooldown(resource_type, config)
        cooldown_seconds = cooldown_ms / 1000.0

        current_time = time.monotonic()
        last_press_time = self._flask_cooldowns.get(resource_type)
        if last_press_time is None:
            return 0.0

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
            origin_x, origin_y = getattr(
                self.border_frame_manager, "_last_window_capture_origin", (0, 0)
            )
            abs_cx = int(roi_cx + offset_x + origin_x)
            abs_cy = int(roi_cy + offset_y + origin_y)
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
