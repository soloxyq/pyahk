"边框图管理器 - 使用Windows Graphics Capture API优化"

import time
import threading
import math
from typing import Dict, Any, Optional, Tuple
import numpy as np
from pathlib import Path
from PIL import Image
import os
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

    def set_skill_coordinates(self, skills_config: Dict[str, Any]):
        """设置技能坐标并计算边框"""
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

    def prepare_border(self, skills_config: Dict[str, Any]):
        self.set_skill_coordinates(skills_config)

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

    def capture_once_for_debug_and_cache(self, interval_ms: int = 40):
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
                        self._update_template_cache_from_frame(frame)
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

    def compare_cooldown_image(self, frame: np.ndarray, x: int, y: int, skill_name: str, size: int, threshold: float = 0.7) -> bool:
        """使用指定帧数据对比冷却区域图像判断技能是否冷却"""
        try:
            if frame is None:
                return False
                
            current_region = self.get_region_from_frame(frame, x, y, size, size)
            if current_region is None:
                return False

            with self._cache_lock:
                cached_template = self._template_cache.get(f"{skill_name}_cooldown")
            if cached_template is None:
                return False

            if current_region.shape != cached_template["image"].shape:
                return False

            diff = np.abs(current_region.astype(np.float32) - cached_template["image"].astype(np.float32))
            similarity = 1.0 - (np.mean(diff) / 255.0)
            return similarity >= threshold

        except Exception as e:
            LOG_ERROR(f"使用帧对比冷却图像时异常: {e}")
            return False

    def is_resource_sufficient(self, frame: np.ndarray, x: int, y: int, color_range_threshold: int = 100) -> bool:
        """从指定帧数据中检测资源是否充足"""
        if frame is None:
            return False
            
        color = self.get_pixel_color(frame, x, y)
        if color is None:
            return False
        r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
        return (max(r, g, b) - min(r, g, b)) > color_range_threshold

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
    
    def _update_template_cache_from_frame(self, frame: np.ndarray):
        """从帧数据中更新模板缓存（带内存管理优化）"""
        try:
            with self._cache_lock:
                # 定期清理缓存以控制内存使用
                current_time = time.time()
                if current_time - self._last_cache_cleanup > 300:  # 敵5分钟清理一次
                    self._cleanup_template_cache()
                    self._last_cache_cleanup = current_time
                
                for coord in self.skill_coords:
                    if coord["name"].endswith("_cooldown"):
                        x, y, size = coord["x"], coord["y"], coord["size"]
                        region = self.get_region_from_frame(frame, x, y, size, size)
                        if region is not None:
                            self._template_cache[coord["name"]] = {
                                "image": region.copy(),
                                "timestamp": current_time,
                                "x": x, "y": y, "size": size
                            }
                            LOG(f"[模板缓存] 更新技能模板: {coord['name']}")
            
            LOG(f"[模板缓存] 已更新 {len([c for c in self.skill_coords if c['name'].endswith('_cooldown')])} 个技能模板")
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
        """获取当前帧数据 - 高性能版本"""
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