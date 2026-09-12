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
from .region_utils import parse_screen_rect
from .config_values import config_float, config_int


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

        # 🎯 帧快照(所有权契约的核心):
        # C++ 零拷贝视图会在"之后第二次 get_frame 调用"时被原地覆写(A/B 双缓冲,
        # CaptureFrameData 只在 get_frame 调用内执行,无异步写线程)。消费者(调度线程
        # 检测/OCR、GUI 预览/校准)在锁外长时间持有视图必然撕裂/混帧。
        # 因此 get_current_frame() 一律返回**锁内复制的快照**:复制发生在 _capture_lock
        # 内,而覆写只能发生在下一次同样要拿锁的调用里 —— 这是完全的同步保证,不是
        # 缩小竞态窗口。快照按时间窗微缓存,同一检测 tick 内多次调用共享同一帧
        # (顺带保证同轮决策基于同一画面)。快照对消费者是**只读共享**的,不得原地修改。
        self._frame_snapshot: Optional[np.ndarray] = None
        self._frame_snapshot_at: float = 0.0
        self._frame_origin: Tuple[int, int] = (0, 0)
        self._last_window_capture_origin: Tuple[int, int] = (0, 0)
        self._snapshot_reuse_window = self._compute_reuse_window(capture_interval)

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
        self._cleanup_done = False

        # 订阅配置更新事件，确保窗口配置总能同步
        from ..core.event_bus import event_bus
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

    @staticmethod
    def _compute_reuse_window(capture_interval: float) -> float:
        """快照复用窗口 = 捕获间隔的一半(一个间隔内不会有新内容,重复复制纯浪费),
        下限 10ms 防御异常配置,**且不超过捕获间隔本身**。

        上限是必须的:间隔小于 20ms 时只取下限会让窗口横跨好几个捕获周期 ——
        用户把间隔调小反而拿到更旧的帧。
        """
        iv = float(capture_interval or 0.0)
        if iv <= 0:
            return 0.010
        return min(max(0.010, iv / 2.0), iv)

    def _on_config_updated(self, skills_config: Dict, global_config: Dict):
        """响应配置更新，更新窗口激活配置"""
        # EventBus.publish 会先复制订阅者列表；cleanup 与一次在飞发布并发时，
        # unsubscribe 不能撤销那份副本，因此旧实例还需在 handler 入口自我门禁。
        if getattr(self, "_cleanup_done", False):
            return
        window_config = (
            global_config.get("window_activation", {})
            if isinstance(global_config, dict)
            else {}
        )
        self.set_window_activation_config(window_config)
        LOG_INFO(f"[BorderFrameManager] 窗口配置已更新: {window_config}")

    def _get_target_window_handle(self) -> Optional[int]:
        """获取目标窗口句柄 - 根据用户配置查找目标窗口"""
        try:
            from ..utils.window_utils import WindowUtils
            import win32gui

            window_config = getattr(self, "window_activation_config", {})
            if not WindowUtils.is_target_config_valid(window_config):
                LOG_ERROR("[窗口检测] 目标窗口配置无效，拒绝退化到前台窗口")
                return None
            ahk_class, ahk_exe = WindowUtils.normalize_target_config(window_config)
            
            LOG(f"[窗口检测] 开始查找目标窗口，配置: ahk_exe='{ahk_exe}', ahk_class='{ahk_class}'")

            target_hwnd = WindowUtils.find_target_window(window_config)
            if target_hwnd:
                LOG(f"[窗口检测] 通过统一目标条件找到窗口，句柄: {target_hwnd}")
                return target_hwnd
            if ahk_exe or ahk_class:
                LOG("[窗口检测] 配置的目标窗口当前不存在")
                # 显式目标是安全边界，不能在目标消失时悄悄改抓当前前台窗口。
                # 否则 READY 会拿浏览器/编辑器的画面建模板，随后 direct 输入也
                # 可能落到同一个无关应用。只有完全未配置目标的 direct 模式才
                # 保留“跟随当前前台窗口”的兼容语义。
                return None

            foreground_hwnd = WindowUtils.find_target_window(
                {}, fallback_to_foreground=True
            )
            LOG(f"[窗口检测] 使用前台窗口，句柄: {foreground_hwnd}")
            return foreground_hwnd

        except Exception as e:
            LOG_ERROR(f"[窗口检测] 查找目标窗口时出错: {e}")
            return None

    def set_window_activation_config(self, config: dict):
        """设置窗口激活配置"""
        # 保留畸形值，让消费端明确 fail-closed；把它折叠成 ``{}`` 会错误地
        # 启用“捕获当前前台窗口”的 direct-mode 兼容路径。
        self.window_activation_config = config

    def set_skill_coordinates(self, skills_config: Dict[str, Any], resource_config: Optional[Dict[str, Any]] = None):
        """设置技能坐标并计算边框（支持HP/MP区域）"""
        if not isinstance(skills_config, dict):
            skills_config = {}
        if not isinstance(resource_config, dict):
            resource_config = {}
        LOG(f"[技能坐标] 开始设置技能坐标，技能配置数量: {len(skills_config)}")
        self.skill_coords = []

        for skill_name, skill_data in skills_config.items():
            if not isinstance(skill_data, dict):
                LOG_ERROR(f"[技能坐标] 技能 '{skill_name}' 配置不是对象，跳过")
                continue
            if skill_data.get("Enabled") is not True:
                LOG(f"[技能坐标] 技能 '{skill_name}' 未启用，跳过")
                continue

            LOG(f"[技能坐标] 处理技能 '{skill_name}': TriggerMode={skill_data.get('TriggerMode', 0)}, ExecuteCondition={skill_data.get('ExecuteCondition', 0)}")

            try:
                trigger_mode = config_int(skill_data.get("TriggerMode", 0))
                execute_condition = config_int(skill_data.get("ExecuteCondition", 0))
            except ValueError:
                LOG_ERROR(f"[技能坐标] 技能 '{skill_name}' 模式字段无效，跳过检测坐标")
                continue

            if trigger_mode == 1:
                try:
                    cooldown_x = config_int(skill_data["CooldownCoordX"])
                    cooldown_y = config_int(skill_data["CooldownCoordY"])
                    cooldown_size = config_int(skill_data.get("CooldownSize", 12))
                    if cooldown_size <= 0:
                        raise ValueError("size must be positive")
                except (KeyError, ValueError) as e:
                    LOG_ERROR(f"[技能坐标] {skill_name} 冷却区域无效，跳过: {e}")
                else:
                    coord_info = {
                        "name": f"{skill_name}_cooldown",
                        "x": cooldown_x,
                        "y": cooldown_y,
                        "size": cooldown_size,
                    }
                    self.skill_coords.append(coord_info)
                    LOG(f"[技能坐标] 添加冷却坐标: {coord_info}")

            if execute_condition in (1, 2):
                try:
                    condition_x = config_int(skill_data["ConditionCoordX"])
                    condition_y = config_int(skill_data["ConditionCoordY"])
                except (KeyError, ValueError) as e:
                    LOG_ERROR(f"[技能坐标] {skill_name} 条件坐标无效，跳过: {e}")
                else:
                    coord_info = {
                        "name": f"{skill_name}_condition",
                        "x": condition_x,
                        "y": condition_y,
                        "size": 1,
                    }
                    self.skill_coords.append(coord_info)
                    LOG(f"[技能坐标] 添加条件坐标: {coord_info}")

        # 添加HP/MP区域到技能坐标中，确保即使没有冷却技能也能截取模板
        if resource_config:
            raw_hp_config = resource_config.get("hp_config", {})
            hp_config = raw_hp_config if isinstance(raw_hp_config, dict) else {}
            if hp_config.get("enabled") is True:
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

            raw_mp_config = resource_config.get("mp_config", {})
            mp_config = raw_mp_config if isinstance(raw_mp_config, dict) else {}
            if mp_config.get("enabled") is True:
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

    def get_resource_region_from_config(self, config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """按资源检测模式获取实际检测区域坐标。"""
        try:
            detection_mode = str(config.get("detection_mode", "rectangle")).lower()

            if detection_mode == "text_ocr":
                rect = parse_screen_rect(
                    config, ("text_x1", "text_y1", "text_x2", "text_y2")
                )
                if rect is not None:
                    return rect
                x1 = config.get("text_x1")
                y1 = config.get("text_y1")
                x2 = config.get("text_x2")
                y2 = config.get("text_y2")
                LOG(f"[资源区域] 无效的文本OCR区域: ({x1},{y1}) -> ({x2},{y2})")
                return None

            if detection_mode == "circle":
                center_x = config_int(config.get("center_x", 0))
                center_y = config_int(config.get("center_y", 0))
                radius = config_int(config.get("radius", 0))
                if radius > 0:
                    return (
                        center_x - radius,
                        center_y - radius,
                        center_x + radius,
                        center_y + radius,
                    )
                LOG(f"[资源区域] 无效的圆形区域: center=({center_x},{center_y}), radius={radius}")
                return None

            rect = parse_screen_rect(config)
            if rect is not None:
                return rect
            x1 = config.get("region_x1")
            y1 = config.get("region_y1")
            x2 = config.get("region_x2")
            y2 = config.get("region_y2")
        except Exception as e:
            LOG_ERROR(f"[资源区域] 获取区域坐标失败: {e}")
            return None
        LOG(f"[资源区域] 无效的矩形区域: ({x1},{y1}) -> ({x2},{y2})")
        return None

    def _get_resource_region_from_config(self, config: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        """兼容旧调用名。"""
        return self.get_resource_region_from_config(config)

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
                    # 快照复用窗口跟随**本次实际生效**的捕获间隔(即 interval_ms,
                    # 而不是构造时的 self.capture_interval)。
                    # 启动路径 macro_engine._start_subsystems_based_on_mode() 传入
                    # _capture_interval_ms()(GUI 配置,10..1000ms 钳制,默认 40)。
                    self.capture_interval = max(0.0, interval_ms / 1000.0)
                    self._snapshot_reuse_window = self._compute_reuse_window(self.capture_interval)
                    # 新会话:作废上个会话可能残留的快照
                    self._frame_snapshot = None
                    self._frame_snapshot_at = 0.0
                    self._frame_origin = (0, 0)
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
                        old_origin = self._frame_origin
                        rect = temp_capture.get_latest_frame_rect()
                        if rect is not None:
                            self._frame_origin = (int(rect["x"]), int(rect["y"]))
                        try:
                            self._save_debug_frame(frame)
                            self._update_template_cache_from_frame(frame)
                        finally:
                            self._frame_origin = old_origin
                    temp_capture.cleanup()
            except Exception as e:
                LOG_ERROR(f"[调试捕获] 异常: {e}")

    def capture_once_for_debug_and_cache(self, interval_ms: int = 40, resource_regions: Optional[Dict[str, Tuple[int, int, int, int]]] = None):
        """进行一次全屏捕获，用于调试和缓存，并返回该帧。"""
        with self._capture_lock:
            LOG(f"[调试捕获] 开始进行一次全屏捕获用于缓存")
            target_hwnd = self._get_target_window_handle()
            if not target_hwnd:
                LOG_ERROR(f"[调试捕获] 未找到目标窗口")
                return None

            # 强制进行全屏捕获
            temp_config = CaptureConfig(target_window_handle=target_hwnd, capture_interval_ms=interval_ms, enable_region=False)
            temp_capture = None
            try:
                from .native_graphics_capture_manager import NativeGraphicsCaptureManager
                temp_capture = NativeGraphicsCaptureManager(temp_config)
                frame = None
                frame_copy = None
                if temp_capture and temp_capture.start_capture():
                    time.sleep(0.1)  # 等待一帧
                    frame = temp_capture.get_latest_frame()
                    if frame is not None:
                        rect = temp_capture.get_latest_frame_rect()
                        if rect is None:
                            LOG_ERROR("[调试捕获和缓存] 原生层未返回帧坐标，丢弃该帧")
                        else:
                            # 返回帧的消费者会继续通过本 manager 做绝对坐标切片，
                            # 因此 origin 必须和 frame_copy 一起保持，不能在返回前恢复
                            # 成上一条（可能来自另一块显示器或区域捕获的）原点。
                            self._frame_origin = (int(rect["x"]), int(rect["y"]))
                            self._save_debug_frame(frame)  # 保存调试帧
                            self._update_template_cache_from_frame(frame, resource_regions)
                            frame_copy = frame.copy()
                if temp_capture:
                    temp_capture.cleanup()
                return frame_copy
            except Exception as e:
                LOG_ERROR(f"[调试捕获和缓存] 异常: {e}")
                if temp_capture:
                    temp_capture.cleanup()
            return None

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
            # 会话结束,快照作废(防止快速重启后在复用窗口内拿到上个会话的旧帧)
            self._frame_snapshot = None
            self._frame_snapshot_at = 0.0
            self._frame_origin = (0, 0)

    def cleanup(self):
        """停止捕获并对称解除全局配置订阅；保持幂等。"""
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        try:
            self.stop()
        finally:
            from ..core.event_bus import event_bus

            event_bus.unsubscribe("engine:config_updated", self._on_config_updated)

    def pause_capture(self):
        """暂停截图循环"""
        with self._capture_lock:
            if self.running and not self.paused:
                if not self.graphics_capture:
                    return False
                paused = self.graphics_capture.pause_capture()
                if paused is False:
                    return False
                self.paused = True
                # 快照作废:恢复后不得在复用窗口内返回暂停前的过期画面
                self._frame_snapshot = None
                self._frame_snapshot_at = 0.0
            return bool(self.running and self.paused)

    def resume_capture(self):
        """恢复截图循环"""
        with self._capture_lock:
            if self.running and self.paused:
                if not self.graphics_capture:
                    return False
                resumed = self.graphics_capture.resume_capture()
                if resumed is False:
                    # 失败时保持 paused=True，MacroEngine 会拒绝提交 RUNNING。
                    return False
                self.paused = False
                self._frame_snapshot = None
                self._frame_snapshot_at = 0.0
            return bool(self.running and not self.paused)

    def get_region_from_frame(self, frame: np.ndarray, x: int, y: int, width: int, height: int) -> Optional[np.ndarray]:
        """从当前捕获的帧中提取指定区域 (坐标为绝对屏幕坐标)"""
        try:
            if frame is None:
                return None

            offset_x, offset_y = 0, 0
            # Frames are output-local in DXGI but all persisted detection
            # coordinates are virtual-desktop physical pixels.  Native capture
            # reports the absolute rectangle for the current snapshot.
            offset_x, offset_y = getattr(self, "_frame_origin", (0, 0))

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
                # 🔧 BUG修复: 帧是 BGRA(见 native_capture get_frame),index0=B、index2=R。
                # 原代码按 r,g,b=index0,1,2 取值,把 B 当 R、R 当 B,打包出的 0xRRGGBB 实为
                # 0xBBGGRR。下游 is_hp_sufficient(硬编码 r>100 检红血)与 rgb_similarity(与
                # 取色器存的真 RGB 比较)因此误判,导致 ExecuteCondition 1/2 错选 Key/AltKey。
                b, g, r = pixel_region[0, 0, 0], pixel_region[0, 0, 1], pixel_region[0, 0, 2]
                return (int(r) << 16) | (int(g) << 8) | int(b)
            return None
        except Exception as e:
            LOG_ERROR(f"从帧中获取像素颜色时异常: {e}")
            return None

    def _create_enhanced_color_mask(
        self,
        hsv_region: np.ndarray,
        template_hsv: np.ndarray,
        resource_type: str,
        h_tolerance: int,
        s_tolerance: int,
        v_tolerance: int,
        color_config: Optional[dict] = None,
    ) -> np.ndarray:
        """创建增强的颜色掩码，支持红色双区间和 D4 屏障色处理。"""
        h_tolerance = self._finite_config_number(
            h_tolerance, "tolerance_h", 0.0, 179.0
        )
        s_tolerance = self._finite_config_number(
            s_tolerance, "tolerance_s", 0.0, 255.0
        )
        v_tolerance = self._finite_config_number(
            v_tolerance, "tolerance_v", 0.0, 255.0
        )
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
        
        resource_match = h_match & s_match & v_match

        if resource_type == 'hp' and self._should_detect_hp_barrier(color_config):
            resource_match = resource_match | self._create_hp_barrier_mask(hsv_region)

        return resource_match

    def _should_detect_hp_barrier(self, color_config: Optional[dict]) -> bool:
        """HP 屏障/护盾会把 D4 血球染成蓝紫色,默认将其视为安全填充。"""
        if color_config is None:
            return True
        # 只有真正的 JSON false 才关闭；字符串 "false" 等错误类型继续采用
        # 安全默认 true，避免把护盾误判为空血并触发药剂。
        return color_config.get("detect_barrier", True) is not False

    @staticmethod
    def _finite_config_number(
        value: Any, name: str, minimum: float, maximum: float
    ) -> float:
        parsed = config_float(value)
        if not minimum <= parsed <= maximum:
            raise ValueError(
                f"{name} 必须是 {minimum:g}..{maximum:g} 的有限数，实际为 {value!r}"
            )
        return parsed

    def _create_hp_barrier_mask(self, hsv_region: np.ndarray) -> np.ndarray:
        """识别 D4 血球上的蓝紫色屏障覆盖层。"""
        h = hsv_region[:, :, 0]
        s = hsv_region[:, :, 1]
        v = hsv_region[:, :, 2]

        # OpenCV H: 85..155 ~= 170..310 degrees,覆盖青蓝/蓝紫屏障色。
        return (h >= 85) & (h <= 155) & (s >= 35) & (v >= 35)

    def compare_resource_circle(self, frame: np.ndarray, center_x: int, center_y: int, radius: int, resource_type: str, threshold: float = 0.0, color_config: Optional[dict] = None) -> Optional[float]:
        """使用半圆形蒙版和连续段检测算法，返回匹配百分比（0.0-100.0）。

        🔧 检测失败(模板缺失/区域越界/异常)返回 **None**,而不是 0.0。
        0.0 的语义是"资源真的空了",会让上层判定血量耗尽 → 无限狂按药剂;
        None 表示"本轮状态未知",上层必须跳过本轮而不触发。
        """
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
                        "h_tolerance": color_config.get("tolerance_h", 10) if color_config else 10,
                        "s_tolerance": color_config.get("tolerance_s", 20) if color_config else 20,
                        "v_tolerance": color_config.get("tolerance_v", 20) if color_config else 20,
                    }
                    # 保存到缓存
                    with self._cache_lock:
                        self._template_cache[template_name] = cached_template
                    LOG_INFO(f"[圆形检测] 已创建资源模板: {template_name}")
                else:
                    LOG_ERROR(f"[圆形检测] 无法从区域创建模板: {template_name} → 本轮跳过(不触发)")
                    return None

            template_hsv = cached_template.get("image")
            if template_hsv is None:
                LOG_ERROR(f"[圆形检测] 缓存的模板无效: {template_name} → 本轮跳过(不触发)")
                return None

            x1, y1 = center_x - radius, center_y - radius
            region = self.get_region_from_frame(frame, x1, y1, radius * 2, radius * 2)
            if region is None:
                LOG_ERROR(f"[圆形检测] {resource_type} 区域超出捕获帧 → 本轮跳过(不触发)")
                return None

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
            # half 字段(在 hp_config/mp_config 里)显式指定分析哪半边,避开被遮挡的一侧:
            #   "left"  → 只取左半 (x < 中线)
            #   "right" → 只取右半 (x >= 中线)
            #   "full"  → 整圆不裁剪
            #   未指定  → 向后兼容: HP 默认左半, MP 默认右半 (D4 中央球布局)
            # 用例: PoE2 角落球需 HP="right" / MP="left"(与 D4 默认相反)。
            width, height = radius * 2, radius * 2
            half = ""
            if color_config:
                half = str(color_config.get("half", "")).strip().lower()
            if not half:
                half = "left" if resource_type == "hp" else ("right" if resource_type == "mp" else "full")

            if half == "full":
                final_mask = circular_mask.copy()
            else:
                half_mask = np.zeros_like(circular_mask)
                if half == "right":
                    half_mask[:, width // 2:] = 255
                elif half == "left":
                    half_mask[:, :width // 2] = 255
                else:
                    # 显式未知枚举不能偷换成整圆：遮挡区域不同可能反转药剂判定。
                    LOG_ERROR(f"[圆形检测] 未知 half='{half}', 本轮跳过(不触发)")
                    return None
                final_mask = cv2.bitwise_and(circular_mask, half_mask)
            # --- 结束 ---

            if color_config:
                h_tolerance = color_config.get("tolerance_h", cached_template.get("h_tolerance", 10))
                s_tolerance = color_config.get("tolerance_s", cached_template.get("s_tolerance", 20))
                v_tolerance = color_config.get("tolerance_v", cached_template.get("v_tolerance", 20))
            else:
                h_tolerance = cached_template.get("h_tolerance", 10)
                s_tolerance = cached_template.get("s_tolerance", 20)
                v_tolerance = cached_template.get("v_tolerance", 20)

            # 色相无关检测(liquid_by_brightness):中毒等状态会改变液体色相(如 HP 中毒变绿),
            # 但液体始终"高饱和 + 高亮度",空玻璃则是暗灰。开启后只按 S/V 阈值判定"有液体",
            # 完全忽略色相。仅当 hp_config/mp_config 显式开启时生效,不影响冷却/取点色/其他配置。
            if color_config and color_config.get("liquid_by_brightness") is True:
                s_min = self._finite_config_number(
                    color_config.get("s_min", 30), "s_min", 0.0, 100.0
                ) * 2.55  # 0-100 -> OpenCV 0-255
                v_min = self._finite_config_number(
                    color_config.get("v_min", 25), "v_min", 0.0, 100.0
                ) * 2.55
                pixel_match = (hsv_region[:, :, 1] > s_min) & (hsv_region[:, :, 2] > v_min)
            else:
                # 使用增强的颜色匹配（支持红色双区间）
                pixel_match = self._create_enhanced_color_mask(
                    hsv_region,
                    template_hsv,
                    resource_type,
                    h_tolerance,
                    s_tolerance,
                    v_tolerance,
                    color_config,
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
            LOG_ERROR(f"[圆形检测] {resource_type} 检测异常: {e} → 本轮跳过(不触发)")
            return None

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

    def has_template_cache(self, template_name: str) -> bool:
        """检查模板缓存是否存在"""
        with self._cache_lock:
            return template_name in self._template_cache
    
    def set_template_cache(self, template_name: str, template_data: dict):
        """设置模板缓存"""
        with self._cache_lock:
            self._template_cache[template_name] = template_data
            LOG_INFO(f"[模板缓存] 已设置模板: {template_name}")
    
    def _compare_resource_hsv(self, frame: np.ndarray, x: int, y: int, width: int, height: int, resource_name: str, threshold: float) -> Optional[float]:
        """使用 HSV 容差和有效填充行统计返回资源百分比。

        计算方式说明:
        1. 对模板 HSV 与当前区域 HSV 做逐像素容差匹配，得到布尔匹配矩阵。
        2. 统计每一行匹配像素是否超过 60%（视为“有效填充”）。
        3. 统计全部有效行数 / 总高度 => 近似“当前剩余资源百分比”。

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

            # 获取区域图像（优先使用传入的width/height，其次使用缓存的）
            t_width = cached_template.get("width", width)
            t_height = cached_template.get("height", height)
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

            # --- 优化的填充行检测算法 ---
            # 计算每行的匹配像素数
            vertical_sum = np.sum(pixel_match, axis=1)

            # 判断每行是否"有效"（60%以上的像素是目标颜色）
            row_threshold = t_width * 0.6
            is_filled = vertical_sum > row_threshold

            # 计算总填充行数（更鲁棒，能抵抗中间的遮挡）
            filled_rows = np.sum(is_filled)
            
            # 计算百分比：总填充行数 / 总高度
            if t_height > 0:
                match_percentage = (filled_rows / t_height) * 100.0
            else:
                match_percentage = 0.0

            LOG(f"[HSV检测] {resource_name} - 匹配详情: 区域大小={t_width}x{t_height}, 填充行数={filled_rows}/{t_height}, 匹配度={match_percentage:.2f}%")
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

            self._last_window_capture_origin = (int(x), int(y))

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

            screenshot = ImageGrab.grab(bbox=region, all_screens=True)
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
        """获取当前帧的**独立快照**(BGRA, H×W×4)。

        帧来源策略:
        1. 实时检测（技能冷却/条件/资源）仅使用此接口提供的 Graphics Capture 最新帧。
        2. 模板或离线调试请使用一次性捕获接口，不与实时路径混用，以避免色域/延迟差异引入匹配抖动。
        3. 若返回 None，上层逻辑应跳过本轮检测，不自动 fallback 到 MSS，以保持来源一致性。

        所有权契约:
        - 返回的数组是锁内复制的快照,**不会**被后续捕获覆写,可跨线程安全持有任意时长
          (旧行为返回 C++ 缓冲区视图,持有期间另一线程两次取帧即撕裂 —— OCR 混帧误读的根源)。
        - 快照在时间窗内被多个调用方**只读共享**,任何消费者都不得原地修改;
          需要可写数组时自行 .copy()。
        """
        try:
            with self._capture_lock:
                # paused 也必须拦:PAUSED = 完全停下,底层已 pause_capture 不再产出新帧,
                # 若只看 running,复用窗口内的在途检测会继续拿到**暂停前**的旧帧,
                # 基于过期画面做判定(如误判血量低而喝药)。
                if not self.running or self.paused or not self.graphics_capture:
                    return None

                # 微缓存:窗口内直接复用快照(不再触发 C++ 取帧,同一 tick 内帧一致)
                now = time.monotonic()
                if (
                    self._frame_snapshot is not None
                    and (now - self._frame_snapshot_at) < self._snapshot_reuse_window
                ):
                    return self._frame_snapshot

                view = self.graphics_capture.get_latest_frame()
                if view is None:
                    return None
                # 锁内复制:覆写只可能发生在下一次(也必须拿本锁的)取帧调用里,
                # 因此这次 memcpy 期间缓冲区不可能变化 —— 完全同步,非窗口缩小。
                snapshot = np.array(view, copy=True)
                # 置只读:窗口内多个消费者拿到的是**同一个**数组对象,任何一方原地
                # 修改都会污染其他人的检测输入。当前没有消费者这么做,设成只读是为了
                # 让将来违反契约的写法当场报错,而不是变成一次静默误判。
                snapshot.setflags(write=False)
                self._frame_snapshot = snapshot
                self._frame_snapshot_at = now
                rect = self.graphics_capture.get_latest_frame_rect()
                if rect is not None:
                    self._frame_origin = (int(rect["x"]), int(rect["y"]))
                return self._frame_snapshot
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
        _global_border_frame_manager.cleanup()
        _global_border_frame_manager = None
