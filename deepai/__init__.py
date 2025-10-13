#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeepAI - HP/MP数字识别AI模块

独立可复用的AI包，提供三种识别引擎：
1. Tesseract OCR - 100%准确率，无需训练
2. 数字模板匹配 - 90-95%准确率，零额外依赖
3. TFLite神经网络 - >98%准确率，8-12ms识别速度

Usage:
    from deepai import get_recognizer
    
    recognizer = get_recognizer('tflite')
    hp_current, hp_max = recognizer.recognize_and_parse(hp_img)
"""

__version__ = "1.0.0"
__author__ = "Torchlight Assistant Team"

from .recognizer import get_recognizer

__all__ = ['get_recognizer']
