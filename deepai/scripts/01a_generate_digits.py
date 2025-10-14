#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=invalid-name
"""步骤1a: 从视频生成数字图像 - 提取帧、裁剪、分割"""

import cv2
import numpy as np
import os
import sys
from pathlib import Path
import json
import shutil

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

clean_old_data = prepare_data_module.clean_old_data
extract_frames_from_video = prepare_data_module.extract_frames_from_video
crop_regions = prepare_data_module.crop_regions
segment_digits = prepare_data_module.segment_digits
save_labels = prepare_data_module.save_labels


def main():
    """主函数 - 只生成数字图像，不进行标注"""
    print(f"\n{'='*80}")
    print(f"🚀 DeepAI 数据生成流程 (步骤1a)")
    print(f"{'='*80}")
    print(f"📝 此步骤只生成数字图像，不进行自动标注")
    print(f"   完成后请运行 '01b_auto_label.py' 进行标注")
    print(f"{'='*80}\n")
    
    # 清理旧数据
    clean_old_data()
    
    # 步骤1: 提取视频帧
    frame_paths = extract_frames_from_video(
        VIDEO_PATH,
        PROCESSED_DATA_DIR / "frames",  # 保存到frames子目录
        sample_interval=FRAME_SAMPLE_INTERVAL,
        max_frames=MAX_FRAMES
    )
    
    if not frame_paths:
        print("\n❌ 视频帧提取失败，程序终止")
        return
    
    # 步骤2: 裁剪HP/MP区域
    hp_paths, mp_paths = crop_regions(
        frame_paths,
        HP_REGION,
        MP_REGION,
        PROCESSED_DATA_DIR,
        hp_sample_ratio=HP_SAMPLE_RATIO
    )
    
    # 步骤3: 分割数字
    digit_data = segment_digits(
        hp_paths + mp_paths,
        DIGITS_DIR,
        min_width=MIN_DIGIT_WIDTH,
        min_height=MIN_DIGIT_HEIGHT
    )
    
    # 步骤4: 保存数据（不包含标注）
    print(f"\n{'='*80}")
    print(f"💾 保存数字图像数据")
    print(f"{'='*80}")
    
    # 初始化标注字段为空
    for data in digit_data:
        data['label'] = ''
        data['confidence'] = 0.0
        data['needs_verification'] = True
        data['verified'] = False
    
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    save_labels(digit_data, labels_path)
    
    print(f"\n{'='*80}")
    print(f"✅ 数字图像生成完成!")
    print(f"{'='*80}")
    print(f"\n📊 统计信息:")
    print(f"   总数字数: {len(digit_data)}")
    print(f"   数据路径: {DIGITS_DIR}")
    print(f"   标注文件: {labels_path}")
    print(f"\n下一步: 运行自动标注脚本")
    print(f"  python deepai/scripts/01b_auto_label.py")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
