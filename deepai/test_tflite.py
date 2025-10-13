#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快速测试TFLite识别器"""

import sys
import cv2

from deepai import get_recognizer


def main():
    print(f"\n{'='*80}")
    print(f"🧪 TFLite识别器测试")
    print(f"{'='*80}\n")
    
    # 获取识别器
    recognizer = get_recognizer('tflite')
    
    if recognizer is None:
        print("\n❌ 初始化失败")
        print("\n可能的原因:")
        print("1. 未安装 tflite-runtime:")
        print("   pip install tflite-runtime")
        print("2. 未找到模型文件:")
        print("   请运行训练流程或下载预训练模型")
        return
    
    print(f"\n✅ 初始化成功!\n")
    
    # 如果提供了图像路径，测试识别
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
        print(f"测试图像: {img_path}")
        
        img = cv2.imread(img_path)
        if img is None:
            print(f"❌ 无法加载图像")
            return
        
        current, maximum = recognizer.recognize_and_parse(img)
        
        if current is not None:
            print(f"✅ 识别结果: {current}/{maximum}")
        else:
            print(f"❌ 识别失败")
    else:
        print("💡 提示: 可以提供图像路径测试识别")
        print("   python deepai/test_tflite.py <image_path>")
    
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()
