"边框图管理器 - 使用Windows Graphics Capture API优化"

import time
import threading
import math
from typing import Dict, Any, Optional, Tuple
import numpy as np
from pathlib import Path
from PIL import Image
import os
import cv2
from .debug_log import LOG, LOG_ERROR, LOG_INFO


# 导入Native Graphics Capture管理器
from .native_graphics_capture_manager import CaptureConfig

# 全局管理器实例
_global_border_frame_manager = None


class BorderFrameManager:
    """边框图管理器 - 使用Windows Graphics Capture API"""

    def __init__(
        self, capture_interval: float = 0.04
    ):  # 40ms间隔，但Graphics Capture会更快
        self.capture_interval = capture_interval
        self.running = False
        self.paused = False
        self._capture_lock = threading.RLock()  # 资源锁

        # 边框区域信息
        self.border_x = 0
        self.border_y = 0
        self.border_width = 0
        self.border_height = 0
        self.skill_coords = []  # 存储技能坐标
        self.border_calculated = False

        # Native Graphics Capture管理器
        self.graphics_capture = None
        self._capture_config = None

        # 模板缓存（添加内存监控）
        self._template_cache = {}
        self._cache_lock = threading.Lock()
        self._cache_memory_limit = 50 * 1024 * 1024  # 50MB限制
        self._last_cache_cleanup = time.time()

        # 调试保存标志
        self.debug_save_enabled = False
        self.debug_save_path = "D:\\gtemp"
        self.debug_save_count = 0

        # 订阅配置更新事件，确保窗口配置总能同步
        from ..core.event_bus import event_bus
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

    def _on_config_updated(self, skills_config: Dict, global_config: Dict):
        """响应配置更新，更新窗口激活配置"""
        window_config = global_config.get("window_activation", {})
        self.set_window_activation_config(window_config)
        LOG_INFO(f"[BorderFrameManager] 窗口配置已更新: {window_config}")

    def _get_target_window_handle(self) -> Optional[int]:
        """获取目标窗口句柄 - 根据用户配置查找目标窗口"""
        try:
            from ..utils.window_utils import WindowUtils
            import win32gui

            window_config = getattr(self, "window_activation_config", {})
            ahk_class = window_config.get("ahk_class", "")
            ahk_exe = window_config.get("ahk_exe", "")
            
            LOG(f"[窗口检测] 开始查找目标窗口，配置: ahk_exe='{ahk_exe}', ahk_class='{ahk_class}'")

            if ahk_exe:
                target_hwnd = WindowUtils.find_window_by_process_name(ahk_exe)
                if target_hwnd:
                    LOG(f"[窗口检测] 通过进程名找到目标窗口，句柄: {target_hwnd}")
                    return target_hwnd
                else:
                    LOG(f"[窗口检测] 未找到进程名为 '{ahk_exe}' 的窗口")

            if ahk_class:
                target_hwnd = WindowUtils.find_window_by_class(ahk_class)
                if target_hwnd:
                    LOG(f"[窗口检测] 通过类名找到目标窗口，句柄: {target_hwnd}")
                    return target_hwnd
                else:
                    LOG(f"[窗口检测] 未找到类名为 '{ahk_class}' 的窗口")

            foreground_hwnd = win32gui.GetForegroundWindow()
            LOG(f"[窗口检测] 使用前台窗口，句柄: {foreground_hwnd}")
            return foreground_hwnd

        except Exception as e:
            LOG_ERROR(f"[窗口检测] 查找目标窗口时出错: {e}")
            return None

    def set_window_activation_config(self, config: dict):
        """设置窗口激活配置"""
        self.window_activation_config = config

    def set_skill_coordinates(self, skills_config: Dict[str, Any], resource_config: Optional[Dict[str, Any]] = None):
        """设置技能坐标并计算边框（支持HP/MP区域）"""
        LOG(f"[技能坐标] 开始设置技能坐标，技能配置数量: {len(skills_config)}")
        self.skill_coords = []

        for skill_name, skill_data in skills_config.items():
            if not skill_data.get("Enabled", False):
                LOG(f"[技能坐标] 技能 '{skill_name}' 未启用，跳过")
                continue

            LOG(f"[技能坐标] 处理技能 '{skill_name}': TriggerMode={skill_data.get('TriggerMode', 0)}, ExecuteCondition={skill_data.get('ExecuteCondition', 0)}")

            if skill_data.get("TriggerMode", 0) == 1 and skill_data.get("CooldownCoordX", 0) > 0:
                coord_info = {
                    "name": f"{skill_name}_cooldown", "x": skill_data["CooldownCoordX"], "y": skill_data["CooldownCoordY"], "size": skill_data.get("CooldownSize", 12)
                }
                self.skill_coords.append(coord_info)
                LOG(f"[技能坐标] 添加冷却坐标: {coord_info}")

            if skill_data.get("ExecuteCondition", 0) in [1, 2] and skill_data.get("ConditionCoordX", 0) > 0:
                coord_info = {
                    "name": f"{skill_name}_condition", "x": skill_data["ConditionCoordX"], "y": skill_data["ConditionCoordY"], "size": 1
                }
                self.skill_coords.append(coord_info)
                LOG(f"[技能坐标] 添加条件坐标: {coord_info}")

        # 添加HP/MP区域到技能坐标中，确保即使没有冷却技能也能截取模板
        if resource_config:
            hp_config = resource_config.get("hp_config", {})
            if hp_config.get("enabled", False):
                hp_region = self._get_resource_region_from_config(hp_config)
                if hp_region:
                    x1, y1, x2, y2 = hp_region
                    coord_info = {
                        "name": "hp_region",
                        "x": x1, "y": y1,
                        "size": max(x2 - x1, y2 - y1)  # 使用区域大小作为size
                    }
                    self.skill_coords.append(coord_info)
                    LOG(f"[技能坐标] 添加HP区域坐标: {coord_info}")

            mp_config = resource_config.get("mp_config", {})
            if mp_config.get("enabled", False):
                mp_region = self._get_resource_region_from_config(mp_config)
                if mp_region:
                    x1, y1, x2, y2 = mp_region
                    coord_info = {
                        "name": "mp_region",
                        "x": x1, "y": y1,
                        "size": max(x2 - x1, y2 - y1)  # 使用区域大小作为size
                    }
                    self.skill_coords.append(coord_info)
                    LOG(f"[技能坐标] 添加MP区域坐标: {coord_info}")

        LOG_INFO(f"[技能坐标] 技能坐标设置完成，有效坐标数量: {len(self.skill_coords)}")
        self._calculate_border()

    def _calculate_border(self):
        """根据技能坐标计算边框区域"""
        if not self.skill_coords:
            LOG(f"[边框计算] 没有技能坐标，无法计算边框")
            self.border_calculated = False
            return
        
        LOG(f"[边框计算] 开始计算边框，技能坐标数量: {len(self.skill_coords)}")
        for i, coord in enumerate(self.skill_coords):
            LOG(f"[边框计算] 技能{i+1}: {coord['name']} 坐标({coord['x']}, {coord['y']}) 大小{coord['size']}")
        
        min_x = min(c["x"] for c in self.skill_coords)
        min_y = min(c["y"] for c in self.skill_coords)
        max_x = max(c["x"] + c["size"] - 1 for c in self.skill_coords)
        max_y = max(c["y"] + c["size"] - 1 for c in self.skill_coords)
        self.border_x, self.border_y = min_x, min_y
        self.border_width = max(1, max_x - min_x + 1)
        self.border_height = max(1, max_y - min_y + 1)
        self.border_calculated = True
        
        LOG_INFO(f"[边框计算] 计算完成，边框区域: ({self.border_x}, {self.border_y}) 大小: {self.border_width}x{self.border_height}")

    def prepare_border(self, skills_config: Dict[str, Any], resource_config: Optional[Dict[str, Any]] = None):
        self.set_skill_coordinates(skills_config, resource_config)

    def _get_resource_region_from_config(self, config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """从资源配置中获取区域坐标"""
        try:
            x1 = config.get("region_x1", 0)
            y1 = config.get("region_y1", 0)
            x2 = config.get("region_x2", 0)
            y2 = config.get("region_y2", 0)

            if x1 < x2 and y1 < y2 and x1 > 0 and y1 > 0:
                return (x1, y1, x2, y2)
            else:
                LOG(f"[资源区域] 无效的区域坐标: ({x1},{y1}) -> ({x2},{y2})")
                return None
        except Exception as e:
            LOG_ERROR(f"[资源区域] 获取区域坐标失败: {e}")
            return None

    def start_capture_loop(self, interval_ms: int = 40, capture_region: Optional[Tuple[int, int, int, int]] = None):
        """启动Native Graphics Capture捕获，支持全屏或指定区域"""
        with self._capture_lock:
            if self.running:
                return

            target_hwnd = self._get_target_window_handle()
            if not target_hwnd:
                LOG_ERROR("[捕获启动] 无法获取目标窗口句柄")
                return

            # 根据是否提供了capture_region来决定捕获模式
            enable_region = capture_region is not None
            if enable_region:
                self._capture_config = CaptureConfig(
                    target_window_handle=target_hwnd, 
                    capture_interval_ms=interval_ms, 
                    enable_region=True, 
                    region_x=capture_region[0], 
                    region_y=capture_region[1], 
                    region_width=capture_region[2], 
                    region_height=capture_region[3]
                )
            else:
                self._capture_config = CaptureConfig(
                    target_window_handle=target_hwnd, 
                    capture_interval_ms=interval_ms, 
                    enable_region=False
                )

            try:
                from .native_graphics_capture_manager import NativeGraphicsCaptureManager
                self.graphics_capture = NativeGraphicsCaptureManager(self._capture_config)
                if self.graphics_capture and self.graphics_capture.start_capture():
                    self.running = True
                    self.paused = False
                else:
                    if self.graphics_capture: self.graphics_capture.cleanup()
                    self.graphics_capture = None
            except Exception as e:
                LOG_ERROR(f"[捕获启动] 异常: {e}")
                if self.graphics_capture: self.graphics_capture.cleanup()
                self.graphics_capture = None

    def capture_once_for_debug(self, interval_ms: int = 40):
        """进行一次边框区域捕获用于调试保存"""
        with self._capture_lock:
            if not self.border_calculated: return
            temp_hwnd = self._get_target_window_handle()
            if not temp_hwnd: return

            temp_config = CaptureConfig(target_window_handle=temp_hwnd, capture_interval_ms=interval_ms, enable_region=True, region_x=self.border_x, region_y=self.border_y, region_width=self.border_width, region_height=self.border_height)
            try:
                from .native_graphics_capture_manager import NativeGraphicsCaptureManager
                temp_capture = NativeGraphicsCaptureManager(temp_config)
                if temp_capture and temp_capture.start_capture():
                    time.sleep(0.1)
                    frame = temp_capture.get_latest_frame()
                    if frame is not None:
                        self._save_debug_frame(frame)
                        self._update_template_cache_from_frame(frame)
                    temp_capture.cleanup()
            except Exception as e:
                LOG_ERROR(f"[调试捕获] 异常: {e}")

    def capture_once_for_debug_and_cache(self, interval_ms: int = 40, resource_regions: Optional[Dict[str, Tuple[int, int, int, int]]] = None):
        """进行一次全屏捕获，用于调试和缓存。"""
        with self._capture_lock:
            LOG(f"[调试捕获] 开始进行一次全屏捕获用于缓存")
            target_hwnd = self._get_target_window_handle()
            if not target_hwnd:
                LOG_ERROR(f"[调试捕获] 未找到目标窗口")
                return

            # 强制进行全屏捕获
            temp_config = CaptureConfig(target_window_handle=target_hwnd, capture_interval_ms=interval_ms, enable_region=False)
            try:
                from .native_graphics_capture_manager import NativeGraphicsCaptureManager
                temp_capture = NativeGraphicsCaptureManager(temp_config)
                if temp_capture and temp_capture.start_capture():
                    time.sleep(0.1)  # 等待一帧
                    frame = temp_capture.get_latest_frame()
                    if frame is not None:
                        self._save_debug_frame(frame)  # 保存调试帧
                        self._update_template_cache_from_frame(frame, resource_regions)
                    temp_capture.cleanup()
            except Exception as e:
                LOG_ERROR(f"[调试捕获和缓存] 异常: {e}")

    def stop(self):
        """停止边框图捕获"""
        with self._capture_lock:
            if not self.running: return
            self.running = False
            self.paused = False
            if self.graphics_capture:
                self.graphics_capture.cleanup()
                self.graphics_capture = None
            self._capture_config = None

    def pause_capture(self):
        """暂停截图循环"""
        with self._capture_lock:
            if self.running and not self.paused:
                self.paused = True
                if self.graphics_capture: self.graphics_capture.pause_capture()

    def resume_capture(self):
        """恢复截图循环"""
        with self._capture_lock:
            if self.running and self.paused:
                self.paused = False
                if self.graphics_capture: self.graphics_capture.resume_capture()

    def get_region_from_frame(self, frame: np.ndarray, x: int, y: int, width: int, height: int) -> Optional[np.ndarray]:
        """从当前捕获的帧中提取指定区域 (坐标为绝对屏幕坐标)"""
        try:
            if frame is None:
                return None

            offset_x, offset_y = 0, 0
            # 如果是区域捕获模式，计算偏移量
            if self._capture_config and self._capture_config.enable_region:
                offset_x = self._capture_config.region_x
                offset_y = self._capture_config.region_y

            # 计算相对于当前帧的坐标
            relative_x = x - offset_x
            relative_y = y - offset_y

            if (relative_x >= 0 and relative_y >= 0 and
                relative_x + width <= frame.shape[1] and
                relative_y + height <= frame.shape[0]):
                return frame[relative_y:relative_y+height, relative_x:relative_x+width]
            
            return None # 请求的区域不在捕获的帧内
        except Exception as e:
            LOG_ERROR(f"从帧中提取区域时异常: {e}")
            return None

    def get_pixel_color(self, frame: np.ndarray, x: int, y: int) -> Optional[int]:
        """从指定帧数据中获取像素颜色"""
        try:
            pixel_region = self.get_region_from_frame(frame, x, y, 1, 1)
            if pixel_region is not None and pixel_region.size > 0:
                r, g, b = pixel_region[0, 0, 0], pixel_region[0, 0, 1], pixel_region[0, 0, 2]
                return (int(r) << 16) | (int(g) << 8) | int(b)
            return None
        except Exception as e:
            LOG_ERROR(f"从帧中获取像素颜色时异常: {e}")
            return None

    def _create_enhanced_color_mask(self, hsv_region: np.ndarray, template_hsv: np.ndarray, resource_type: str, h_tolerance: int, s_tolerance: int, v_tolerance: int) -> np.ndarray:
        """创建增强的颜色掩码，支持红色双区间处理"""
        # 对于HP资源，使用红色双区间处理
        if resource_type == 'hp':
            # 红色的H值分布在0-10和170-179两个区间
            template_h = template_hsv[:, :, 0]
            region_h = hsv_region[:, :, 0]
            
            # 区间1: 0-10
            h_match1 = np.abs(region_h.astype(np.int16) - template_h.astype(np.int16)) <= h_tolerance
            
            # 区间2: 170-179 (处理跨越0度的情况)
            h_diff = np.abs(region_h.astype(np.int16) - template_h.astype(np.int16))
            h_diff_wrap = np.minimum(h_diff, 180 - h_diff)
            h_match2 = h_diff_wrap <= h_tolerance
            
            h_match = h_match1 | h_match2
        else:
            # 其他资源使用标准HSV差值匹配
            h_diff = np.abs(hsv_region[:, :, 0].astype(np.int16) - template_hsv[:, :, 0].astype(np.int16))
            h_diff = np.minimum(h_diff, 180 - h_diff)
            h_match = h_diff <= h_tolerance
        
        # S和V通道使用标准匹配
        s_diff = np.abs(hsv_region[:, :, 1].astype(np.int16) - template_hsv[:, :, 1].astype(np.int16))
        v_diff = np.abs(hsv_region[:, :, 2].astype(np.int16) - template_hsv[:, :, 2].astype(np.int16))
        
        s_match = s_diff <= s_tolerance
        v_match = v_diff <= v_tolerance
        
        return h_match & s_match & v_match

    def compare_resource_circle(self, frame: np.ndarray, center_x: int, center_y: int, radius: int, resource_type: str, threshold: float = 0.0, color_config: Optional[dict] = None) -> float:
        """使用半圆形蒙版和连续段检测算法，返回匹配百分比（0.0-100.0）"""
        try:
            import cv2

            template_name = f"{resource_type}_region"
            with self._cache_lock:
                cached_template = self._template_cache.get(template_name)
            
            # 如果缓存中没有模板，尝试从当前区域创建模板
            if cached_template is None:
                LOG_INFO(f"[圆形检测] 未找到资源模板 {template_name}，尝试从当前区域创建模板")
                x1, y1 = center_x - radius, center_y - radius
                region = self.get_region_from_frame(frame, x1, y1, radius * 2, radius * 2)
                if region is not None:
                    if region.shape[2] == 4:
                        region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
                    hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
                    
                    # 创建临时模板缓存
                    cached_template = {
                        "image": hsv_region.copy(),
                        "timestamp": time.time(),
                        "type": "resource_region",
                        "h_tolerance": 10,
                        "s_tolerance": 20, 
                        "v_tolerance": 20
                    }
                    # 保存到缓存
                    with self._cache_lock:
                        self._template_cache[template_name] = cached_template
                    LOG_INFO(f"[圆形检测] 已创建资源模板: {template_name}")
                else:
                    LOG_ERROR(f"[圆形检测] 无法从区域创建模板: {template_name}")
                    return 0.0

            template_hsv = cached_template.get("image")
            if template_hsv is None:
                LOG_ERROR(f"[圆形检测] 缓存的模板无效: {template_name}")
                return 0.0

            x1, y1 = center_x - radius, center_y - radius
            region = self.get_region_from_frame(frame, x1, y1, radius * 2, radius * 2)
            if region is None: return 0.0

            if region.shape[2] == 4:
                region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

            # --- 半圆蒙版逻辑（优化版）---
            # 使用更精确的圆形掩码创建
            y_indices, x_indices = np.ogrid[:radius*2, :radius*2]
            center_coord = float(radius)
            dist_from_center = np.sqrt((x_indices - center_coord) ** 2 + (y_indices - center_coord) ** 2)
            circular_mask = (dist_from_center <= radius).astype(np.uint8) * 255

            # 创建半圆掩码
            half_mask = np.zeros_like(circular_mask)
            width, height = radius * 2, radius * 2
            if resource_type == 'hp':  # HP的右半边被干扰，只分析左半边
                half_mask[:, :width // 2] = 255
            elif resource_type == 'mp':  # MP的左半边被干扰，只分析右半边
                half_mask[:, width // 2:] = 255

            final_mask = cv2.bitwise_and(circular_mask, half_mask)
            # --- 结束 ---

            h_tolerance = cached_template.get("h_tolerance", 10)
            s_tolerance = cached_template.get("s_tolerance", 20)
            v_tolerance = cached_template.get("v_tolerance", 20)

            # 使用增强的颜色匹配（支持红色双区间）
            pixel_match = self._create_enhanced_color_mask(
                hsv_region, template_hsv, resource_type, h_tolerance, s_tolerance, v_tolerance
            )

            # --- 优化的连续段检测算法 ---
            # 计算每行在蒙版内的匹配像素数
            masked_match = cv2.bitwise_and(pixel_match.astype(np.uint8), pixel_match.astype(np.uint8), mask=final_mask)
            vertical_sum = np.sum(masked_match, axis=1)

            # 计算每行的有效像素阈值（蒙版内像素数的60%）
            mask_vertical_sum = np.sum(final_mask > 0, axis=1)
            row_threshold = mask_vertical_sum * 0.6
            is_filled = vertical_sum > row_threshold

            # 从底部向上找到最长的连续有效行段
            max_len = 0
            current_len = 0
            for i in range(height-1, -1, -1):  # 从下往上扫描
                if is_filled[i] and mask_vertical_sum[i] > 0:  # 该行有蒙版且有效
                    current_len += 1
                    if current_len > max_len:
                        max_len = current_len
                else:
                    current_len = 0

            # 计算百分比：最长连续段 / 总高度
            if height > 0:
                match_percentage = (max_len / height) * 100.0
            else:
                match_percentage = 0.0

            LOG_INFO(f"[圆形检测] {resource_type.upper()} 检测结果: {match_percentage:.1f}% (连续段长度: {max_len}/{height})")
            return match_percentage

        except Exception as e:
            LOG_ERROR(f"[圆形检测] {resource_type} 检测异常: {e}")
            return 0.0

    def compare_cooldown_image(self, frame: np.ndarray, x: int, y: int, skill_name: str, size: int, threshold: float = 0.7) -> Optional[float]:
        """使用HSV容差检测，统一处理技能冷却。

        返回:
            匹配百分比(0.0-100.0)；若检测失败/数据缺失，返回 None 以便上层安全跳过。
        """
        try:
            import cv2

            # 资源检测由 compare_resource_circle 处理，这里只处理技能冷却
            if skill_name.endswith('_region'):
                LOG_ERROR(f"[冷却检测] 错误的调用: {skill_name} 应由资源检测方法处理")
                return None

            template_name = f"{skill_name}_cooldown"

            with self._cache_lock:
                cached_template = self._template_cache.get(template_name)
            
            if cached_template is None:
                LOG_ERROR(f"[冷却检测] 未找到技能模板: {template_name}")
                return None

            template_hsv = cached_template.get("image")
            if template_hsv is None:
                LOG_ERROR(f"[冷却检测] 缓存的模板无效: {template_name}")
                return None

            current_region = self.get_region_from_frame(frame, x, y, size, size)
            if current_region is None:
                LOG_ERROR(f"[冷却检测] 无法获取技能区域: {skill_name} 坐标({x}, {y}) 大小{size}")
                return None

            if current_region.shape[2] == 4:
                current_region = cv2.cvtColor(current_region, cv2.COLOR_BGRA2BGR)
            hsv_region = cv2.cvtColor(current_region, cv2.COLOR_BGR2HSV)

            if hsv_region.shape != template_hsv.shape:
                hsv_region = cv2.resize(hsv_region, (template_hsv.shape[1], template_hsv.shape[0]))

            # 技能冷却使用适中的容差，避免过于严格
            h_tolerance = 10  # 从5增加到10
            s_tolerance = 20  # 从15增加到20
            v_tolerance = 25  # 从20增加到25

            h_diff = np.abs(hsv_region[:, :, 0].astype(np.int16) - template_hsv[:, :, 0].astype(np.int16))
            h_diff = np.minimum(h_diff, 180 - h_diff)
            s_diff = np.abs(hsv_region[:, :, 1].astype(np.int16) - template_hsv[:, :, 1].astype(np.int16))
            v_diff = np.abs(hsv_region[:, :, 2].astype(np.int16) - template_hsv[:, :, 2].astype(np.int16))
            
            # 分解布尔比较操作以避免数组比较错误
            h_match = h_diff <= h_tolerance
            s_match = s_diff <= s_tolerance
            v_match = v_diff <= v_tolerance
            pixel_match = h_match & s_match & v_match

            total_pixels = hsv_region.shape[0] * hsv_region.shape[1]
            if total_pixels == 0: 
                LOG_ERROR(f"[冷却检测] 技能区域像素数为0: {skill_name}")
                return None
            
            matching_pixels = np.count_nonzero(pixel_match)
            match_percentage = (matching_pixels / total_pixels) * 100.0
            
            # 高频: 技能冷却逐帧匹配详情 -> 仅在 DEBUG=1 时输出
            LOG(f"[冷却检测] {skill_name} - 匹配详情: 总像素={total_pixels}, 匹配像素={matching_pixels}, 匹配度={match_percentage:.2f}%")
            return match_percentage

        except Exception as e:
            LOG_ERROR(f"[HSV冷却检测] {skill_name} 检测异常: {e}")
            import traceback
            LOG_ERROR(f"[HSV冷却检测] 详细错误信息: {traceback.format_exc()}")
            return None

    def _compare_resource_hsv(self, frame: np.ndarray, x: int, y: int, size: int, resource_name: str, threshold: float) -> Optional[float]:
        """使用HSV容差和连续段检测算法。

        计算方式说明:
        1. 对模板 HSV 与当前区域 HSV 做逐像素容差匹配，得到布尔匹配矩阵。
        2. 统计每一行匹配像素是否超过 60%（视为“有效填充”）。
        3. 自底向上寻找最长连续有效行段长度 / 总高度 => 近似“当前剩余资源百分比”。

        该结果是启发式“填充高度”估算，不保证与游戏真实值线性一致。
        失败返回 None。
        """
        try:
            import cv2

            # 从缓存获取HSV模板
            with self._cache_lock:
                cached_template = self._template_cache.get(resource_name)
            if cached_template is None:
                LOG_ERROR(f"[HSV检测] 未找到资源模板: {resource_name}")
                return None

            template_hsv = cached_template.get("image")
            if template_hsv is None:
                LOG_ERROR(f"[HSV检测] 缓存的模板无效: {resource_name}")
                return None

            # 获取区域图像
            t_width = cached_template.get("width", size)
            t_height = cached_template.get("height", size)
            region = self.get_region_from_frame(frame, x, y, t_width, t_height)
            if region is None:
                LOG_ERROR(f"[HSV检测] 无法获取资源区域: {resource_name} 坐标({x}, {y}) 大小{t_width}x{t_height}")
                return None

            # 转换为HSV
            if region.shape[2] == 4:  # BGRA
                region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

            # 确保模板和区域尺寸一致
            if hsv_region.shape != template_hsv.shape:
                hsv_region = cv2.resize(hsv_region, (template_hsv.shape[1], template_hsv.shape[0]))

            # 获取容差设置
            h_tolerance = cached_template.get("h_tolerance", 10)
            s_tolerance = cached_template.get("s_tolerance", 20)
            v_tolerance = cached_template.get("v_tolerance", 20)

            # 使用增强的颜色匹配（支持红色双区间）
            # 从resource_name中提取资源类型
            resource_type = resource_name.replace('_region', '') if '_region' in resource_name else 'unknown'
            pixel_match = self._create_enhanced_color_mask(
                hsv_region, template_hsv, resource_type, h_tolerance, s_tolerance, v_tolerance
            )

            # --- 优化的连续段检测算法 ---
            # 计算每行的匹配像素数
            vertical_sum = np.sum(pixel_match, axis=1)

            # 判断每行是否"有效"（60%以上的像素是目标颜色）
            row_threshold = t_width * 0.6
            is_filled = vertical_sum > row_threshold

            # 从底部向上找到最长的连续有效行段
            max_len = 0
            current_len = 0
            for i in range(t_height-1, -1, -1):  # 从下往上扫描
                if is_filled[i]:
                    current_len += 1
                    if current_len > max_len:
                        max_len = current_len
                else:
                    current_len = 0

            # 计算百分比：最长连续段 / 总高度
            if t_height > 0:
                match_percentage = (max_len / t_height) * 100.0
            else:
                match_percentage = 0.0

            LOG(f"[HSV检测] {resource_name} - 匹配详情: 区域大小={t_width}x{t_height}, 最长连续段={max_len}, 匹配度={match_percentage:.2f}%")
            return match_percentage

        except Exception as e:
            LOG_ERROR(f"[HSV检测] {resource_name} 检测异常: {e}")
            import traceback
            LOG_ERROR(f"[HSV检测] 详细错误信息: {traceback.format_exc()}")
            return None

    def is_resource_sufficient(self, frame: np.ndarray, x: int, y: int, color_range_threshold: int = 100) -> bool:
        """从指定帧数据中检测资源是否充足"""
        if frame is None:
            return False
            
        color = self.get_pixel_color(frame, x, y)
        if color is None:
            return False
        r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        color_range = max(r, g, b) - min(r, g, b)
        result = color_range > color_range_threshold
        
        return result

    def is_hp_sufficient(self, frame: np.ndarray, x: int, y: int) -> bool:
        """从指定帧数据中检测HP是否充足"""
        if frame is None:
            return False
            
        color = self.get_pixel_color(frame, x, y)
        if color is None:
            return False
        r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        return r > 100 and g < 50 and b < 50

    def rgb_similarity(self, frame: np.ndarray, x: int, y: int, target_color: int, tolerance: int) -> bool:
        """从指定帧数据中进行RGB相似度检测"""
        if frame is None:
            return False
            
        color = self.get_pixel_color(frame, x, y)
        if color is None:
            return False
        r1, g1, b1 = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        r2, g2, b2 = (target_color >> 16) & 0xFF, (target_color >> 8) & 0xFF, target_color & 0xFF
        return abs(r1 - r2) <= tolerance and abs(g1 - g2) <= tolerance and abs(b1 - b2) <= tolerance

    def capture_target_window_frame(self) -> Optional[np.ndarray]:
        """一次性全屏捕获（模板截取 / 调试专用；禁止在实时检测循环中调用）。"""
        try:
            import win32gui
            from mss import mss

            target_hwnd = self._get_target_window_handle()
            if not target_hwnd or not win32gui.IsWindow(target_hwnd):
                LOG_ERROR("[帧捕获-MSS] 无法找到或窗口句柄无效")
                return None

            # 激活窗口到前台，确保截图正确
            try:
                import win32con
                # 如果窗口最小化，先恢复
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                # 设置为前台窗口
                win32gui.SetForegroundWindow(target_hwnd)
                time.sleep(0.2)  # 等待窗口激活的短暂延迟
            except Exception as e:
                LOG_ERROR(f"[窗口激活] 激活目标窗口失败: {e}")
                # 即使激活失败，也继续尝试截图，作为后备

            # 获取窗口的矩形区域
            rect = win32gui.GetWindowRect(target_hwnd)
            x, y, width, height = rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]

            if width <= 0 or height <= 0:
                LOG_ERROR(f"[帧捕获-MSS] 窗口尺寸无效: w={width}, h={height}")
                return None

            monitor = {"top": y, "left": x, "width": width, "height": height}

            with mss() as sct:
                # 从指定区域截图
                sct_img = sct.grab(monitor)
                # 转换为OpenCV格式 (BGRA -> BGR)
                frame = np.array(sct_img)
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # 保存截图用于调试
            try:
                save_path = "D:\\gtemp"
                os.makedirs(save_path, exist_ok=True)
                cv2.imwrite(os.path.join(save_path, "mss_capture.png"), frame_bgr)
                LOG_INFO("[帧保存] MSS截图已保存到 D:\\gtemp\\mss_capture.png")
            except Exception as save_e:
                LOG_ERROR(f"[帧保存] 保存MSS截图失败: {save_e}")

            LOG_INFO(f"[帧捕获-MSS] 成功捕获目标窗口帧，尺寸: {frame_bgr.shape}")
            return frame_bgr

        except ImportError:
            LOG_ERROR("[帧捕获-MSS] mss或pywin32库未安装")
            return None
        except Exception as e:
            LOG_ERROR(f"[帧捕获-MSS] 一次性捕获异常: {e}")
            import traceback
            LOG_ERROR(traceback.format_exc())
            return None

    def capture_screen_for_reroll(self, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[np.ndarray]:
        """
        专用于洗练功能，使用Pillow进行一次性的、独立的屏幕截图。

        Args:
            region: 可选的截图区域 (left, top, right, bottom).

        Returns:
            截图的numpy数组 (RGB格式)，如果失败则返回None.
        """
        try:
            from PIL import ImageGrab
            import numpy as np

            screenshot = ImageGrab.grab(bbox=region)
            frame = np.array(screenshot)
            # ImageGrab返回的是RGB，但有些系统可能是BGR，如果后续OCR不准，可能需要转换
            # frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return frame
        except Exception as e:
            LOG_ERROR(f"[独立截图] 使用Pillow截图失败: {e}")
            return None

    def _save_debug_frame(self, frame: np.ndarray):
        """保存调试帧数据到文件，处理BGRA格式。"""
        if not self.debug_save_enabled:
            LOG_INFO("[调试保存] 功能未启用，跳过保存。")
            return

        try:
            LOG_INFO(f"[调试保存] 正在尝试保存调试帧到 {self.debug_save_path}")
            os.makedirs(self.debug_save_path, exist_ok=True)
            LOG_INFO(f"[调试保存] 目录 {self.debug_save_path} 已确认存在。")
            
            self.debug_save_count += 1
            filename = f"debug_frame_{self.debug_save_count:04d}.png"
            filepath = os.path.join(self.debug_save_path, filename)
            
            # 从BGRA转换为RGB并保存
            import cv2
            from PIL import Image
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
            image = Image.fromarray(rgb_frame)
            image.save(filepath)
            LOG_INFO(f"[调试保存] 成功！帧数据已保存到: {filepath}")

        except Exception as e:
            LOG_ERROR(f"[调试保存] 保存帧数据时发生错误: {e}", exc_info=True)
    
    def _update_template_cache_from_frame(self, frame: np.ndarray, resource_regions: Optional[Dict[str, Tuple[int, int, int, int]]] = None):
        """从帧数据中更新模板缓存（支持技能冷却和资源检测）"""
        try:
            with self._cache_lock:
                # 定期清理缓存以控制内存使用
                current_time = time.time()
                if current_time - self._last_cache_cleanup > 300:  # 每5分钟清理一次
                    self._cleanup_template_cache()
                    self._last_cache_cleanup = current_time

                # 更新技能冷却模板
                skill_template_count = 0
                for coord in self.skill_coords:
                    # 只处理技能冷却坐标，不处理条件坐标
                    if coord["name"].endswith("_cooldown"):
                        x, y, size = coord["x"], coord["y"], coord["size"]
                        region = self.get_region_from_frame(frame, x, y, size, size)
                        if region is not None:
                            # 转换为HSV并保存
                            import cv2
                            if region.shape[2] == 4:  # BGRA
                                region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
                            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
                            
                            self._template_cache[coord["name"]] = {
                                "image": hsv_region.copy(),
                                "timestamp": current_time,
                                "x": x, "y": y, "size": size,
                                "type": "skill_cooldown"
                            }
                            skill_template_count += 1
                            LOG(f"[模板缓存] 更新技能模板: {coord['name']}")

                # 更新资源检测模板
                resource_template_count = 0
                if resource_regions:
                    for region_name, (x1, y1, x2, y2) in resource_regions.items():
                        width, height = x2 - x1, y2 - y1
                        region = self.get_region_from_frame(frame, x1, y1, width, height)
                        if region is not None:
                            # 转换为HSV并保存
                            import cv2
                            if region.shape[2] == 4:  # BGRA
                                region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
                            hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

                            # 获取容差设置（使用默认值，稍后可以通过其他方式更新）
                            h_tolerance = 10  # 默认值
                            s_tolerance = 20
                            v_tolerance = 20

                            self._template_cache[region_name] = {
                                "image": hsv_region.copy(),
                                "timestamp": current_time,
                                "x": x1, "y": y1, "width": width, "height": height,
                                "type": "resource_region",
                                "h_tolerance": h_tolerance,
                                "s_tolerance": s_tolerance,
                                "v_tolerance": v_tolerance
                            }
                            resource_template_count += 1
                            LOG(f"[模板缓存] 更新资源HSV模板: {region_name} (容差: H±{h_tolerance}, S±{s_tolerance}, V±{v_tolerance})")

                LOG(f"[模板缓存] 已更新 {skill_template_count} 个技能模板，{resource_template_count} 个资源模板")
        except Exception as e:
            LOG_ERROR(f"[模板缓存] 更新模板缓存失败: {e}")
    
    def _cleanup_template_cache(self):
        """清理过期的模板缓存"""
        try:
            current_time = time.time()
            expired_keys = []
            total_memory = 0
            
            for key, template_data in self._template_cache.items():
                # 计算内存使用
                if 'image' in template_data:
                    total_memory += template_data['image'].nbytes
                
                # 检查是否过期（10分钟）
                if current_time - template_data.get('timestamp', 0) > 600:
                    expired_keys.append(key)
            
            # 删除过期的模板
            for key in expired_keys:
                del self._template_cache[key]
            
            # 如果内存使用过多，删除最旧的模板
            if total_memory > self._cache_memory_limit:
                sorted_items = sorted(self._template_cache.items(), 
                                    key=lambda x: x[1].get('timestamp', 0))
                # 删除最旧的50%
                remove_count = len(sorted_items) // 2
                for i in range(remove_count):
                    del self._template_cache[sorted_items[i][0]]
                
                LOG(f"[模板缓存] 内存使用过多，已清理 {remove_count} 个模板")
                
            LOG(f"[模板缓存] 清理完成，剩余模板: {len(self._template_cache)}, 内存使用: {total_memory/1024/1024:.2f}MB")
        except Exception as e:
            LOG_ERROR(f"[模板缓存] 清理缓存时出错: {e}")
    
    def enable_debug_save(self):
        """启用调试保存"""
        self.debug_save_enabled = True
        LOG(f"[调试保存] 调试保存已启用，保存路径: {self.debug_save_path}")
    
    def get_current_frame(self) -> Optional[np.ndarray]:
        """获取当前帧数据 - 高性能版本。

        帧来源策略:
        1. 实时检测（技能冷却/条件/资源）仅使用此接口提供的 Graphics Capture 最新帧。
        2. 模板或离线调试请使用一次性捕获接口，不与实时路径混用，以避免色域/延迟差异引入匹配抖动。
        3. 若返回 None，上层逻辑应跳过本轮检测，不自动 fallback 到 MSS，以保持来源一致性。
        """
        try:
            with self._capture_lock:
                if not self.running or not self.graphics_capture:
                    return None
                    
                # 直接从正在运行的捕获器获取最新帧
                return self.graphics_capture.get_latest_frame()
        except Exception as e:
            LOG_ERROR(f"[帧获取] 获取当前帧失败: {e}")
            return None

    def get_cache_status(self) -> dict:
        """获取缓存状态信息"""
        with self._cache_lock:
            total_memory = sum(template_data.get('image', np.array([])).nbytes 
                             for template_data in self._template_cache.values())
            return {
                "template_count": len(self._template_cache),
                "memory_usage_mb": total_memory / 1024 / 1024,
                "memory_limit_mb": self._cache_memory_limit / 1024 / 1024
            }

# --- Unmodified helpers ---
def get_border_frame_manager():
    global _global_border_frame_manager
    if _global_border_frame_manager is None:
        _global_border_frame_manager = BorderFrameManager()
    return _global_border_frame_manager

def cleanup_border_frame_manager():
    global _global_border_frame_manager
    if _global_border_frame_manager:
        _global_border_frame_manager.stop()
        _global_border_frame_manager = None