#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tesseract OCR 管理器 - 用于识别游戏中的数字文本"""

import cv2
import numpy as np
import os
import threading
import time
from typing import Tuple, Optional, Dict, Any
import pytesseract

from .config_values import config_int


DEFAULT_TESSERACT_CONFIG = {
    "tesseract_cmd": "D:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    "lang": "eng",
    "psm_mode": 7,
    "char_whitelist": "0123456789/",
}


def normalize_tesseract_config(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回可比较、可直接构造识别器的 Tesseract 配置。"""
    supplied = config if isinstance(config, dict) else {}
    merged = {**DEFAULT_TESSERACT_CONFIG, **supplied}
    try:
        psm_mode = config_int(merged.get("psm_mode", 7))
    except ValueError:
        psm_mode = 7
    if not 0 <= psm_mode <= 13:
        psm_mode = 7
    return {
        "tesseract_cmd": str(merged.get("tesseract_cmd", "") or "").strip(),
        "lang": str(merged.get("lang", "eng") or "eng").strip() or "eng",
        "psm_mode": psm_mode,
        "char_whitelist": str(
            merged.get("char_whitelist", "0123456789/") or "0123456789/"
        ),
    }


def tesseract_config_signature(config: Optional[Dict[str, Any]] = None) -> tuple:
    """生成稳定签名，供运行中配置切换判断是否需要重建。"""
    normalized = normalize_tesseract_config(config)
    return tuple(normalized[key] for key in DEFAULT_TESSERACT_CONFIG)


class TesseractOcrManager:
    """使用 Tesseract OCR 引擎识别数字文本"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化 Tesseract OCR 管理器"""
        self.config = normalize_tesseract_config(config)
        configured_cmd = self.config["tesseract_cmd"]
        # 不让一个无效的新路径继承上一个配置写入 pytesseract 的全局路径。
        # 路径无效时回退 PATH 中的 tesseract，与 pytesseract 默认语义一致。
        self.tesseract_cmd = (
            configured_cmd if configured_cmd and os.path.exists(configured_cmd) else "tesseract"
        )
        self.lang = self.config["lang"]
        psm_mode = self.config["psm_mode"]
        char_whitelist = self.config["char_whitelist"]
        self.custom_config = f"--psm {psm_mode} -c tessedit_char_whitelist={char_whitelist}"
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
            # pytesseract 把可执行文件路径放在模块级全局变量中。配置热切换后旧识别
            # 调用仍可能在途，因此必须在同一把锁内按实例设置、调用并恢复，不能让
            # 两个配置互相借用对方的 executable。
            with _global_tesseract_lock:
                previous_cmd = pytesseract.pytesseract.tesseract_cmd
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
                try:
                    text = pytesseract.image_to_string(
                        processed_roi, lang=self.lang, config=self.custom_config
                    )
                finally:
                    pytesseract.pytesseract.tesseract_cmd = previous_cmd
            text = text.strip()
            if not text or '/' not in text:
                return text, -1.0
            parts = text.split('/')
            if len(parts) == 2:
                # 移除逗号和其他非数字字符
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
_global_tesseract_signature: Optional[tuple] = None
_global_tesseract_lock = threading.RLock()

def get_tesseract_ocr_manager(config: Optional[Dict[str, Any]] = None) -> TesseractOcrManager:
    """获取与请求配置匹配的全局实例。

    显式传入配置时按规范化签名比较；签名变化就在锁内构造替代实例并原子换指针，
    从而使“加载另一份配置”立即生效。未传配置时保留当前实例，首次调用才用默认值。
    """
    global _global_tesseract_manager, _global_tesseract_signature
    requested_signature = tesseract_config_signature(config)
    with _global_tesseract_lock:
        needs_rebuild = _global_tesseract_manager is None
        if config is not None and requested_signature != _global_tesseract_signature:
            needs_rebuild = True
        if needs_rebuild:
            replacement = TesseractOcrManager(config)
            _global_tesseract_manager = replacement
            _global_tesseract_signature = requested_signature
        return _global_tesseract_manager

def reset_tesseract_ocr_manager():
    """重置全局实例(同样进锁:不进锁会与并发的 get 构成丢失更新 —— get 刚赋值就被置 None,
    下一个 get 再建一个实例并再写一次 pytesseract 全局 cmd 路径)。"""
    global _global_tesseract_manager, _global_tesseract_signature
    with _global_tesseract_lock:
        _global_tesseract_manager = None
        _global_tesseract_signature = None
