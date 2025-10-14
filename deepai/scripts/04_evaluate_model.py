#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤4: 模型评估 - 详细评估模型性能"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

import sys
from pathlib import Path
import json
import numpy as np
import cv2
import tensorflow as tf
from tensorflow import keras
from sklearn.metrics import confusion_matrix, classification_report
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei']  # 设置中文字体为微软雅黑
matplotlib.rcParams['axes.unicode_minus'] = False  # 正常显示负号
import time

# Seaborn是可选的，用于更美观的混淆矩阵
try:
    import seaborn as sns
    SEABORN_AVAILABLE = True
except ImportError:
    SEABORN_AVAILABLE = False
    print("⚠️  警告: seaborn未安装，将使用matplotlib绘制混淆矩阵")

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deepai.config import *


def load_model_and_labels():
    """加载模型和标签映射"""
    print(f"\n{'='*80}")
    print(f"📦 加载模型")
    print(f"{'='*80}")
    
    if not os.path.exists(MODEL_SAVE_PATH):
        print(f"❌ 错误: 模型文件不存在 - {MODEL_SAVE_PATH}")
        return None, None
    
    model = keras.models.load_model(MODEL_SAVE_PATH)
    print(f"✅ 模型加载成功: {MODEL_SAVE_PATH}")
    
    label_map_path = os.path.join(MODELS_DIR, 'label_map.json')
    with open(label_map_path, 'r', encoding='utf-8') as f:
        idx_to_label = json.load(f)
    
    # 转换键为整数
    idx_to_label = {int(k): v for k, v in idx_to_label.items()}
    
    print(f"✅ 标签映射加载成功")
    print(f"类别数: {len(idx_to_label)}")
    print(f"标签: {sorted(idx_to_label.values())}\n")
    
    return model, idx_to_label


def load_test_data(labels_path, idx_to_label):
    """加载测试数据"""
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
    
    for d in labeled_data:
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


def evaluate_performance(model, X_test, y_test, idx_to_label):
    """评估性能"""
    print(f"\n{'='*80}")
    print(f"⚡ 性能测试")
    print(f"{'='*80}")
    
    # 预测（测量时间）
    start_time = time.time()
    y_pred = model.predict(X_test, verbose=0)
    end_time = time.time()
    
    total_time = (end_time - start_time) * 1000  # 转换为毫秒
    avg_time = total_time / len(X_test)
    
    print(f"总预测时间: {total_time:.2f} ms")
    print(f"单个预测: {avg_time:.2f} ms")
    print(f"预测速度: {1000/avg_time:.1f} 次/秒")
    
    # HP/MP一起识别的时间（假设5个字符）
    hp_mp_time = avg_time * 5
    print(f"\nHP/MP识别时间(5个字符): {hp_mp_time:.2f} ms")
    
    y_pred_classes = np.argmax(y_pred, axis=1)
    
    # 准确率
    accuracy = np.mean(y_pred_classes == y_test)
    print(f"\n总体准确率: {accuracy:.2%}")
    
    print(f"✅ 性能测试完成!\n")
    
    return y_pred_classes, y_pred


def plot_confusion_matrix(y_test, y_pred_classes, idx_to_label, save_path):
    """绘制混淆矩阵"""
    print(f"📊 生成混淆矩阵...")
    
    cm = confusion_matrix(y_test, y_pred_classes)
    
    labels = [idx_to_label[i] for i in sorted(idx_to_label.keys())]
    
    plt.figure(figsize=(10, 8))
    
    if SEABORN_AVAILABLE:
        # 使用seaborn绘制更美观的热图
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', 
                    xticklabels=labels, yticklabels=labels)
    else:
        # 使用matplotlib绘制基本热图
        plt.imshow(cm, interpolation='nearest', cmap='Blues')
        plt.colorbar()
        
        # 添加数值标注
        for i in range(cm.shape[0]):
            for j in range(cm.shape[1]):
                plt.text(j, i, str(cm[i, j]),
                        ha="center", va="center",
                        color="white" if cm[i, j] > cm.max() / 2 else "black")
        
        # 设置刻度标签
        tick_marks = np.arange(len(labels))
        plt.xticks(tick_marks, labels)
        plt.yticks(tick_marks, labels)
    
    plt.title('混淆矩阵')
    plt.ylabel('真实标签')
    plt.xlabel('预测标签')
    plt.tight_layout()
    plt.savefig(save_path)
    print(f"✅ 混淆矩阵已保存: {save_path}\n")


def print_classification_report(y_test, y_pred_classes, idx_to_label):
    """打印分类报告"""
    print(f"\n{'='*80}")
    print(f"📋 分类报告")
    print(f"{'='*80}\n")
    
    labels = [idx_to_label[i] for i in sorted(idx_to_label.keys())]
    
    report = classification_report(y_test, y_pred_classes, 
                                   target_names=labels, digits=4)
    print(report)


def analyze_errors(X_test, y_test, y_pred_classes, y_pred, idx_to_label, save_dir):
    """分析错误样本"""
    print(f"\n{'='*80}")
    print(f"🔍 错误分析")
    print(f"{'='*80}")
    
    errors_dir = os.path.join(save_dir, 'errors')
    os.makedirs(errors_dir, exist_ok=True)
    
    # 找出错误样本
    error_indices = np.where(y_pred_classes != y_test)[0]
    
    print(f"错误样本数: {len(error_indices)}/{len(y_test)}")
    
    if len(error_indices) == 0:
        print(f"✅ 没有错误样本!\n")
        return
    
    # 保存前20个错误样本
    num_show = min(20, len(error_indices))
    
    fig, axes = plt.subplots(4, 5, figsize=(15, 12))
    axes = axes.ravel()
    
    for i in range(num_show):
        idx = error_indices[i]
        img = X_test[idx].squeeze()
        true_label = idx_to_label[y_test[idx]]
        pred_label = idx_to_label[y_pred_classes[idx]]
        confidence = y_pred[idx][y_pred_classes[idx]]
        
        axes[i].imshow(img, cmap='gray')
        axes[i].set_title(f"真实: '{true_label}'\n预测: '{pred_label}'\n置信度: {confidence:.2%}", 
                         fontsize=10)
        axes[i].axis('off')
    
    # 隐藏多余的子图
    for i in range(num_show, 20):
        axes[i].axis('off')
    
    plt.tight_layout()
    error_plot_path = os.path.join(errors_dir, 'error_samples.png')
    plt.savefig(error_plot_path)
    print(f"✅ 错误样本已保存: {error_plot_path}\n")


def test_inference_speed(model, idx_to_label):
    """测试推理速度"""
    print(f"\n{'='*80}")
    print(f"🚀 推理速度测试")
    print(f"{'='*80}")
    
    # 创建随机测试数据
    test_sizes = [1, 5, 10, 50, 100]
    
    print(f"测试不同批次大小的推理速度:\n")
    
    batch_5_time = None
    
    for size in test_sizes:
        dummy_input = np.random.rand(size, IMG_HEIGHT, IMG_WIDTH, 1).astype(np.float32)
        
        # 预热
        model.predict(dummy_input, verbose=0)
        
        # 测试
        times = []
        for _ in range(10):
            start = time.time()
            model.predict(dummy_input, verbose=0)
            end = time.time()
            times.append((end - start) * 1000)
        
        avg_time = np.mean(times)
        std_time = np.std(times)
        per_sample = avg_time / size
        
        print(f"  批次大小 {size:3d}: {avg_time:6.2f} ± {std_time:5.2f} ms "
              f"(单个: {per_sample:.2f} ms, {1000/per_sample:.0f} 次/秒)")
        
        # 记录批次大小为5的时间（用于HP/MP识别）
        if size == 5:
            batch_5_time = avg_time
    
    print(f"\n✅ 速度测试完成!\n")
    
    # 返回批次大小为5的推理时间（更接近实际使用场景）
    return batch_5_time if batch_5_time else avg_time


def main():
    """主函数"""
    print(f"\n{'='*80}")
    print(f"🔍 DeepAI 模型评估")
    print(f"{'='*80}\n")
    
    # 加载模型
    model, idx_to_label = load_model_and_labels()
    if model is None:
        return
    
    # 加载测试数据
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    X_test, y_test = load_test_data(labels_path, idx_to_label)
    
    # 评估性能
    y_pred_classes, y_pred = evaluate_performance(model, X_test, y_test, idx_to_label)
    
    # 混淆矩阵
    cm_path = os.path.join(MODELS_DIR, 'confusion_matrix.png')
    plot_confusion_matrix(y_test, y_pred_classes, idx_to_label, cm_path)
    
    # 分类报告
    print_classification_report(y_test, y_pred_classes, idx_to_label)
    
    # 错误分析
    analyze_errors(X_test, y_test, y_pred_classes, y_pred, idx_to_label, MODELS_DIR)
    
    # 推理速度测试
    avg_time = test_inference_speed(model, idx_to_label)
    
    # 计算准确率
    accuracy = np.mean(y_pred_classes == y_test)
    error_count = np.sum(y_pred_classes != y_test)
    
    # 输出评估结论
    print(f"\n{'='*80}")
    print(f"📊 评估结论")
    print(f"{'='*80}\n")
    
    print(f"🎯 模型性能:")
    print(f"   准确率: {accuracy:.2%}")
    print(f"   测试样本数: {len(y_test)}")
    print(f"   正确识别: {len(y_test) - error_count}")
    print(f"   错误识别: {error_count}")
    print()
    
    print(f"⚡ 推理速度:")
    print(f"   批量识别(5个字符): ~{avg_time:.2f} ms")
    print(f"   单个字符平均: ~{avg_time / 5:.2f} ms")
    print(f"   识别速度: ~{1000 / (avg_time / 5):.0f} 次/秒")
    print()
    
    # 评估等级
    if accuracy >= 0.99:
        grade = "🌟 优秀"
        comment = "模型表现出色，可以投入使用！"
    elif accuracy >= 0.95:
        grade = "✅ 良好"
        comment = "模型表现良好，建议继续优化。"
    elif accuracy >= 0.90:
        grade = "⚠️  一般"
        comment = "模型需要更多训练数据或调整参数。"
    else:
        grade = "❌ 较差"
        comment = "模型需要重新训练，检查数据质量。"
    
    print(f"📈 评估等级: {grade}")
    print(f"   {comment}")
    print()
    
    print(f"{'='*80}")
    print(f"✅ 评估完成!")
    print(f"{'='*80}")
    print(f"\n生成的文件:")
    print(f"  混淆矩阵: {cm_path}")
    print(f"  错误样本: {os.path.join(MODELS_DIR, 'errors/error_samples.png')}")
    print(f"\n下一步:")
    if accuracy >= 0.95:
        print(f"  ✅ 模型已达到可用标准，可以集成到应用中")
        print(f"  ✅ 在主程序UI中选择 'Keras' 引擎")
    else:
        print(f"  ⚠️  建议继续迭代训练（运行步骤 4 → 2 → 3）")
        print(f"  ⚠️  或增加更多训练数据（运行步骤 1a → 1b）")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
