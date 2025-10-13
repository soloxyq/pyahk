#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tesseract OCR 性能基准测试（实际运行场景）"""

import cv2
import time
import numpy as np
from torchlight_assistant.core.config_manager import ConfigManager
from torchlight_assistant.utils.tesseract_ocr_manager import get_tesseract_ocr_manager


def benchmark_ocr():
    """性能基准测试 - 模拟实际ResourceManager运行场景"""
    
    print("=" * 80)
    print("Tesseract OCR 性能基准测试")
    print("=" * 80)
    
    # 1. 预加载OCR（模拟程序启动）
    init_start = time.perf_counter()
    config_manager = ConfigManager()
    global_config = config_manager.load_config("default.json")
    tesseract_config = global_config.get("global", {}).get("tesseract_ocr", {})
    ocr_manager = get_tesseract_ocr_manager(tesseract_config)
    init_time = (time.perf_counter() - init_start) * 1000
    print(f"✓ Tesseract OCR 初始化耗时: {init_time:.2f}ms\n")
    
    # 2. 加载测试图像（转换为RGB模拟Graphics Capture）
    test_image_path = "debug_frame_0002.png"
    frame_bgr = cv2.imread(test_image_path)
    if frame_bgr is None:
        print(f"❌ 无法加载测试图像: {test_image_path}")
        print(f"   请确保文件存在: {test_image_path}")
        return
    
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    
    hp_region = (97, 814, 218, 835)
    mp_region = (1767, 814, 1894, 835)
    
    iterations = 20
    
    print("=" * 80)
    print("实际运行场景测试")
    print("=" * 80)
    
    # 测试HP+MP顺序检测（模拟ResourceManager.check_and_execute_resources）
    print("\n顺序检测 HP + MP（实际运行逻辑）:")
    print("-" * 40)
    
    sequential_times = []
    hp_text_result = None
    mp_text_result = None
    
    for i in range(iterations):
        start = time.perf_counter()
        
        # 检查HP
        hp_text, hp_percentage = ocr_manager.recognize_and_parse(frame_rgb, hp_region)
        
        # 检查MP（使用相同的frame）
        mp_text, mp_percentage = ocr_manager.recognize_and_parse(frame_rgb, mp_region)
        
        elapsed = (time.perf_counter() - start) * 1000
        sequential_times.append(elapsed)
        
        if i == 0:
            hp_text_result = (hp_text, hp_percentage)
            mp_text_result = (mp_text, mp_percentage)
    
    sequential_avg = sum(sequential_times) / len(sequential_times)
    sequential_min = min(sequential_times)
    sequential_max = max(sequential_times)
    
    print(f"  HP识别: '{hp_text_result[0]}' → {hp_text_result[1]:.2f}%")
    print(f"  MP识别: '{mp_text_result[0]}' → {mp_text_result[1]:.2f}%")
    print(f"\n  平均总耗时: {sequential_avg:.2f}ms")
    print(f"  最快: {sequential_min:.2f}ms")
    print(f"  最慢: {sequential_max:.2f}ms")
    
    # 性能分析
    print("\n" + "=" * 80)
    print("性能分析与建议")
    print("=" * 80)
    
    print(f"\n初始化开销: {init_time:.2f}ms（程序启动时一次）")
    print(f"单次检测耗时: {sequential_avg:.2f}ms（HP+MP顺序检测）")
    
    # 推荐配置
    if sequential_avg < 150:
        rating = "🟢 优秀"
        check_interval = 200
        cpu_usage = (sequential_avg / check_interval) * 100
        suggestion = f"可以使用较短检测间隔"
    elif sequential_avg < 250:
        rating = "🟡 良好"
        check_interval = 300
        cpu_usage = (sequential_avg / check_interval) * 100
        suggestion = f"建议适中检测间隔"
    elif sequential_avg < 350:
        rating = "⚠️ 中等"
        check_interval = 500
        cpu_usage = (sequential_avg / check_interval) * 100
        suggestion = f"建议较长检测间隔"
    else:
        rating = "🔴 较慢"
        check_interval = 1000
        cpu_usage = (sequential_avg / check_interval) * 100
        suggestion = f"建议显著降低检测频率"
    
    print(f"\n性能等级: {rating}")
    print(f"建议: {suggestion}")
    print(f"\n推荐配置:")
    print(f"  check_interval: {check_interval}ms")
    print(f"  检测频率: {1000/check_interval:.1f}次/秒")
    print(f"  预估CPU占用: {cpu_usage:.1f}% (单线程)")
    
    # 准确性验证
    print("\n准确性验证:")
    if hp_text_result[0] == "540/540" and mp_text_result[0] == "253/253":
        print("  ✅ 识别结果完全正确")
    else:
        print(f"  ⚠️ 识别结果异常")
        print(f"     期望: HP=540/540, MP=253/253")
        print(f"     实际: HP={hp_text_result[0]}, MP={mp_text_result[0]}")
    
    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)


if __name__ == "__main__":
    benchmark_ocr()
