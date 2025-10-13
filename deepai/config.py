#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DeepAI训练配置文件 - 请根据实际情况修改"""

from pathlib import Path

# ============================================================================
# 📹 视频和坐标配置（请修改为您的实际值）
# ============================================================================

# 游戏录制视频路径
VIDEO_PATH = "deepai/data/raw/game_capture.mp4"

# HP区域坐标 (x1, y1, x2, y2)
HP_REGION = (97, 814, 228, 835)

# MP区域坐标 (x1, y1, x2, y2)
MP_REGION = (1767, 814, 1894, 835)

# ============================================================================
# 📊 数据准备配置
# ============================================================================

# 帧提取间隔（每N帧提取一次，30fps视频建议15-30）
FRAME_SAMPLE_INTERVAL = 15

# 最大提取帧数（避免数据过多，0表示不限制）
MAX_FRAMES = 0

# 单个数字最小宽度和高度（像素，用于过滤噪点）
MIN_DIGIT_WIDTH = 6
MIN_DIGIT_HEIGHT = 10

# ============================================================================
# 🔤 OCR自动标注配置
# ============================================================================

# Tesseract可执行文件路径（用于自动标注）
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Tesseract配置
TESSERACT_CONFIG = '--psm 7 -c tessedit_char_whitelist=0123456789/'

# 自动标注置信度阈值（低于此值的需要人工验证）
AUTO_LABEL_CONFIDENCE = 0.9

# ============================================================================
# 🧠 模型训练配置
# ============================================================================

# 模型保存路径
MODEL_SAVE_PATH = "deepai/models/digit_cnn.h5"

# 训练参数
BATCH_SIZE = 32
EPOCHS = 20
LEARNING_RATE = 0.001
VALIDATION_SPLIT = 0.2

# 设备配置（支持CPU训练）
USE_GPU = False  # 设置为False使用CPU训练,11个字符的轻量级模型CPU训练完全足够
GPU_MEMORY_LIMIT = None  # GPU显存限制(MB)，None表示不限制

# 数据增强参数
AUGMENTATION = {
    'rotation_range': 10,
    'width_shift_range': 0.1,
    'height_shift_range': 0.1,
    'zoom_range': 0.1,
}

# 早停参数
EARLY_STOPPING_PATIENCE = 5

# 图像尺寸（训练时统一调整到此尺寸）
IMG_WIDTH = 28
IMG_HEIGHT = 28

# 图像尺寸（统一缩放到此尺寸）
IMAGE_WIDTH = 28
IMAGE_HEIGHT = 28

# 类别数量（0-9 + /）
NUM_CLASSES = 11

# 类别映射
CLASS_NAMES = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '/']

# ============================================================================
# 📂 目录配置
# ============================================================================

# 使用pathlib构建稳健的跨平台路径
BASE_DIR = Path(__file__).resolve().parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DIGITS_DIR = DATA_DIR / "digits"

# 模型目录
MODELS_DIR = BASE_DIR / "models"

# 日志目录
LOGS_DIR = BASE_DIR / "logs"

# 确保目录存在
for dir_path in [RAW_DATA_DIR, PROCESSED_DATA_DIR, DIGITS_DIR, MODELS_DIR, LOGS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# ============================================================================
# 🎨 可视化配置
# ============================================================================

# 是否显示详细调试信息
VERBOSE = True

# 是否显示数据准备过程的图像
SHOW_PREVIEW = False

# 训练过程中保存模型检查点
SAVE_CHECKPOINTS = True

print("=" * 80)
print("DeepAI配置已加载")
print("=" * 80)
print(f"视频路径: {VIDEO_PATH}")
print(f"HP区域: {HP_REGION}")
print(f"MP区域: {MP_REGION}")
print(f"模型保存: {MODEL_SAVE_PATH}")
print("=" * 80)
