#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤1: 数据准备 - 从视频提取帧并分割数字"""

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


def clean_old_data():
    """清理旧的处理数据"""
    print(f"\n{'='*80}")
    print(f"🧹 清理旧数据")
    print(f"{'='*80}")
    
    dirs_to_clean = [
        PROCESSED_DATA_DIR / "frames",
        PROCESSED_DATA_DIR / "hp",
        PROCESSED_DATA_DIR / "mp",
        DIGITS_DIR,
    ]
    
    cleaned_count = 0
    for dir_path in dirs_to_clean:
        if dir_path.exists():
            file_count = len(list(dir_path.glob("*")))
            if file_count > 0:
                print(f"  清理: {dir_path} ({file_count} 个文件)")
                shutil.rmtree(dir_path)
                dir_path.mkdir(parents=True, exist_ok=True)
                cleaned_count += file_count
    
    # 清理labels.json
    labels_path = DATA_DIR / "labels.json"
    if labels_path.exists():
        print(f"  清理: {labels_path}")
        labels_path.unlink()
        cleaned_count += 1
    
    if cleaned_count > 0:
        print(f"✅ 已清理 {cleaned_count} 个旧文件/目录")
    else:
        print(f"✅ 没有旧数据需要清理")


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


def crop_regions(frame_paths, hp_region, mp_region, output_dir, hp_sample_ratio=0.1):
    """裁剪HP/MP区域，使用不同的采样率
    
    Args:
        frame_paths: 视频帧路径列表
        hp_region: HP区域坐标
        mp_region: MP区域坐标
        output_dir: 输出目录
        hp_sample_ratio: HP区域采样率（默认0.1，即10%）
    """
    print(f"\n{'='*80}")
    print(f"✂️ 开始裁剪HP/MP区域")
    print(f"{'='*80}")
    print(f"📊 采样策略: HP={hp_sample_ratio*100:.0f}%, MP={100-hp_sample_ratio*100:.0f}%")
    print(f"   原因: HP变化慢，MP变化快，需要更多MP样本")
    
    hp_dir = os.path.join(output_dir, "hp")
    mp_dir = os.path.join(output_dir, "mp")
    os.makedirs(hp_dir, exist_ok=True)
    os.makedirs(mp_dir, exist_ok=True)
    
    hp_paths = []
    mp_paths = []
    
    # 计算HP采样间隔（例如：0.1采样率 = 每10帧采样1次）
    hp_sample_interval = max(1, int(1.0 / hp_sample_ratio))
    
    for i, frame_path in enumerate(frame_paths):
        frame = cv2.imread(frame_path)
        if frame is None:
            continue
        
        # HP区域：按采样率采样（例如每10帧采样1次）
        if i % hp_sample_interval == 0:
            x1, y1, x2, y2 = hp_region
            hp_roi = frame[y1:y2, x1:x2]
            hp_path = os.path.join(hp_dir, f"hp_{i:06d}.png")
            cv2.imwrite(hp_path, hp_roi)
            hp_paths.append(hp_path)
        
        # MP区域：每帧都采样
        x1, y1, x2, y2 = mp_region
        mp_roi = frame[y1:y2, x1:x2]
        mp_path = os.path.join(mp_dir, f"mp_{i:06d}.png")
        cv2.imwrite(mp_path, mp_roi)
        mp_paths.append(mp_path)
        
        if (i + 1) % 50 == 0:
            print(f"  已处理: {i+1}/{len(frame_paths)} 帧 (HP: {len(hp_paths)}, MP: {len(mp_paths)})")
    
    hp_ratio_actual = len(hp_paths) / (len(hp_paths) + len(mp_paths)) * 100
    mp_ratio_actual = len(mp_paths) / (len(hp_paths) + len(mp_paths)) * 100
    
    print(f"✅ 完成! HP: {len(hp_paths)} ({hp_ratio_actual:.1f}%), MP: {len(mp_paths)} ({mp_ratio_actual:.1f}%)")
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
        
        # 预处理（与Keras识别器一致）
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # 使用开运算去除逗号等小噪点（增强版）
        # 使用3x3的核，可以更有效地去除逗号
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        
        # 可选：再次使用2x2的核进行闭运算，填补数字内部的小孔
        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel_close, iterations=1)
        
        # 查找轮廓
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # 按x坐标排序（从左到右）
        bounding_boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            
            # 基本尺寸过滤
            if w < min_width or h < min_height:
                continue
            
            # 宽高比过滤：排除太窄或太扁的形状（如逗号）
            aspect_ratio = w / h if h > 0 else 0
            
            # 数字和斜杠的宽高比通常在 0.2 到 2.0 之间
            # 逗号通常很窄很小，宽高比 < 0.2
            if aspect_ratio < 0.15 or aspect_ratio > 2.5:
                continue
            
            # 面积过滤：排除太小的轮廓（逗号面积很小）
            area = w * h
            if area < min_width * min_height * 0.8:  # 至少要有最小尺寸的80%面积
                continue
            
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
    """使用Tesseract自动标注
    
    注意：Tesseract识别单个字符的准确率远低于识别完整文本
    - 主程序识别 "1234/5678" → 准确率高（有上下文）
    - 这里识别单个 "1" → 准确率低（缺少上下文）
    
    因此需要人工验证来提高标注质量
    """
    print(f"\n{'='*80}")
    print(f"🏷️ 开始自动标注（使用Tesseract）")
    print(f"{'='*80}")
    print(f"⚠️  注意: Tesseract识别单个字符准确率较低，需要人工验证")
    print(f"   主程序识别完整文本 '1234/5678' 准确率高")
    print(f"   这里识别单个字符 '1' 准确率低（缺少上下文）")
    
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
        
        try:
            # 使用与主程序相同的预处理（已经在segment_digits中完成了放大和二值化）
            # 但这里的图像是单个数字，需要额外放大以提高识别率
            h, w = img.shape
            
            # 如果图像太小，再次放大
            if h < 30 or w < 20:
                scale_factor = max(30 / h, 20 / w, 2.0)
                img = cv2.resize(img, None, fx=scale_factor, fy=scale_factor, 
                               interpolation=cv2.INTER_CUBIC)
            
            # 添加边距，提高识别率（Tesseract在有边距时效果更好）
            border_size = 10
            img_with_border = cv2.copyMakeBorder(
                img, border_size, border_size, border_size, border_size,
                cv2.BORDER_CONSTANT, value=0  # 黑色边框
            )
            
            result = pytesseract.image_to_data(
                img_with_border, 
                config=TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT
            )
            
            valid_results = []
            for text, conf in zip(result['text'], result['conf']):
                if conf != '-1' and text.strip():
                    valid_results.append((text.strip(), float(conf)))
            
            if valid_results:
                label, confidence = max(valid_results, key=lambda x: x[1])
                confidence /= 100.0
                
                data['label'] = label
                data['confidence'] = confidence
                data['needs_verification'] = confidence < AUTO_LABEL_CONFIDENCE
                
                # 输出识别结果
                img_name = os.path.basename(img_path)
                status = "✓" if confidence >= AUTO_LABEL_CONFIDENCE else "?"
                print(f"  [{i+1:4d}/{len(digit_data)}] {status} {img_name:30s} → '{label}' ({confidence:.1%})")
                
                labeled_count += 1
                if data['needs_verification']:
                    low_confidence_count += 1
            else:
                data['label'] = ''
                data['confidence'] = 0.0
                data['needs_verification'] = True
                low_confidence_count += 1
                
                # 输出识别失败
                img_name = os.path.basename(img_path)
                print(f"  [{i+1:4d}/{len(digit_data)}] ✗ {img_name:30s} → (识别失败)")
                
        except Exception as e:
            data['label'] = ''
            data['confidence'] = 0.0
            data['needs_verification'] = True
            low_confidence_count += 1
            
            # 输出错误
            img_name = os.path.basename(img_path)
            print(f"  [{i+1:4d}/{len(digit_data)}] ✗ {img_name:30s} → (错误: {e})")
    
    print(f"✅ 完成! 已标注: {labeled_count}, 需要验证: {low_confidence_count}")
    return digit_data


def save_labels(digit_data, output_path):
    """保存标注结果"""
    print(f"\n{'='*80}")
    print(f"💾 保存标注数据")
    print(f"{'='*80}")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(digit_data, f, indent=2, ensure_ascii=False)
    
    total = len(digit_data)
    labeled = sum(1 for d in digit_data if d.get('label'))
    needs_verify = sum(1 for d in digit_data if d.get('needs_verification', False))
    
    print(f"总数字数: {total}")
    print(f"已标注: {labeled}")
    print(f"需要验证: {needs_verify}")
    print(f"保存路径: {output_path}")
    print(f"✅ 完成!")


def save_digits_as_templates(digit_data):
    """将高置信度的数字图片保存为模板"""
    print(f"\n{'='*80}")
    print(f"🖼️ 开始保存高质量数字作为模板")
    print(f"{'='*80}")

    # 定位到项目根目录下的 'game_char_templates/hp_mp_digits'
    template_base_dir = Path(__file__).resolve().parent.parent.parent / "game_char_templates" / "hp_mp_digits"
    template_base_dir.mkdir(parents=True, exist_ok=True)
    
    saved_count = 0
    
    for data in digit_data:
        label = data.get('label')
        confidence = data.get('confidence', 0)
        
        # 只保存置信度高于阈值的、单个有效字符
        if label and len(label) == 1 and confidence >= AUTO_LABEL_CONFIDENCE:
            # 将标签'/'转换为'slash'以用作目录名
            dir_name = 'slash' if label == '/' else label
            target_dir = template_base_dir / dir_name
            target_dir.mkdir(exist_ok=True)
            
            source_path = Path(data['image_path'])
            if source_path.exists():
                # 使用原始ROI文件名和数字索引，确保唯一性
                target_path = target_dir / source_path.name
                shutil.copy(str(source_path), str(target_path))
                saved_count += 1

    print(f"✅ 完成! 共保存 {saved_count} 个高质量模板")


def main():
    """主函数"""
    print(f"\n{'='*80}")
    print(f"🚀 DeepAI 数据准备流程")
    print(f"{'='*80}\n")
    
    # 清理旧数据
    clean_old_data()
    
    frame_paths = extract_frames_from_video(
        VIDEO_PATH,
        PROCESSED_DATA_DIR / "frames",  # 保存到frames子目录
        sample_interval=FRAME_SAMPLE_INTERVAL,
        max_frames=MAX_FRAMES
    )
    
    if not frame_paths:
        return
    
    hp_paths, mp_paths = crop_regions(
        frame_paths,
        HP_REGION,
        MP_REGION,
        PROCESSED_DATA_DIR,
        hp_sample_ratio=HP_SAMPLE_RATIO
    )
    
    digit_data = segment_digits(
        hp_paths + mp_paths,
        DIGITS_DIR,
        min_width=MIN_DIGIT_WIDTH,
        min_height=MIN_DIGIT_HEIGHT
    )
    
    digit_data = auto_label_with_tesseract(digit_data)
    
    # 新增：保存高质量数字作为模板
    save_digits_as_templates(digit_data)
    
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
