#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CNN数字识别器 - 使用训练好的CNN模型识别数字"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import numpy as np
import cv2
import tensorflow as tf
from tensorflow import keras
import json
from pathlib import Path


class CnnDigitRecognizer:
    """CNN数字识别器（单例模式）"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.model = None
        self.idx_to_label = None
        self.img_width = 28
        self.img_height = 28
        self._initialized = False
    
    def initialize(self, model_path=None, label_map_path=None):
        """初始化识别器"""
        if self._initialized:
            return True
        
        try:
            # 默认路径
            if model_path is None:
                model_path = "deepai/models/digit_cnn.h5"
            if label_map_path is None:
                label_map_path = "deepai/models/label_map.json"
            
            # 检查文件是否存在
            if not os.path.exists(model_path):
                print(f"❌ CNN模型文件不存在: {model_path}")
                print(f"请先运行训练脚本: python deepai/scripts/03_train_model.py")
                return False
            
            if not os.path.exists(label_map_path):
                print(f"❌ 标签映射文件不存在: {label_map_path}")
                return False
            
            # 加载模型
            self.model = keras.models.load_model(model_path)
            
            # 加载标签映射
            with open(label_map_path, 'r', encoding='utf-8') as f:
                self.idx_to_label = json.load(f)
            
            # 转换键为整数
            self.idx_to_label = {int(k): v for k, v in self.idx_to_label.items()}
            
            self._initialized = True
            return True
            
        except Exception as e:
            print(f"❌ CNN识别器初始化失败: {e}")
            return False
    
    def preprocess_image(self, img):
        """预处理图像（与训练时保持一致）"""
        # 转换为灰度图
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        
        # 放大3倍（与Tesseract预处理一致）
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        
        # OTSU二值化
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        return binary
    
    def segment_digits(self, binary, min_width=6, min_height=10):
        """分割单个数字"""
        # 查找轮廓
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # 按x坐标排序（从左到右）
        bounding_boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w >= min_width and h >= min_height:
                bounding_boxes.append((x, y, w, h))
        
        bounding_boxes.sort(key=lambda b: b[0])
        
        # 提取每个数字
        digit_images = []
        for x, y, w, h in bounding_boxes:
            digit_img = binary[y:y+h, x:x+w]
            digit_images.append(digit_img)
        
        return digit_images
    
    def recognize_digit(self, digit_img):
        """识别单个数字"""
        if not self._initialized:
            return None, 0.0
        
        try:
            # 调整到模型输入尺寸
            resized = cv2.resize(digit_img, (self.img_width, self.img_height), 
                                interpolation=cv2.INTER_AREA)
            
            # 归一化
            normalized = resized.astype(np.float32) / 255.0
            
            # 添加批次和通道维度
            input_data = np.expand_dims(normalized, axis=(0, -1))
            
            # 预测
            pred = self.model.predict(input_data, verbose=0)
            
            # 获取结果
            pred_idx = np.argmax(pred[0])
            confidence = pred[0][pred_idx]
            label = self.idx_to_label[pred_idx]
            
            return label, float(confidence)
            
        except Exception as e:
            print(f"❌ 数字识别失败: {e}")
            return None, 0.0
    
    def recognize_and_parse(self, img):
        """识别图像中的数字并解析为整数对（与TesseractOcrManager接口一致）
        
        Args:
            img: BGR格式的图像
            
        Returns:
            tuple: (当前值, 最大值) 或 (None, None)
        """
        if not self._initialized:
            return None, None
        
        try:
            # 预处理
            binary = self.preprocess_image(img)
            
            # 分割数字
            digit_images = self.segment_digits(binary)
            
            if not digit_images:
                return None, None
            
            # 识别每个数字
            result_str = ""
            total_confidence = 0.0
            
            for digit_img in digit_images:
                label, confidence = self.recognize_digit(digit_img)
                if label:
                    result_str += label
                    total_confidence += confidence
            
            if not result_str:
                return None, None
            
            # 解析为整数对
            if '/' in result_str:
                parts = result_str.split('/', 1)
                if len(parts) == 2:
                    try:
                        current = int(parts[0])
                        maximum = int(parts[1])
                        return current, maximum
                    except ValueError:
                        return None, None
            
            return None, None
            
        except Exception as e:
            print(f"❌ 识别失败: {e}")
            return None, None


# 全局单例实例
_cnn_recognizer_instance = None


def get_cnn_recognizer():
    """获取CNN识别器单例"""
    global _cnn_recognizer_instance
    if _cnn_recognizer_instance is None:
        _cnn_recognizer_instance = CnnDigitRecognizer()
    return _cnn_recognizer_instance


if __name__ == "__main__":
    """测试CNN识别器"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python cnn_digit_recognizer.py <image_path>")
        sys.exit(1)
    
    img_path = sys.argv[1]
    img = cv2.imread(img_path)
    
    if img is None:
        print(f"❌ 无法加载图像: {img_path}")
        sys.exit(1)
    
    recognizer = get_cnn_recognizer()
    
    if not recognizer.initialize():
        print("❌ 初始化失败")
        sys.exit(1)
    
    print(f"✅ CNN识别器初始化成功")
    print(f"测试图像: {img_path}")
    
    current, maximum = recognizer.recognize_and_parse(img)
    
    if current is not None:
        print(f"✅ 识别结果: {current}/{maximum}")
    else:
        print(f"❌ 识别失败")
