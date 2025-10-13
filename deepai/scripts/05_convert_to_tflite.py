#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤5: 模型转换 - 将TensorFlow模型转换为TFLite格式"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import sys
from pathlib import Path
import json
import numpy as np
import cv2
import tensorflow as tf
from tensorflow import keras

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from deepai.config import *


def load_test_data(labels_path, idx_to_label):
    """加载测试数据（用于验证转换后的准确率）"""
    print(f"\n{'='*80}")
    print(f"📊 加载测试数据")
    print(f"{'='*80}")
    
    with open(labels_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 过滤已标注的数据
    labeled_data = [d for d in data if d.get('label') and len(d['label']) == 1]
    
    # 创建反向映射
    label_to_idx = {v: k for k, v in idx_to_label.items()}
    
    images = []
    labels = []
    
    for d in labeled_data[:100]:  # 只用100个样本测试
        img_path = d['image_path']
        label = d['label']
        
        if label not in label_to_idx:
            continue
        
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        
        img_resized = cv2.resize(img, (IMG_WIDTH, IMG_HEIGHT), interpolation=cv2.INTER_AREA)
        img_normalized = img_resized.astype(np.float32) / 255.0
        
        images.append(img_normalized)
        labels.append(label_to_idx[label])
    
    images = np.array(images)
    labels = np.array(labels)
    images = np.expand_dims(images, axis=-1)
    
    print(f"测试数据: {images.shape[0]} 样本")
    print(f"✅ 加载完成!\n")
    
    return images, labels


def convert_to_tflite(model_path, output_path, quantize=True):
    """转换模型为TFLite格式"""
    print(f"\n{'='*80}")
    print(f"🔄 转换模型为TFLite")
    print(f"{'='*80}")
    
    # 加载Keras模型
    print(f"加载模型: {model_path}")
    model = keras.models.load_model(model_path)
    
    # 创建转换器
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    
    if quantize:
        print(f"启用量化优化...")
        # 动态范围量化（无需代表性数据集）
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        
        # 可选：使用float16量化（更好的精度/大小平衡）
        # converter.target_spec.supported_types = [tf.float16]
    
    # 转换
    print(f"开始转换...")
    tflite_model = converter.convert()
    
    # 保存
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(tflite_model)
    
    # 显示大小信息
    original_size = os.path.getsize(model_path) / 1024
    tflite_size = os.path.getsize(output_path) / 1024
    compression_ratio = (1 - tflite_size / original_size) * 100
    
    print(f"\n✅ 转换完成!")
    print(f"原始模型大小: {original_size:.2f} KB")
    print(f"TFLite模型大小: {tflite_size:.2f} KB")
    print(f"压缩率: {compression_ratio:.1f}%")
    print(f"保存路径: {output_path}\n")
    
    return tflite_model


def test_tflite_model(tflite_path, test_images, test_labels, idx_to_label):
    """测试TFLite模型的准确率"""
    print(f"\n{'='*80}")
    print(f"🧪 测试TFLite模型")
    print(f"{'='*80}")
    
    # 加载TFLite模型
    interpreter = tf.lite.Interpreter(model_path=tflite_path)
    interpreter.allocate_tensors()
    
    # 获取输入输出张量信息
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    
    print(f"输入形状: {input_details[0]['shape']}")
    print(f"输出形状: {output_details[0]['shape']}")
    
    # 测试推理
    correct = 0
    total = len(test_images)
    
    import time
    times = []
    
    for i, (img, true_label) in enumerate(zip(test_images, test_labels)):
        # 准备输入
        input_data = np.expand_dims(img, axis=0).astype(np.float32)
        
        # 推理
        interpreter.set_tensor(input_details[0]['index'], input_data)
        
        start = time.time()
        interpreter.invoke()
        end = time.time()
        times.append((end - start) * 1000)
        
        # 获取输出
        output_data = interpreter.get_tensor(output_details[0]['index'])
        pred_label = np.argmax(output_data[0])
        
        if pred_label == true_label:
            correct += 1
    
    accuracy = correct / total
    avg_time = np.mean(times)
    
    print(f"\n测试结果:")
    print(f"  准确率: {accuracy:.2%} ({correct}/{total})")
    print(f"  平均推理时间: {avg_time:.2f} ms")
    print(f"  HP+MP预估时间: {avg_time * 5:.2f} ms (5个字符)")
    print(f"✅ 测试完成!\n")
    
    return accuracy, avg_time


def create_model_info(model_path, tflite_path, accuracy, inference_time):
    """创建模型信息文件"""
    info = {
        "model_type": "TFLite",
        "original_model": os.path.basename(model_path),
        "tflite_model": os.path.basename(tflite_path),
        "original_size_kb": os.path.getsize(model_path) / 1024,
        "tflite_size_kb": os.path.getsize(tflite_path) / 1024,
        "test_accuracy": f"{accuracy:.2%}",
        "avg_inference_time_ms": f"{inference_time:.2f}",
        "hp_mp_time_ms": f"{inference_time * 5:.2f}",
        "image_size": [IMG_WIDTH, IMG_HEIGHT],
        "num_classes": 11,
        "classes": "0-9 and /",
        "quantized": True,
        "optimization": "Dynamic range quantization",
        "framework": "TensorFlow Lite",
        "runtime": "tflite-runtime",
        "created_date": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    info_path = os.path.join(MODELS_DIR, 'model_info.json')
    with open(info_path, 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
    
    print(f"模型信息已保存: {info_path}")


def main():
    """主函数"""
    print(f"\n{'='*80}")
    print(f"🚀 TFLite 模型转换流程")
    print(f"{'='*80}\n")
    
    # 检查模型文件
    if not os.path.exists(MODEL_SAVE_PATH):
        print(f"❌ 错误: 模型文件不存在 - {MODEL_SAVE_PATH}")
        print(f"请先运行训练脚本: python deepai/scripts/03_train_model.py")
        return
    
    # 加载标签映射
    label_map_path = os.path.join(MODELS_DIR, 'label_map.json')
    with open(label_map_path, 'r', encoding='utf-8') as f:
        idx_to_label = json.load(f)
    idx_to_label = {int(k): v for k, v in idx_to_label.items()}
    
    # 定义输出路径
    tflite_path = os.path.join(MODELS_DIR, 'digit_cnn.tflite')
    
    # 转换模型
    tflite_model = convert_to_tflite(
        MODEL_SAVE_PATH, 
        tflite_path, 
        quantize=True
    )
    
    # 加载测试数据
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    test_images, test_labels = load_test_data(labels_path, idx_to_label)
    
    # 测试TFLite模型
    accuracy, inference_time = test_tflite_model(
        tflite_path, 
        test_images, 
        test_labels, 
        idx_to_label
    )
    
    # 创建模型信息文件
    create_model_info(MODEL_SAVE_PATH, tflite_path, accuracy, inference_time)
    
    print(f"\n{'='*80}")
    print(f"✅ 转换流程完成!")
    print(f"{'='*80}")
    print(f"\n生成的文件:")
    print(f"  TFLite模型: {tflite_path}")
    print(f"  模型信息: {os.path.join(MODELS_DIR, 'model_info.json')}")
    print(f"\n下一步: 在主程序中使用TFLite引擎")
    print(f"  1. 安装依赖: pip install tflite-runtime")
    print(f"  2. 在UI中选择 'TFLite' 引擎")
    print(f"  3. 享受{inference_time * 5:.1f}ms的识别速度!")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
