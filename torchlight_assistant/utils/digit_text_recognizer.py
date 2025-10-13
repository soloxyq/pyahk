#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数字文本识别器 - 使用模板匹配识别HP/MP数值"""

import cv2
import numpy as np
from typing import Tuple, Optional, List
from .digit_template_generator import get_digit_template_generator
from .debug_log import LOG, LOG_INFO, LOG_ERROR


class DigitTextRecognizer:
    """使用模板匹配识别数字文本（如 540/540）"""
    
    def __init__(self, match_threshold: float = 0.75, enable_multiscale: bool = True,
                 scale_range: Tuple[float, float, float] = (0.95, 1.0, 1.05)):
        """
        初始化识别器
        
        Args:
            match_threshold: 模板匹配阈值 (0.0-1.0)，越高越严格
            enable_multiscale: 是否启用多尺度匹配，提升对UI缩放的鲁棒性
            scale_range: 多尺度匹配的缩放范围 (min, default, max)，默认95%-105%
        """
        self.match_threshold = match_threshold
        self.enable_multiscale = enable_multiscale
        self.scale_range = scale_range
        
        # 获取模板生成器
        self.template_generator = get_digit_template_generator()
        self.templates = self.template_generator.get_all_templates()
        
        scale_info = f",多尺度范围{int(scale_range[0]*100)}-{int(scale_range[2]*100)}%" if enable_multiscale else ""
        LOG_INFO(f"[DigitTextRecognizer] 初始化完成，加载了 {len(self.templates)} 个字符模板{scale_info}")
    
    def preprocess_image(self, image: np.ndarray, binary_threshold: int = 220,
                        use_adaptive: bool = False) -> np.ndarray:
        """
        预处理图像以便识别 - 使用自适应阈值提升光照鲁棒性
        
        Args:
            image: 输入图像（可以是灰度或彩色）
            binary_threshold: 全局二值化阈值（当use_adaptive=False时使用）
            use_adaptive: 是否使用自适应阈值（对光照变化更鲁棒）
            
        Returns:
            预处理后的二值图像
        """
        # 转换为灰度图
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        
        # 二值化策略
        if use_adaptive:
            # 自适应阈值 - 对光照变化和背景色变化更鲁棒
            # 使用高斯加权平均，邻域大小11，常数偏移2
            binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY, 11, -2)
        else:
            # 全局二值化 - 提取白色文本
            _, binary = cv2.threshold(gray, binary_threshold, 255, cv2.THRESH_BINARY)
        
        # 轻微的形态学闭运算 - 连接断裂的笔画，但不过度
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 1))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        
        return binary
    
    def extract_char_regions(self, binary_img: np.ndarray) -> List[Tuple[int, int, int, int, np.ndarray]]:
        """
        使用连通域分析提取独立字符区域
        
        Args:
            binary_img: 二值化图像
            
        Returns:
            List[(x, y, w, h, char_img)]: 字符区域列表，按x坐标排序
        """
        # 查找轮廓
        contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        char_regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            
            # 过滤噪点：宽度和高度需要在合理范围
            if w >= 3 and h >= 8 and w <= 20 and h <= 25:
                char_img = binary_img[y:y+h, x:x+w]
                char_regions.append((x, y, w, h, char_img))
        
        # 按x坐标排序（从左到右）
        char_regions.sort(key=lambda r: r[0])
        
        return char_regions
    
    def match_single_char(self, char_img: np.ndarray) -> Tuple[Optional[str], float]:
        """
        匹配单个字符图像 - 支持多尺度匹配提升鲁棒性
        
        Args:
            char_img: 单个字符的二值图像
            
        Returns:
            (最佳匹配字符, 置信度)
        """
        best_char = None
        best_score = 0.0
        
        char_h, char_w = char_img.shape[:2]
        
        # 生成多尺度图像列表（如果启用）
        if self.enable_multiscale:
            scales = [self.scale_range[0], self.scale_range[1], self.scale_range[2]]
            scaled_images = []
            for scale in scales:
                if scale == 1.0:
                    scaled_images.append((char_img, scale))
                else:
                    scaled_h = int(char_h * scale)
                    scaled_w = int(char_w * scale)
                    if scaled_h > 0 and scaled_w > 0:
                        scaled = cv2.resize(char_img, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
                        scaled_images.append((scaled, scale))
        else:
            scaled_images = [(char_img, 1.0)]
        
        # 与每个模板进行匹配
        for char, template in self.templates.items():
            temp_h, temp_w = template.shape[:2]
            
            # 对每个尺度进行匹配
            for test_img, scale in scaled_images:
                test_h, test_w = test_img.shape[:2]
                
                # 尺寸差异太大则跳过（允许±4px差异）
                if abs(test_h - temp_h) > 4 or abs(test_w - temp_w) > 4:
                    continue
                
                try:
                    # 如果尺寸完全相同，直接比较
                    if test_h == temp_h and test_w == temp_w:
                        # 使用归一化相关系数
                        result = cv2.matchTemplate(test_img, template, cv2.TM_CCOEFF_NORMED)
                        score = result[0, 0]
                    else:
                        # 尺寸不同，需要缩放或在较大图像中搜索
                        if test_h >= temp_h and test_w >= temp_w:
                            # 字符图像更大，在其中搜索模板
                            result = cv2.matchTemplate(test_img, template, cv2.TM_CCOEFF_NORMED)
                            _, score, _, _ = cv2.minMaxLoc(result)
                        else:
                            # 模板更大，在模板中搜索字符
                            result = cv2.matchTemplate(template, test_img, cv2.TM_CCOEFF_NORMED)
                            _, score, _, _ = cv2.minMaxLoc(result)
                    
                    if score > best_score:
                        best_score = score
                        best_char = char
                        
                except Exception as e:
                    continue
        
        return best_char, best_score
    
    def recognize_text_in_region(self, frame: np.ndarray, region: Tuple[int, int, int, int],
                                 debug: bool = False) -> str:
        """
        识别指定区域内的数字文本 - 使用连通域分割方法
        
        Args:
            frame: 原始图像帧
            region: 区域坐标 (x1, y1, x2, y2)
            debug: 是否输出调试信息
            
        Returns:
            识别出的文本字符串（如 "540/540"）
        """
        x1, y1, x2, y2 = region
        
        # 裁剪区域
        roi = frame[y1:y2, x1:x2]
        
        if roi.size == 0:
            LOG_ERROR(f"[DigitTextRecognizer] 无效的区域: {region}")
            return ""
        
        # 预处理图像
        processed = self.preprocess_image(roi)
        
        if debug:
            print(f"[DEBUG] ROI尺寸: {processed.shape}, 区域: {region}")
        
        # 提取独立字符
        char_regions = self.extract_char_regions(processed)
        
        if debug:
            print(f"[DEBUG] 提取到 {len(char_regions)} 个字符区域")
        
        # 识别每个字符
        recognized_text = ""
        for i, (x, y, w, h, char_img) in enumerate(char_regions):
            char, score = self.match_single_char(char_img)
            
            if char is not None and score >= self.match_threshold:
                recognized_text += char
                if debug:
                    print(f"  字符{i+1}: 位置({x},{y}), 尺寸{w}x{h}, 识别'{char}', 置信度{score:.3f}")
            else:
                if debug:
                    best_char = char if char else "?"
                    print(f"  字符{i+1}: 位置({x},{y}), 尺寸{w}x{h}, 最佳'{best_char}', 置信度{score:.3f} (低于阈值{self.match_threshold})")
                # 置信度太低，可能是噪点，跳过
        
        if debug:
            print(f"[DEBUG] 最终识别: '{recognized_text}'")
        
        return recognized_text
    
    def parse_hp_mp_text(self, text: str) -> Tuple[int, int, float]:
        """
        解析HP/MP文本格式（如 "540/540"）
        
        Args:
            text: 识别出的文本
            
        Returns:
            (当前值, 最大值, 百分比) 如果解析失败返回 (0, 0, 0.0)
        """
        try:
            # 移除空格
            text = text.strip()
            
            # 查找斜杠
            if '/' not in text:
                LOG_ERROR(f"[DigitTextRecognizer] 文本格式错误，缺少斜杠: '{text}'")
                return 0, 0, 0.0
            
            # 分割
            parts = text.split('/')
            if len(parts) != 2:
                LOG_ERROR(f"[DigitTextRecognizer] 文本格式错误: '{text}'")
                return 0, 0, 0.0
            
            # 解析数字
            current = int(parts[0])
            maximum = int(parts[1])
            
            # 计算百分比
            if maximum > 0:
                percentage = (current / maximum) * 100.0
            else:
                percentage = 0.0
            
            return current, maximum, percentage
        
        except Exception as e:
            LOG_ERROR(f"[DigitTextRecognizer] 解析文本失败: '{text}', 错误: {e}")
            return 0, 0, 0.0
    
    def recognize_and_parse(self, frame: np.ndarray, region: Tuple[int, int, int, int],
                           debug: bool = False) -> Tuple[str, float]:
        """
        识别并解析HP/MP文本，直接返回百分比
        
        Args:
            frame: 原始图像帧
            region: 区域坐标 (x1, y1, x2, y2)
            debug: 是否输出调试信息
            
        Returns:
            (识别的文本, 百分比)
        """
        # 识别文本
        text = self.recognize_text_in_region(frame, region, debug)
        
        if not text:
            return "", 0.0
        
        # 解析
        current, maximum, percentage = self.parse_hp_mp_text(text)
        
        if debug:
            LOG_INFO(f"[DigitTextRecognizer] 解析结果: {current}/{maximum} = {percentage:.1f}%")
        
        return text, percentage


# 全局单例
_global_recognizer: Optional[DigitTextRecognizer] = None


def get_digit_text_recognizer(match_threshold: float = 0.75, enable_multiscale: bool = True) -> DigitTextRecognizer:
    """
    获取全局数字识别器实例
    
    Args:
        match_threshold: 模板匹配阈值
        enable_multiscale: 是否启用多尺度匹配
    """
    global _global_recognizer
    if _global_recognizer is None:
        _global_recognizer = DigitTextRecognizer(match_threshold, enable_multiscale)
    return _global_recognizer


if __name__ == "__main__":
    # 测试代码
    print("测试数字文本识别器...")
    
    # 读取测试图片
    img = cv2.imread('debug_frame_0002.png')
    if img is None:
        print("❌ 找不到测试图片 debug_frame_0002.png")
        exit(1)
    
    recognizer = get_digit_text_recognizer(match_threshold=0.70)
    
    # 测试HP区域
    hp_region = (97, 814, 218, 835)
    print(f"\n测试HP区域 {hp_region}:")
    hp_text, hp_percentage = recognizer.recognize_and_parse(img, hp_region, debug=True)
    print(f"  结果: '{hp_text}' -> {hp_percentage:.1f}%")
    
    # 测试MP区域
    mp_region = (1767, 814, 1894, 835)
    print(f"\n测试MP区域 {mp_region}:")
    mp_text, mp_percentage = recognizer.recognize_and_parse(img, mp_region, debug=True)
    print(f"  结果: '{mp_text}' -> {mp_percentage:.1f}%")
    
    print("\n✅ 测试完成！")
