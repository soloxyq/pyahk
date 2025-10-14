#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DeepAI - HP/MP数字识别AI模块

独立可复用的AI包，提供三种识别引擎：
1. Tesseract OCR - 95-100%准确率，无需训练，~240ms
2. 数字模板匹配 - 90-95%准确率，零额外依赖，~60ms
3. Keras神经网络 - >99%准确率，需要一次训练，~47ms (5倍速提升)

Usage:
    from deepai import get_recognizer
    
    # Keras引擎（推荐，最快）
    recognizer = get_recognizer('keras')
    hp_current, hp_max = recognizer.recognize_and_parse(hp_img)
    
    # 模板匹配引擎（无需训练）
    recognizer = get_recognizer('template')
    hp_current, hp_max = recognizer.recognize_and_parse(hp_img)
"""

__version__ = "1.0.0"
__author__ = "Torchlight Assistant Team"

from .recognizer import get_recognizer

__all__ = ['get_recognizer']
