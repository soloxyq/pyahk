#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=invalid-name
"""步骤1b: Tesseract自动标注 - 对已生成的数字图像进行初始标注"""

import cv2
import os
import sys
from pathlib import Path
import json

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deepai.config import *

# 动态导入原始脚本中的函数（避免数字开头的模块名问题）
import importlib.util
spec = importlib.util.spec_from_file_location(
    "prepare_data_module",
    Path(__file__).parent / "01_prepare_data.py"
)
prepare_data_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_data_module)

auto_label_with_tesseract = prepare_data_module.auto_label_with_tesseract
save_labels = prepare_data_module.save_labels
save_digits_as_templates = prepare_data_module.save_digits_as_templates


def main():
    """主函数 - 只进行自动标注"""
    print(f"\n{'='*80}")
    print(f"🚀 DeepAI 自动标注流程 (步骤1b)")
    print(f"{'='*80}")
    print(f"📝 使用Tesseract对数字图像进行自动标注")
    print(f"{'='*80}\n")
    
    # 检查labels.json是否存在
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    if not os.path.exists(labels_path):
        print(f"❌ 错误: 未找到数字图像数据 - {labels_path}")
        print(f"   请先运行: python deepai/scripts/01a_generate_digits.py")
        return
    
    # 加载数字数据
    print(f"📂 加载数字图像数据...")
    with open(labels_path, 'r', encoding='utf-8') as f:
        digit_data = json.load(f)
    
    print(f"✅ 加载了 {len(digit_data)} 个数字图像")
    
    # 检查是否已经标注过
    already_labeled = sum(1 for d in digit_data if d.get('label'))
    if already_labeled > 0:
        print(f"\n⚠️  警告: 已有 {already_labeled} 个样本被标注")
        choice = input("是否重新标注所有样本? [y/N]: ").strip().lower()
        if choice != 'y':
            print("❌ 已取消")
            return
        # 清空标注
        for data in digit_data:
            data['label'] = ''
            data['confidence'] = 0.0
            data['needs_verification'] = True
            data['verified'] = False
    
    # 步骤1: 自动标注
    digit_data = auto_label_with_tesseract(digit_data)
    
    # 步骤2: 保存标注结果
    save_labels(digit_data, labels_path)
    
    # 步骤3: 保存高质量数字作为模板
    save_digits_as_templates(digit_data)
    
    print(f"\n{'='*80}")
    print(f"✅ 自动标注完成!")
    print(f"{'='*80}")
    print(f"\n下一步: 运行验证脚本检查标注")
    print(f"  python deepai/scripts/02_verify_labels.py")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
