#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tesseract OCR 管理器 - 用于识别游戏中的数字文本"""

import cv2
import numpy as np
import time
import os
from typing import Tuple, Optional, Dict, Any
import pytesseract


class TesseractOcrManager:
    """使用 Tesseract OCR 引擎识别数字文本"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化 Tesseract OCR 管理器"""
        config = config or {}
        tesseract_cmd = config.get('tesseract_cmd', '')
        if tesseract_cmd and os.path.exists(tesseract_cmd):
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self.lang = config.get('lang', 'eng')
        psm_mode = config.get('psm_mode', 7)
        char_whitelist = config.get('char_whitelist', '0123456789/')
        self.custom_config = f'--psm {psm_mode} -c tessedit_char_whitelist={char_whitelist}'
        print(f"[TesseractOcrManager] 初始化完成，配置: {self.custom_config}")
    
    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """预处理图像 - 优化版本（放大3倍 + OTSU二值化）"""
        # 转灰度
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        
        # 放大3倍提高识别精度
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        
        # OTSU自适应阈值二值化
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return binary
    
    def recognize_and_parse(self, frame: np.ndarray, region: Tuple[int, int, int, int], debug: bool = False) -> Tuple[str, float]:
        """识别并解析文本"""
        try:
            x1, y1, x2, y2 = region
            roi = frame[y1:y2, x1:x2]
            if roi.size == 0:
                return "", -1.0
            processed_roi = self.preprocess_image(roi)
            if debug:
                cv2.imshow("OCR ROI", processed_roi)
                cv2.waitKey(1)
            text = pytesseract.image_to_string(processed_roi, lang=self.lang, config=self.custom_config)
            text = text.strip()
            if not text or '/' not in text:
                return text, -1.0
            parts = text.split('/')
            if len(parts) == 2:
                current_str = "".join(filter(str.isdigit, parts[0]))
                max_str = "".join(filter(str.isdigit, parts[1]))
                if current_str and max_str:
                    current_val = int(current_str)
                    max_val = int(max_str)
                    if max_val > 0:
                        percentage = (current_val / max_val) * 100.0
                        return text, percentage
            return text, -1.0
        except Exception as e:
            print(f"[TesseractOcrManager] 错误: {e}")
            return "", -1.0


_global_tesseract_manager: Optional[TesseractOcrManager] = None

def get_tesseract_ocr_manager(config: Optional[Dict[str, Any]] = None) -> TesseractOcrManager:
    """获取全局实例"""
    global _global_tesseract_manager
    if _global_tesseract_manager is None:
        _global_tesseract_manager = TesseractOcrManager(config)
    return _global_tesseract_manager

def reset_tesseract_ocr_manager():
    """重置全局实例"""
    global _global_tesseract_manager
    _global_tesseract_manager = None
