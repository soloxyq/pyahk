#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于C++库的图形捕获管理器

这个模块使用我们自己编译的C++库来替代有问题的Python WinRT绑定，
提供稳定可靠的游戏画面捕获功能。
"""

import os
import sys
import threading
import time
from typing import Optional, Callable, Any, Tuple
import numpy as np
from .debug_log import LOG, LOG_ERROR, LOG_INFO
from dataclasses import dataclass


# 添加native_capture目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
native_capture_dir = os.path.join(project_root, "native_capture")
sys.path.insert(0, native_capture_dir)

try:
    # 使用封装的CaptureManager类，不直接导入C++库接口
    from python_wrapper import CaptureManager
except ImportError as e:
    LOG_ERROR(f"警告: 无法导入CaptureManager: {e}")
    CaptureManager = None


@dataclass
class CaptureConfig:
    """捕获配置"""

    target_window_title: str = ""  # 目标窗口标题
    target_window_handle: Optional[int] = None  # 目标窗口句柄
    capture_monitor: bool = False  # 是否捕获显示器
    monitor_index: int = 0  # 显示器索引
    frame_callback: Optional[Callable] = None  # 帧回调函数

    # 新增的捕获配置参数
    capture_interval_ms: int = 60  # 捕获间隔(毫秒)，默认值，实际由用户界面配置
    enable_region: bool = False  # 是否启用区域捕获
    region_x: int = 0  # 捕获区域X坐标
    region_y: int = 0  # 捕获区域Y坐标
    region_width: int = 0  # 捕获区域宽度
    region_height: int = 0  # 捕获区域高度


class NativeGraphicsCaptureManager:
    """基于C++库的图形捕获管理器"""

    def __init__(self, config: CaptureConfig):
        """初始化捕获管理器

        Args:
            config: 捕获配置
        """
        self.config = config
        self.capture_manager: Optional[CaptureManager] = None
        self.session_id: Optional[int] = None  # 使用session_id
        self.is_running = False
        self.capture_thread: Optional[threading.Thread] = None

        # Python层面不再维护帧缓存，完全依赖C++层面的永久缓存机制

    def initialize(self) -> bool:
        """初始化捕获库

        Returns:
            bool: 是否初始化成功
        """
        try:
            LOG(f"[捕获初始化] 开始初始化捕获库")
            if CaptureManager is None:
                LOG_ERROR("[捕获初始化] 错误: CaptureManager未能正确导入")
                return False

            # 使用CaptureManager封装类
            LOG(f"[捕获初始化] 创建CaptureManager实例")
            self.capture_manager = CaptureManager()
            LOG(f"[捕获初始化] 调用CaptureManager.initialize()")
            if not self.capture_manager.initialize():
                LOG_ERROR(f"[捕获初始化] CaptureManager.initialize()返回失败")
                return False

            LOG_INFO(f"[捕获初始化] 捕获库初始化成功")
            return True

        except Exception as e:
            LOG_ERROR(f"[捕获初始化] 初始化捕获库异常: {e}")
            return False

    def start_capture(self) -> bool:
        """开始捕获

        Returns:
            bool: 是否成功开始捕获
        """
        LOG(f"[捕获启动] 开始启动捕获")
        if self.is_running:
            LOG(f"[捕获启动] 捕获已在运行中")
            return True

        if not self.capture_manager:
            LOG(f"[捕获启动] capture_manager未初始化，开始初始化")
            if not self.initialize():
                LOG_ERROR(f"[捕获启动] 初始化失败")
                return False

        # 创建捕获会话
        LOG(f"[捕获启动] 开始创建捕获会话")
        if not self._create_capture_session():
            LOG_ERROR(f"[捕获启动] 创建捕获会话失败")
            return False

        # 开始捕获
        LOG_INFO(f"[捕获启动] 开始启动捕获会话")
        if not self._start_capture_session():
            LOG_ERROR(f"[捕获启动] 启动捕获会话失败")
            return False

        self.is_running = True
        LOG_INFO(f"[捕获启动] 捕获启动成功")

        # 如果配置了回调函数，启动帧更新线程
        if self.config.frame_callback:
            LOG(f"[捕获启动] 启动帧更新线程")
            self.capture_thread = threading.Thread(
                target=self._frame_update_loop, daemon=True
            )
            self.capture_thread.start()

        return True

    def _create_capture_session(self) -> bool:
        """创建捕获会话"""
        try:
            LOG(f"[捕获会话] 开始创建捕获会话")
            # 准备捕获配置
            capture_config = {
                "capture_interval_ms": self.config.capture_interval_ms,
                "enable_region": self.config.enable_region,
                "region": {
                    "x": self.config.region_x,
                    "y": self.config.region_y,
                    "width": self.config.region_width,
                    "height": self.config.region_height,
                },
            }
            LOG(f"[捕获会话] 捕获配置: {capture_config}")

            if self.config.capture_monitor:
                # 显示器捕获
                LOG(f"[捕获会话] 创建显示器捕获会话，显示器索引: {self.config.monitor_index}")
                self.session_id = self.capture_manager.create_monitor_session(
                    self.config.monitor_index, capture_config
                )
                if self.session_id is None:
                    LOG_ERROR(f"[捕获会话] 创建显示器捕获会话失败")
                    return False
                LOG_INFO(f"[捕获会话] 成功创建显示器捕获会话，session_id: {self.session_id}")

            else:
                # 窗口捕获
                window_handle = self._get_target_window()
                if not window_handle:
                    LOG_ERROR(f"[捕获会话] 获取目标窗口失败")
                    return False

                LOG_INFO(f"[捕获会话] 创建窗口捕获会话，窗口句柄: {window_handle}")
                self.session_id = self.capture_manager.create_window_session(
                    window_handle, capture_config
                )
                if self.session_id is None:
                    LOG_ERROR(f"[捕获会话] 创建窗口捕获会话失败")
                    return False

                try:
                    window_title = self.capture_manager.get_window_title(window_handle)
                    LOG_INFO(f"[捕获会话] 成功创建窗口捕获会话，session_id: {self.session_id}, 窗口标题: {window_title}")
                except UnicodeDecodeError as e:
                    LOG_ERROR(f"[捕获会话] 获取窗口标题时编码错误: {e}")
                    LOG_INFO(f"[捕获会话] 成功创建窗口捕获会话，session_id: {self.session_id}, 窗口标题: <编码错误>")
                except Exception as e:
                    LOG_ERROR(f"[捕获会话] 获取窗口标题时异常: {e}")
                    LOG_INFO(f"[捕获会话] 成功创建窗口捕获会话，session_id: {self.session_id}, 窗口标题: <获取失败>")

            # 记录配置信息
            if self.config.enable_region:
                LOG_INFO(f"[捕获会话] 启用区域捕获: ({self.config.region_x}, {self.config.region_y}, {self.config.region_width}, {self.config.region_height})")

            return True

        except Exception as e:
            LOG_ERROR(f"[捕获会话] 创建捕获会话异常: {e}")
            return False

    def _get_target_window(self) -> Optional[int]:
        """获取目标窗口句柄"""
        if self.config.target_window_handle:
            return self.config.target_window_handle

        if self.config.target_window_title:
            window_handle = self.capture_manager.find_window_by_title(
                self.config.target_window_title
            )
            if window_handle:
                return window_handle
            else:
                return None

        return None

    def _start_capture_session(self) -> bool:
        """开始捕获会话"""
        try:
            LOG_INFO(f"[捕获会话] 开始启动捕获会话，session_id: {self.session_id}")
            # 使用现有的start_capture接口
            if not self.capture_manager.start_capture(self.session_id):
                LOG_ERROR(f"[捕获会话] 启动捕获会话失败，session_id: {self.session_id}")
                return False

            LOG_INFO(f"[捕获会话] 成功启动捕获会话，session_id: {self.session_id}")
            return True

        except Exception as e:
            LOG_ERROR(f"[捕获会话] 启动捕获会话异常: {e}")
            return False

    def _update_latest_frame(self):
        """更新最新帧（用于支持frame_callback配置）"""
        if not self.is_running or not self.config.frame_callback:
            return

        try:
            # 直接从C++库获取帧，不在Python层面缓存
            frame_data = self.capture_manager.get_frame(self.session_id)
            if frame_data is not None:
                # 直接调用用户回调，不缓存
                if self.config.frame_callback:
                    self.config.frame_callback(
                        frame_data, int(time.time() * 1000), None
                    )

        except Exception as e:
            LOG_ERROR(f"在捕获的后台操作中发生异常: {e}")

    def _frame_update_loop(self):
        """帧更新循环（用于回调模式），动态调整休眠时间。"""

        while self.is_running:
            try:
                self._update_latest_frame()

                # 动态计算休眠时间，为C++捕获间隔的一半，确保能及时获取新帧
                # 最小休眠时间设为1ms，避免CPU空转
                cpp_interval_ms = self.config.capture_interval_ms
                sleep_interval_s = max(0.001, (cpp_interval_ms / 2) / 1000.0)

                time.sleep(sleep_interval_s)
            except Exception as e:
                # 发生异常时退出循环，避免无限错误
                break

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """获取最新帧（直接从C++库获取，帧缓存在C++层面实现）

        Returns:
            Optional[np.ndarray]: 最新帧数据，格式为(H, W, 3) RGB
        """
        if not self.is_running:
            return None

        try:
            # 直接从C++库获取帧，C++层面已实现永久缓存机制
            if self.session_id is not None:
                frame_data = self.capture_manager.get_frame(self.session_id)
                return frame_data

            return None

        except Exception as e:
            LOG_ERROR(f"获取最新帧异常: {e}")
            return None

    def capture_single_frame(self) -> Optional[np.ndarray]:
        """捕获单帧（一次性操作）

        Returns:
            Optional[np.ndarray]: 捕获的帧数据
        """
        if not self.is_running:
            if not self.start_capture():
                return None

            # 等待捕获稳定
            time.sleep(0.5)

        frame = self.get_latest_frame()
        return frame

    def stop_capture(self):
        """停止捕获并等待线程结束"""
        if not self.is_running:
            return

        self.is_running = False

        # 等待捕获线程结束
        if self.capture_thread and self.capture_thread.is_alive():
            try:
                self.capture_thread.join(timeout=1.0)
            except Exception as e:
                LOG_ERROR(f"等待捕获线程时异常: {e}")
        self.capture_thread = None

        try:
            if self.capture_manager and self.session_id is not None:
                self.capture_manager.stop_capture(self.session_id)
                self.capture_manager.destroy_session(self.session_id)
                self.session_id = None

        except Exception as e:
            LOG_ERROR(f"停止或销毁捕获会话时异常: {e}")

    def pause_capture(self):
        """暂停捕获（停止C++库捕获，节省CPU资源）"""
        if not self.is_running:
            return

        try:
            if self.capture_manager and self.session_id is not None:
                # 使用stop_capture停止捕获，但不销毁会话
                self.capture_manager.stop_capture(self.session_id)
        except Exception as e:
            LOG_ERROR(f"在捕获的后台操作中发生异常: {e}")

    def resume_capture(self):
        """恢复捕获（重新启动C++库捕获）"""
        if not self.is_running:
            return

        try:
            if self.capture_manager and self.session_id is not None:
                # 使用start_capture重新启动捕获
                result = self.capture_manager.start_capture(self.session_id)
                if result:  # 布尔值，True表示成功
                    # 清理缓存，避免使用暂停前的旧帧
                    self.clear_cache()
        except Exception as e:
            LOG_ERROR(f"在捕获的后台操作中发生异常: {e}")

    def clear_cache(self):
        """清理C++层的帧缓存"""
        # 清理C++层的核心缓存
        if self.capture_manager and self.session_id is not None:
            try:
                self.capture_manager.clear_frame_cache(self.session_id)
            except Exception as e:
                LOG_ERROR(f"清理C++帧缓存时异常: {e}")

    def cleanup(self):
        """清理资源"""
        self.stop_capture()

        if self.capture_manager:
            self.capture_manager.cleanup()
            self.capture_manager = None

    def restart_capture(self) -> bool:
        """手动重启捕获（非自动）"""

        self.stop_capture()
        time.sleep(1.0)  # 等待清理完成
        return self.start_capture()

    def set_capture_config(self, config_dict: dict) -> bool:
        """设置捕获配置

        Args:
            config_dict: 配置字典，包含:
                - capture_interval_ms: 捕获间隔(毫秒)
                - region: 捕获区域 {'x': int, 'y': int, 'width': int, 'height': int}
                - enable_region: 是否启用区域捕获

        Returns:
            bool: 是否设置成功
        """
        if not self.is_running or self.session_id is None:
            return False

        try:
            # 更新本地配置
            if "capture_interval_ms" in config_dict:
                self.config.capture_interval_ms = config_dict["capture_interval_ms"]

            if "enable_region" in config_dict:
                self.config.enable_region = config_dict["enable_region"]

            if "region" in config_dict:
                region = config_dict["region"]
                self.config.region_x = region.get("x", 0)
                self.config.region_y = region.get("y", 0)
                self.config.region_width = region.get("width", 0)
                self.config.region_height = region.get("height", 0)

            # 设置到C++库
            success = self.capture_manager.set_capture_config(
                self.session_id, config_dict
            )
            return success

        except Exception as e:
            LOG_ERROR(f"设置捕获配置时异常: {e}")
            return False

    def get_capture_config(self) -> Optional[dict]:
        """获取当前捕获配置

        Returns:
            Optional[dict]: 配置字典，失败返回None
        """
        if not self.is_running or self.session_id is None:
            # 返回本地配置
            return {
                "capture_interval_ms": self.config.capture_interval_ms,
                "enable_region": self.config.enable_region,
                "region": {
                    "x": self.config.region_x,
                    "y": self.config.region_y,
                    "width": self.config.region_width,
                    "height": self.config.region_height,
                },
            }

        try:
            # 从C++库获取配置
            config = self.capture_manager.get_capture_config(self.session_id)
            return config

        except Exception as e:
            LOG_ERROR(f"获取捕获配置时异常: {e}")
            return None

    def get_capture_info(self) -> dict:
        """获取捕获信息"""
        info = {
            "is_running": self.is_running,
            "session_id": self.session_id,
            "capture_lib_initialized": self.capture_manager is not None,
            "config": {
                "target_window_title": self.config.target_window_title,
                "target_window_handle": self.config.target_window_handle,
                "capture_monitor": self.config.capture_monitor,
                "monitor_index": self.config.monitor_index,
                "capture_interval_ms": self.config.capture_interval_ms,
                "enable_region": self.config.enable_region,
                "region_x": self.config.region_x,
                "region_y": self.config.region_y,
                "region_width": self.config.region_width,
                "region_height": self.config.region_height,
            },
        }

        # 移除Python层面的帧缓存状态检查

        return info

    def __enter__(self):
        """上下文管理器入口"""
        if not self.initialize():
            raise RuntimeError("初始化捕获管理器失败")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.cleanup()

    def __del__(self):
        """析构函数"""
        self.cleanup()


# 便捷函数
def create_window_capture_manager(
    window_title: str, frame_callback: Optional[Callable] = None
) -> NativeGraphicsCaptureManager:
    """创建窗口捕获管理器

    Args:
        window_title: 窗口标题
        frame_callback: 帧回调函数

    Returns:
        NativeGraphicsCaptureManager: 捕获管理器实例
    """
    config = CaptureConfig(
        target_window_title=window_title, frame_callback=frame_callback
    )
    return NativeGraphicsCaptureManager(config)


def create_monitor_capture_manager(
    monitor_index: int = 0, frame_callback: Optional[Callable] = None
) -> NativeGraphicsCaptureManager:
    """创建显示器捕获管理器

    Args:
        monitor_index: 显示器索引
        frame_callback: 帧回调函数

    Returns:
        NativeGraphicsCaptureManager: 捕获管理器实例
    """
    config = CaptureConfig(
        capture_monitor=True, monitor_index=monitor_index, frame_callback=frame_callback
    )
    return NativeGraphicsCaptureManager(config)


def quick_capture_window(
    window_title: str, save_path: Optional[str] = None
) -> Optional[np.ndarray]:
    """快速捕获窗口

    Args:
        window_title: 窗口标题
        save_path: 保存路径（可选）

    Returns:
        Optional[np.ndarray]: 捕获的帧数据
    """
    try:
        with create_window_capture_manager(window_title) as manager:
            frame = manager.capture_single_frame()

            if frame is not None and save_path:
                from PIL import Image

                Image.fromarray(frame).save(save_path)

            return frame

    except Exception as e:
        return None


if __name__ == "__main__":
    # 测试代码

    LOG_INFO("测试Native Graphics Capture Manager")

    # 测试窗口捕获
    try:
        frame = quick_capture_window("记事本", "test_native_capture.png")
        if frame is not None:
            LOG_INFO(f"捕获成功! 图像尺寸: {frame.shape}")
        else:
            LOG_ERROR("捕获失败")
    except Exception as e:
        LOG_ERROR(f"测试失败: {e}")
        import traceback

        traceback.print_exc()
