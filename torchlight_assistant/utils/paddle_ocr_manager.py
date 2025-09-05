#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A dedicated manager for handling PaddleOCR initialization and execution."""

import threading
import time
from typing import List, Optional
import numpy as np
from paddleocr import PaddleOCR
from ..core.event_bus import event_bus
from .debug_log import LOG, LOG_INFO, LOG_ERROR


class PaddleOCRManager:
    """Wraps the PaddleOCR instance to control its lifecycle and provide a simple interface."""

    _instance: Optional["PaddleOCRManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PaddleOCRManager, cls).__new__(cls)
            cls._instance._init_internal()
        return cls._instance

    def _init_internal(self):
        """内部初始化方法"""
        self._initialized = False
        self._initializing = False
        self._init_error = None
        self.ocr = None
        self._init_lock = threading.Lock()

        # 不自动初始化，等待用户手动触发

    def start_async_initialization(self):
        """开始异步初始化OCR引擎"""
        if self._initializing or self._initialized:
            LOG(f"[PaddleOCR] OCR引擎已在初始化中或已完成初始化")
            return

        self._initializing = True
        LOG_INFO("[PaddleOCR] 开始异步初始化PaddleOCR引擎...")

        def _async_init():
            try:
                with self._init_lock:
                    if self._initialized:
                        return

                    LOG("[PaddleOCR] 正在初始化PaddleOCR引擎（这可能需要一些时间）...")
                    # 优化的PaddleOCR配置以提高性能
                    self.ocr = PaddleOCR(
                        lang="ch",
                        use_textline_orientation=False,  # 关闭文本行方向检测
                        use_doc_orientation_classify=False,  # 关闭文档方向分类
                        use_doc_unwarping=False,  # 关闭文档反弯曲
                        det_limit_side_len=960,  # 减小检测图像边长限制，提高速度
                        det_limit_type='min',  # 使用最小边长限制
                        rec_batch_num=6,  # 优化批处理大小
                        enable_mkldnn=True,  # 启用MKL-DNN加速（如果可用）
                        cpu_threads=2,  # 限制CPU线程数，避免占用过多资源
                        show_log=False  # 关闭详细日志
                    )
                    self._initialized = True
                    self._initializing = False
                    LOG_INFO("[PaddleOCR] PaddleOCR引擎异步初始化成功！")
                    event_bus.publish("ocr:init_success")

            except Exception as e:
                self._init_error = str(e)
                self._initializing = False
                LOG_ERROR(f"[PaddleOCR] 异步初始化PaddleOCR引擎失败: {e}")
                event_bus.publish("ocr:init_failed", {"error": str(e)})

        # 在后台线程中初始化
        init_thread = threading.Thread(target=_async_init, daemon=True)
        init_thread.start()

    def get_initialization_status(self) -> dict:
        """获取初始化状态信息"""
        return {
            "initialized": self._initialized,
            "initializing": self._initializing,
            "error": self._init_error,
        }

    def wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """等待初始化完成"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._initialized:
                return True
            if self._init_error:
                return False
            time.sleep(0.1)
        return False

    def is_ready(self) -> bool:
        """检查OCR引擎是否就绪"""
        return self._initialized and self.ocr is not None

    def get_text_from_image(self, frame: np.ndarray) -> List[str]:
        """
        Extracts recognized text from a given image frame.

        Args:
            frame: The image frame (numpy array) to process.

        Returns:
            A list of recognized text strings
        """
        if frame is None:
            return []

        # 如果还在初始化中，等待一下
        if self._initializing and not self._initialized:
            LOG(f"[PaddleOCR] OCR引擎正在初始化中，等待完成...")
            if not self.wait_for_initialization(timeout=10.0):
                LOG_ERROR("[PaddleOCR] OCR引擎初始化超时或失败")
                return []

        if not self.is_ready():
            LOG_ERROR("[PaddleOCR] OCR引擎未就绪")
            return []

        try:
            # 性能优化：记录处理时间
            start_time = time.time()
            
            with self._init_lock:
                # 使用新版本的predict方法
                result = self.ocr.predict(input=frame)
            
            processing_time = time.time() - start_time
            
            if not result or len(result) == 0:
                LOG(f"[PaddleOCR] 未识别到文本 (处理时间: {processing_time:.3f}s)")
                return []

            # 新版本PaddleOCR返回OCRResult对象列表
            ocr_result = result[0]  # 取第一个结果

            # 安全地提取文字内容
            extracted = []

            # 检查是否有识别到的文本
            if "rec_texts" in ocr_result and "rec_scores" in ocr_result:
                rec_texts = ocr_result["rec_texts"]
                rec_scores = ocr_result["rec_scores"]

                for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                    try:
                        if score > 0.5:  # 只保留置信度>0.5的结果
                            extracted.append(text)
                    except (IndexError, TypeError) as e:
                        LOG_ERROR(f"[PaddleOCR] 解析OCR结果第{i}项时出错: {e}")
                        continue

            LOG_INFO(f"[PaddleOCR] 成功提取 {len(extracted)} 个文本 (处理时间: {processing_time:.3f}s): {extracted}")
            return extracted
        except Exception as e:
            LOG_ERROR(f"[PaddleOCR] OCR处理过程中出错: {e}")
            return []


# 全局实例，程序启动时就开始初始化
_global_ocr_manager = None


def get_paddle_ocr_manager() -> PaddleOCRManager:
    """获取全局OCR管理器实例"""
    global _global_ocr_manager
    if _global_ocr_manager is None:
        _global_ocr_manager = PaddleOCRManager()
    return _global_ocr_manager
