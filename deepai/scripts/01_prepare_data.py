#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤1: 数据准备 - 从视频提取帧并分割数字"""

import cv2
import numpy as np
import os
import sys
from pathlib import Path
import json

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from deepai.config import *


def extract_frames_from_video(video_path, output_dir, sample_interval=15, max_frames=0):
    """从视频中提取帧"""
    print(f"\n{'='*80}")
    print(f"📹 开始提取视频帧: {video_path}")
    print(f"{'='*80}")
    
    if not os.path.exists(video_path):
        print(f"❌ 错误: 视频文件不存在 - {video_path}")
        print(f"💡 请将游戏录制视频放到: {video_path}")
        return []
    
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"❌ 错误: 无法打开视频文件")
        return []
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    duration = total_frames / fps
    
    print(f"视频信息:")
    print(f"  总帧数: {total_frames}")
    print(f"  帧率: {fps} fps")
    print(f"  时长: {duration:.1f} 秒")
    print(f"  采样间隔: 每 {sample_interval} 帧")
    
    os.makedirs(output_dir, exist_ok=True)
    
    frame_count = 0
    extracted_count = 0
    extracted_paths = []
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        if frame_count % sample_interval == 0:
            frame_path = os.path.join(output_dir, f"frame_{frame_count:06d}.png")
            cv2.imwrite(frame_path, frame)
            extracted_paths.append(frame_path)
            extracted_count += 1
            
            if extracted_count % 10 == 0:
                print(f"  已提取: {extracted_count} 帧 ({frame_count}/{total_frames})")
            
            if max_frames > 0 and extracted_count >= max_frames:
                print(f"  达到最大帧数限制: {max_frames}")
                break
        
        frame_count += 1
    
    cap.release()
    
    print(f"✅ 完成! 共提取 {extracted_count} 帧")
    return extracted_paths


def crop_regions(frame_paths, hp_region, mp_region, output_dir):
    """裁剪HP/MP区域"""
    print(f"\n{'='*80}")
    print(f"✂️ 开始裁剪HP/MP区域")
    print(f"{'='*80}")
    
    hp_dir = os.path.join(output_dir, "hp")
    mp_dir = os.path.join(output_dir, "mp")
    os.makedirs(hp_dir, exist_ok=True)
    os.makedirs(mp_dir, exist_ok=True)
    
    hp_paths = []
    mp_paths = []
    
    for i, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        if frame is None:
            continue
        
        # 裁剪HP区域
        x1, y1, x2, y2 = hp_region
        hp_roi = frame[y1:y2, x1:x2]
        hp_path = os.path.join(hp_dir, f"hp_{i:06d}.png")
        cv2.imwrite(hp_path, hp_roi)
        hp_paths.append(hp_path)
        
        # 裁剪MP区域
        x1, y1, x2, y2 = mp_region
        mp_roi = frame[y1:y2, x1:x2]
        mp_path = os.path.join(mp_dir, f"mp_{i:06d}.png")
        cv2.imwrite(mp_path, mp_roi)
        mp_paths.append(mp_path)
        
        if (i + 1) % 50 == 0:
            print(f"  已处理: {i+1}/{len(frame_paths)} 帧")
    
    print(f"✅ 完成! HP: {len(hp_paths)}, MP: {len(mp_paths)}")
    return hp_paths, mp_paths


def segment_digits(roi_paths, output_dir, min_width=6, min_height=10):
    """分割单个数字"""
    print(f"\n{'='*80}")
    print(f"🔢 开始分割单个数字")
    print(f"{'='*80}")
    
    os.makedirs(output_dir, exist_ok=True)
    
    digit_data = []
    total_digits = 0
    
    for roi_path in roi_paths:
        img = cv2.imread(roi_path)
        if img is None:
            continue
        
        # 预处理（与Tesseract一致）
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
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
        for idx, (x, y, w, h) in enumerate(bounding_boxes):
            digit_img = binary[y:y+h, x:x+w]
            
            # 保存数字图像
            basename = os.path.basename(roi_path).replace('.png', '')
            digit_path = os.path.join(output_dir, f"{basename}_digit_{idx}.png")
            cv2.imwrite(digit_path, digit_img)
            
            digit_data.append({
                'image_path': digit_path,
                'source': roi_path,
                'position': idx,
                'bbox': [x, y, w, h]
            })
            
            total_digits += 1
        
        if len(roi_paths) > 0 and (roi_paths.index(roi_path) + 1) % 50 == 0:
            print(f"  已处理: {roi_paths.index(roi_path)+1}/{len(roi_paths)}, 累计数字: {total_digits}")
    
    print(f"✅ 完成! 共分割 {total_digits} 个数字")
    return digit_data


def auto_label_with_tesseract(digit_data):
    """使用Tesseract自动标注"""
    print(f"\n{'='*80}")
    print(f"🏷️ 开始自动标注（使用Tesseract）")
    print(f"{'='*80}")
    
    try:
        import pytesseract
        if TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    except ImportError:
        print("❌ 错误: 未安装pytesseract")
        print("请运行: pip install pytesseract")
        return digit_data
    
    labeled_count = 0
    low_confidence_count = 0
    
    for i, data in enumerate(digit_data):
        img_path = data['image_path']
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            continue
        
        # 使用Tesseract识别
        try:
            # 使用相同的配置
            result = pytesseract.image_to_data(
                img, 
                config=TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT
            )
            
            # 获取最高置信度的识别结果
            confidences = [float(c) for c in result['conf'] if c != '-1']
            texts = [t for t, c in zip(result['text'], result['conf']) if c != '-1' and t.strip()]
            
            if texts and confidences:
                best_idx = confidences.index(max(confidences))
                label = texts[best_idx].strip()
                confidence = confidences[best_idx] / 100.0
                
                data['label'] = label
                data['confidence'] = confidence
                data['needs_verification'] = confidence < AUTO_LABEL_CONFIDENCE
                
                labeled_count += 1
                
                if data['needs_verification']:
                    low_confidence_count += 1
            else:
                data['label'] = ''
                data['confidence'] = 0.0
                data['needs_verification'] = True
                low_confidence_count += 1
                
        except Exception as e:
            print(f"  警告: 识别失败 - {img_path}: {e}")
            data['label'] = ''
            data['confidence'] = 0.0
            data['needs_verification'] = True
            low_confidence_count += 1
        
        if (i + 1) % 100 == 0:
            print(f"  已标注: {i+1}/{len(digit_data)}")
    
    print(f"✅ 完成! 已标注: {labeled_count}, 需要验证: {low_confidence_count}")
    return digit_data


def save_labels(digit_data, output_path):
    """保存标注结果"""
    print(f"\n{'='*80}")
    print(f"💾 保存标注数据")
    print(f"{'='*80}")
    
    # 保存为JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(digit_data, f, indent=2, ensure_ascii=False)
    
    # 统计信息
    total = len(digit_data)
    labeled = sum(1 for d in digit_data if d.get('label'))
    needs_verify = sum(1 for d in digit_data if d.get('needs_verification', False))
    
    print(f"总数字数: {total}")
    print(f"已标注: {labeled}")
    print(f"需要验证: {needs_verify}")
    print(f"保存路径: {output_path}")
    print(f"✅ 完成!")


def main():
    """主函数"""
    print(f"\n{'='*80}")
    print(f"🚀 DeepAI 数据准备流程")
    print(f"{'='*80}\n")
    
    # 步骤1: 提取视频帧
    frame_paths = extract_frames_from_video(
        VIDEO_PATH,
        PROCESSED_DATA_DIR,
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
        PROCESSED_DATA_DIR
    )
    
    # 步骤3: 分割数字
    all_roi_paths = hp_paths + mp_paths
    digit_data = segment_digits(
        all_roi_paths,
        DIGITS_DIR,
        min_width=MIN_DIGIT_WIDTH,
        min_height=MIN_DIGIT_HEIGHT
    )
    
    # 步骤4: 自动标注
    digit_data = auto_label_with_tesseract(digit_data)
    
    # 步骤5: 保存标注
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    save_labels(digit_data, labels_path)
    
    print(f"\n{'='*80}")
    print(f"✅ 数据准备完成!")
    print(f"{'='*80}")
    print(f"\n下一步: 运行验证脚本检查标注")
    print(f"  python deepai/scripts/02_verify_labels.py")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
