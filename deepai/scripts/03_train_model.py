#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤3: 模型训练 - 使用TensorFlow CPU训练轻量级CNN"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'  # 减少TensorFlow日志输出

import sys
from pathlib import Path
import json
import numpy as np
import cv2
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers, models
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
from sklearn.model_selection import train_test_split
from collections import Counter
import matplotlib.pyplot as plt

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from deepai.config import *


def configure_device():
    """配置计算设备"""
    print(f"\n{'='*80}")
    print(f"⚙️ 配置计算设备")
    print(f"{'='*80}")
    
    if USE_GPU:
        gpus = tf.config.list_physical_devices('GPU')
        if gpus:
            try:
                # 设置显存限制
                if GPU_MEMORY_LIMIT:
                    tf.config.set_logical_device_configuration(
                        gpus[0],
                        [tf.config.LogicalDeviceConfiguration(memory_limit=GPU_MEMORY_LIMIT)]
                    )
                print(f"✅ 使用GPU训练: {gpus[0].name}")
                if GPU_MEMORY_LIMIT:
                    print(f"   显存限制: {GPU_MEMORY_LIMIT} MB")
            except RuntimeError as e:
                print(f"⚠️ GPU配置失败: {e}")
                print(f"   将使用CPU训练")
        else:
            print(f"⚠️ 未检测到GPU设备")
            print(f"   将使用CPU训练")
    else:
        # 禁用GPU
        tf.config.set_visible_devices([], 'GPU')
        print(f"✅ 使用CPU训练")
        print(f"   提示: 对于11个字符的轻量级模型,CPU训练完全足够")
        print(f"   预计训练时间: 1-5分钟(取决于数据量)")
    
    print(f"TensorFlow版本: {tf.__version__}")
    print(f"Keras版本: {keras.__version__}")
    print(f"✅ 配置完成!\n")


def load_dataset(labels_path):
    """加载数据集"""
    print(f"\n{'='*80}")
    print(f"📊 加载数据集")
    print(f"{'='*80}")
    
    with open(labels_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 过滤已标注的数据
    labeled_data = [d for d in data if d.get('label') and len(d['label']) == 1]
    
    print(f"总数据: {len(data)}")
    print(f"已标注: {len(labeled_data)}")
    
    if len(labeled_data) < 50:
        print(f"❌ 错误: 标注数据不足(至少需要50个)")
        return None, None, None
    
    # 统计标签分布
    label_counts = Counter([d['label'] for d in labeled_data])
    print(f"\n标签分布:")
    for label, count in sorted(label_counts.items()):
        print(f"  '{label}': {count} 个")
    
    # 创建标签映射
    unique_labels = sorted(label_counts.keys())
    label_to_idx = {label: idx for idx, label in enumerate(unique_labels)}
    idx_to_label = {idx: label for label, idx in label_to_idx.items()}
    
    print(f"\n类别数: {len(unique_labels)}")
    print(f"标签映射: {label_to_idx}")
    
    # 加载图像和标签
    images = []
    labels = []
    
    for d in labeled_data:
        img_path = d['image_path']
        label = d['label']
        
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        
        # 调整到固定大小
        img_resized = cv2.resize(img, (IMG_WIDTH, IMG_HEIGHT), interpolation=cv2.INTER_AREA)
        
        # 归一化
        img_normalized = img_resized.astype(np.float32) / 255.0
        
        images.append(img_normalized)
        labels.append(label_to_idx[label])
    
    images = np.array(images)
    labels = np.array(labels)
    
    # 添加通道维度
    images = np.expand_dims(images, axis=-1)
    
    print(f"\n数据形状:")
    print(f"  图像: {images.shape}")
    print(f"  标签: {labels.shape}")
    print(f"✅ 加载完成!\n")
    
    return images, labels, idx_to_label


def create_model(num_classes):
    """创建轻量级CNN模型"""
    print(f"\n{'='*80}")
    print(f"🧠 创建模型")
    print(f"{'='*80}")
    
    # 数据增强层（成为模型的一部分，GPU加速，确保训练推理一致性）
    data_augmentation = keras.Sequential([
        layers.RandomRotation(factor=AUGMENTATION['rotation_range'] / 360),
        layers.RandomZoom(height_factor=AUGMENTATION['zoom_range'], width_factor=AUGMENTATION['zoom_range']),
        layers.RandomTranslation(
            height_factor=AUGMENTATION['height_shift_range'], 
            width_factor=AUGMENTATION['width_shift_range']
        ),
    ], name='data_augmentation')
    
    model = models.Sequential([
        # 数据增强层（仅在训练时激活）
        data_augmentation,
        
        # 第一层卷积 - 特征提取
        layers.Conv2D(32, (3, 3), activation='relu', input_shape=(IMG_HEIGHT, IMG_WIDTH, 1)),
        layers.MaxPooling2D((2, 2)),
        layers.Dropout(0.25),
        
        # 第二层卷积 - 更复杂的特征
        layers.Conv2D(64, (3, 3), activation='relu'),
        layers.MaxPooling2D((2, 2)),
        layers.Dropout(0.25),
        
        # 全连接层
        layers.Flatten(),
        layers.Dense(128, activation='relu'),
        layers.Dropout(0.5),
        layers.Dense(num_classes, activation='softmax')
    ])
    
    # 编译模型
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
        loss='sparse_categorical_crossentropy',
        metrics=['accuracy']
    )
    
    model.summary()
    
    # 计算参数量
    total_params = model.count_params()
    print(f"\n模型参数量: {total_params:,}")
    print(f"模型大小(估算): {total_params * 4 / 1024 / 1024:.2f} MB")
    print(f"✅ 模型创建完成!\n")
    
    return model


def train_model(model, X_train, y_train, X_val, y_val):
    """训练模型"""
    print(f"\n{'='*80}")
    print(f"🚀 开始训练")
    print(f"{'='*80}")
    
    # 数据增强已通过Keras预处理层集成到模型中，无需ImageDataGenerator
    print(f"✅ 使用Keras预处理层进行数据增强（GPU加速）")
    
    # 回调函数
    callbacks = [
        # 早停
        EarlyStopping(
            monitor='val_loss',
            patience=EARLY_STOPPING_PATIENCE,
            restore_best_weights=True,
            verbose=1
        ),
        # 保存最佳模型
        ModelCheckpoint(
            MODEL_SAVE_PATH,
            monitor='val_accuracy',
            save_best_only=True,
            verbose=1
        ),
        # 学习率衰减
        ReduceLROnPlateau(
            monitor='val_loss',
            factor=0.5,
            patience=3,
            min_lr=1e-6,
            verbose=1
        )
    ]
    
    # 训练（数据增强层已在模型中，直接使用原始数据）
    history = model.fit(
        X_train, y_train,
        batch_size=BATCH_SIZE,
        epochs=EPOCHS,
        validation_data=(X_val, y_val),
        callbacks=callbacks,
        verbose=1
    )
    
    print(f"\n{'='*80}")
    print(f"✅ 训练完成!")
    print(f"{'='*80}\n")
    
    return history


def plot_training_history(history, save_path):
    """绘制训练曲线"""
    print(f"📈 绘制训练曲线...")
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    
    # 准确率曲线
    ax1.plot(history.history['accuracy'], label='训练集')
    ax1.plot(history.history['val_accuracy'], label='验证集')
    ax1.set_title('模型准确率')
    ax1.set_xlabel('Epoch')
    ax1.set_ylabel('准确率')
    ax1.legend()
    ax1.grid(True)
    
    # 损失曲线
    ax2.plot(history.history['loss'], label='训练集')
    ax2.plot(history.history['val_loss'], label='验证集')
    ax2.set_title('模型损失')
    ax2.set_xlabel('Epoch')
    ax2.set_ylabel('损失')
    ax2.legend()
    ax2.grid(True)
    
    plt.tight_layout()
    plt.savefig(save_path)
    print(f"✅ 训练曲线已保存: {save_path}\n")


def evaluate_model(model, X_test, y_test, idx_to_label):
    """评估模型"""
    print(f"\n{'='*80}")
    print(f"📊 模型评估")
    print(f"{'='*80}")
    
    # 预测
    y_pred = model.predict(X_test, verbose=0)
    y_pred_classes = np.argmax(y_pred, axis=1)
    
    # 计算准确率
    accuracy = np.mean(y_pred_classes == y_test)
    print(f"测试集准确率: {accuracy:.2%}")
    
    # 计算每个类别的准确率
    print(f"\n各类别准确率:")
    for idx, label in sorted(idx_to_label.items()):
        mask = y_test == idx
        if mask.sum() > 0:
            class_acc = np.mean(y_pred_classes[mask] == y_test[mask])
            print(f"  '{label}': {class_acc:.2%} ({mask.sum()} 样本)")
    
    # 计算置信度统计
    max_probs = np.max(y_pred, axis=1)
    print(f"\n置信度统计:")
    print(f"  平均: {max_probs.mean():.2%}")
    print(f"  中位数: {np.median(max_probs):.2%}")
    print(f"  最小: {max_probs.min():.2%}")
    
    print(f"✅ 评估完成!\n")
    
    return accuracy


def main():
    """主函数"""
    print(f"\n{'='*80}")
    print(f"🚀 DeepAI 模型训练流程")
    print(f"{'='*80}\n")
    
    # 配置设备
    configure_device()
    
    # 加载数据
    labels_path = os.path.join(DATA_DIR, 'labels.json')
    images, labels, idx_to_label = load_dataset(labels_path)
    
    if images is None:
        return
    
    # 划分数据集
    print(f"{'='*80}")
    print(f"📊 划分数据集")
    print(f"{'='*80}")
    
    X_train, X_test, y_train, y_test = train_test_split(
        images, labels, test_size=0.2, random_state=42, stratify=labels
    )
    
    X_train, X_val, y_train, y_val = train_test_split(
        X_train, y_train, test_size=0.2, random_state=42, stratify=y_train
    )
    
    print(f"训练集: {X_train.shape[0]} 样本")
    print(f"验证集: {X_val.shape[0]} 样本")
    print(f"测试集: {X_test.shape[0]} 样本")
    print(f"✅ 划分完成!\n")
    
    # 创建模型
    num_classes = len(idx_to_label)
    model = create_model(num_classes)
    
    # 训练模型
    history = train_model(model, X_train, y_train, X_val, y_val)
    
    # 绘制训练曲线
    history_plot_path = os.path.join(MODELS_DIR, 'training_history.png')
    plot_training_history(history, history_plot_path)
    
    # 评估模型
    evaluate_model(model, X_test, y_test, idx_to_label)
    
    # 保存标签映射
    label_map_path = os.path.join(MODELS_DIR, 'label_map.json')
    with open(label_map_path, 'w', encoding='utf-8') as f:
        json.dump(idx_to_label, f, indent=2, ensure_ascii=False)
    print(f"标签映射已保存: {label_map_path}")
    
    print(f"\n{'='*80}")
    print(f"✅ 训练流程完成!")
    print(f"{'='*80}")
    print(f"\n生成的文件:")
    print(f"  模型文件: {MODEL_SAVE_PATH}")
    print(f"  标签映射: {label_map_path}")
    print(f"  训练曲线: {history_plot_path}")
    print(f"\n下一步: 运行评估脚本")
    print(f"  python deepai/scripts/04_evaluate_model.py")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
