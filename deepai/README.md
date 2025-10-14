# DeepAI CNN 训练方案完整指南

## 📋 概述

本方案提供**三种**数字识别引擎，满足不同用户需求：

### 🎯 三种识别引擎

| 引擎                 | 速度  | 准确率  | 依赖       | 需要训练 | 适用场景                 |
| -------------------- | ----- | ------- | ---------- | -------- | ------------------------ |
| **1️⃣ 模板匹配**      | ~7ms  | 90-95%  | 无         | ✅ 是    | 推荐，最快，无额外依赖 🚀 |
| **2️⃣ Keras 模型**    | ~47ms | >99%    | TensorFlow | ✅ 是    | 最高准确率               |
| **3️⃣ Tesseract OCR** | ~240ms | 95-100% | Tesseract  | ❌ 否    | 通用性强，稳定可靠       |

**一次训练，获得两种高性能引擎！**

- 训练过程会自动生成：数字模板 + Keras 模型
- 用户可根据需求选择最适合的引擎

---

## 🚀 快速开始

### 方式 A：Tesseract OCR（默认，推荐新手）

1. 确保已安装 Tesseract
2. 在主程序 UI 中选择 "Tesseract" 引擎
3. 直接使用！

### 方式 B：使用 Keras 模型（高性能）

1. 安装依赖: `pip install -r deepai/requirements.txt`
2. 在主程序 UI 中选择 "Keras" 引擎
3. 直接使用（如果项目包含预训练模型）或运行训练流程

---

## 📦 安装指南

### 完整安装（推荐）

```bash
pip install -r deepai/requirements.txt
```

**包含**：

- ✅ TensorFlow CPU 版本（轻量级，无需 GPU）
- ✅ NumPy（数据处理）
- ✅ OpenCV（图像处理）
- ✅ scikit-learn（机器学习工具）
- ✅ matplotlib（基础可视化）
- ✅ seaborn（高级可视化，可选）
- ✅ Pillow（GUI 工具）
- ✅ pytesseract（OCR 自动标注）

### 最小安装

```bash
pip install tensorflow numpy opencv-python scikit-learn matplotlib Pillow pytesseract
```

### 依赖检查

```bash
python -c "import sys; deps = {'tensorflow': 'TensorFlow', 'numpy': 'NumPy', 'cv2': 'OpenCV', 'sklearn': 'scikit-learn', 'matplotlib': 'Matplotlib', 'PIL': 'Pillow', 'pytesseract': 'pytesseract'}; print('核心依赖:'); [print(f'  ✅ {name}') if __import__(module) or True else print(f'  ❌ {name} - 未安装') for module, name in deps.items()]"
```

---

## 🎓 迭代式训练流程

### 主控脚本

```bash
python deepai/scripts/00_iterative_labeling.py
```

### 菜单选项

```
--- 迭代式AI训练工作流 ---
1a. 从视频生成数字图像 (提取帧 → 裁剪 → 分割)
1b. Tesseract自动标注 (对生成的图像进行初始标注)
2.  人工验证标注 (核心步骤，提升模型精度)
3.  训练模型 (当有足够已验证的标注时)
4.  模型辅助标注 (用训练好的模型加速标注)
5.  评估模型 (查看模型性能和准确率)
6.  生成最终产物 (评估模型 + 生成模板)
0.  退出
```

### 典型工作流程

#### 首次训练

```
1a → 1b → 2 → 3 → 5
```

#### 迭代优化

```
4 → 2 → 3 → 5 (重复2-3次)
```

#### 最终完成

```
5 → 6
```

---

## 📝 详细步骤说明

### 步骤 1a：从视频生成数字图像

**功能**：

- 从视频提取帧 → `deepai/data/processed/frames/`
- 裁剪 HP/MP 区域 → `deepai/data/processed/hp/` 和 `mp/`
- 分割单个数字 → `deepai/data/digits/`

**智能采样**：

- HP 区域采样率：10%（HP 变化慢，只需少量样本）
- MP 区域采样率：100%（MP 变化快，需要更多样本）
- 自动去除逗号噪点（使用开运算）

**何时使用**：

- 首次运行
- 增加新视频数据

**输出**：

- 数字图像文件
- 未标注的 `labels.json`

### 步骤 1b：Tesseract 自动标注

**功能**：

- 使用 Tesseract 对数字图像进行自动标注
- 输出每个样本的识别结果

**输出格式**：

```
  [   1/1932] ✓ hp_000000_digit_0.png → '1' (95.2%)
  [   2/1932] ? hp_000000_digit_1.png → '2' (78.5%)
  [   3/1932] ✗ hp_000000_digit_2.png → (识别失败)
```

**状态符号**：

- `✓` - 高置信度（≥ 90%）
- `?` - 低置信度（< 90%），需要验证
- `✗` - 识别失败

**何时使用**：

- 在步骤 1a 之后
- 重新标注所有样本

**准确率**：70-85%

### 步骤 2：人工验证标注

**核心原则**：只验证 Tesseract 识别失败或低置信度的样本，高置信度样本自动通过。

**工作模式**：

- ✅ 高置信度样本（≥ 90%）- 自动标记为已验证（无需人工检查）
- ⚠️ 低置信度样本（< 90%）- 需要人工验证
- ✗ 识别失败样本 - 需要人工标注

**GUI 快捷键**：

- **0-9, /** : 快速标注（自动保存并跳到下一个）
- **Enter/→** : 保存并下一个
- **←** : 上一个
- **Space** : 跳过
- **Delete** : 删除
- **Ctrl+S** : 保存退出

**效率提升**：

- 第 1 轮：只需验证 ~15-30% 的样本
- 第 2 轮：只需验证 ~5-10% 的样本
- 第 3 轮：只需验证 ~1-2% 的样本

### 步骤 3：训练模型

**功能**：

- 使用已验证的数据训练 CNN 模型
- 保存模型到 `deepai/models/digit_cnn.keras`
- 支持 Keras 3.x
- 中文字体支持

**训练参数**（在 `config.py` 中配置）：

- 批次大小：32
- 训练轮数：20
- 学习率：0.001
- 验证集比例：20%
- 图像尺寸：28x28
- 类别数量：11（0-9 + /）

**何时使用**：

- 有足够已验证的样本（建议 > 500）
- 每次迭代后

**输出**：

- 训练好的模型 `digit_cnn.keras`
- 训练历史图表 `training_history.png`
- 标签映射 `label_map.json`

### 步骤 4：模型辅助标注

**功能**：

- 使用训练好的模型重新标注未验证的样本
- 自动重新加载最新模型
- 输出每个样本的识别结果

**输出格式**：

```
  [   1/1932] ✓ hp_000000_digit_0.png → '1' (99.8%)
  [   2/1932] ? mp_000000_digit_0.png → '5' (92.5%)
  [   3/1932] ✗ mp_000000_digit_1.png → (加载失败)
```

**何时使用**：

- 在步骤 3 之后
- 迭代优化时

**准确率**：95-99%

### 步骤 5：评估模型

**功能**：

- 评估模型性能
- 生成混淆矩阵
- 分析错误样本
- 测试推理速度
- 输出清晰的评估结论

**评估结论示例**：

```
================================================================================
📊 评估结论
================================================================================
🎯 模型性能:
   准确率: 98.86%
   测试样本数: 1932
   正确识别: 1910
   错误识别: 22

⚡ 推理速度:
   单个字符: ~15.23 ms
   完整HP/MP (5个字符): ~76.15 ms

📈 评估等级: ✅ 良好
   模型表现良好，建议继续优化。

下一步:
  ✅ 模型已达到可用标准，可以集成到应用中
  ✅ 在主程序UI中选择 'Keras' 引擎
================================================================================
```

**评估等级**：

- 🌟 优秀（≥ 99%）：可以投入使用
- ✅ 良好（95-99%）：可以使用，建议继续优化
- ⚠️ 一般（90-95%）：需要更多训练
- ❌ 较差（< 90%）：需要重新训练

**何时使用**：

- 在步骤 3 之后
- 想了解模型性能时
- 决定是否继续训练时

**输出**：

- 准确率、速度等指标
- 混淆矩阵图片 `confusion_matrix.png`
- 错误样本图片（保存在 `models/errors/`）
- 评估等级和建议

### 步骤 6：生成最终产物

**功能**：

- 运行步骤 5（评估模型）
- 生成数字模板到 `game_char_templates/hp_mp_digits/`

**何时使用**：

- 训练完成，准备投入使用
- 生成所有最终文件

**输出**：

- 评估报告
- 数字模板（每个字符的最佳样本）

---

## 🔄 迭代优化策略

### 数据量与准确率

| 轮次    | 验证样本数 | 模型准确率 | 人工验证量 |
| ------- | ---------- | ---------- | ---------- |
| 第 1 轮 | 500        | 95-96%     | ~300 样本  |
| 第 2 轮 | 1000       | 97-98%     | ~100 样本  |
| 第 3 轮 | 1500       | 98-99%     | ~30 样本   |
| 第 4 轮 | 2000       | > 99%      | ~10 样本   |

### 时间投入

| 步骤         | 首次       | 后续      |
| ------------ | ---------- | --------- |
| 1a. 生成数字 | 5-10 分钟  | -         |
| 1b. 自动标注 | 2-3 分钟   | 2-3 分钟  |
| 2. 人工验证  | 20-40 分钟 | 5-10 分钟 |
| 3. 训练模型  | 5-10 分钟  | 5-10 分钟 |
| 4. 模型标注  | 2-3 分钟   | 2-3 分钟  |

**总时间**：

- 第 1 轮：~40 分钟
- 第 2 轮：~20 分钟
- 第 3 轮：~15 分钟

---

## ⚙️ 使用训练好的模型

### 在主程序中使用

1. 打开主程序
2. 进入 **资源管理** 配置
3. 设置 **HP/MP 检测模式** 为 **文本 OCR**
4. 在 **OCR 引擎** 下拉框中选择：
   - **模板匹配（推荐）** - 最快速度，无额外依赖
   - **Keras 模型** - 最高准确率，需要 TensorFlow
   - **Tesseract** - 通用性强，需要 Tesseract

### 快速测试

```python
from deepai import get_recognizer
import cv2

# 使用模板匹配（推荐）
recognizer = get_recognizer("template")

# 识别单个数字
img = cv2.imread("path/to/digit.png", cv2.IMREAD_GRAYSCALE)
label, confidence = recognizer.recognize_digit(img)
print(f"识别结果: {label} (置信度: {confidence:.1%})")

# 识别完整HP/MP
img = cv2.imread("path/to/hp_region.png")
current, maximum = recognizer.recognize_and_parse(img)
print(f"HP/MP: {current}/{maximum}")
```

### 性能对比

| 引擎       | 准确率  | 速度   | 内存占用 | 依赖       |
| ---------- | ------- | ------ | -------- | ---------- |
| 模板匹配   | 90-95%  | ~7ms   | 极低     | 无         |
| Keras 模型 | >99%    | ~47ms  | 中等     | TensorFlow |
| Tesseract  | 95-100% | ~240ms | 低       | Tesseract  |

---

## 🐛 常见问题

### Q1: TensorFlow 安装失败

**解决方案**：

```bash
# 方案1：安装特定版本
pip install tensorflow==2.15.0

# 方案2：使用国内镜像
pip install tensorflow -i https://pypi.tuna.tsinghua.edu.cn/simple

# 方案3：升级pip
python -m pip install --upgrade pip
pip install tensorflow
```

### Q2: Seaborn 未安装

**问题**：

```
ModuleNotFoundError: No module named 'seaborn'
```

**解决方案**：

```bash
# 方案1：安装seaborn（推荐）
pip install seaborn

# 方案2：不安装（脚本会自动使用matplotlib替代）
# 无需任何操作，脚本会正常运行
```

### Q3: pytesseract 找不到 tesseract

**解决方案**：

1. 下载安装 Tesseract：https://github.com/UB-Mannheim/tesseract/wiki
2. 配置路径（在 `deepai/config.py` 中）：
   ```python
   TESSERACT_CMD = r"D:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

### Q4: 验证界面是空白的

**检查清单**：

1. 是否运行了数据准备脚本？
   ```bash
   python deepai/scripts/01a_generate_digits.py
   python deepai/scripts/01b_auto_label.py
   ```
2. labels.json 文件是否存在？
   ```bash
   dir deepai\data\labels.json
   ```
3. 图像文件是否存在？
   ```bash
   dir deepai\data\digits\
   ```

### Q5: 如何配置视频路径和坐标？

**解决方案**：
编辑 `deepai/config.py` 文件：

```python
# 游戏录制视频路径
VIDEO_PATH = "deepai/data/raw/game_capture.mp4"

# HP区域坐标 (x1, y1, x2, y2)
HP_REGION = (97, 814, 228, 835)

# MP区域坐标 (x1, y1, x2, y2)
MP_REGION = (1767, 814, 1894, 835)
```

---

## 📁 目录结构

```
deepai/
├── README.md                    # 本文档
├── config.py                    # 配置文件
├── requirements.txt             # 依赖列表
├── recognizer.py               # 识别器实现
├── __init__.py                 # 模块初始化
├── scripts/                    # 训练脚本
│   ├── 00_iterative_labeling.py  # 主控脚本
│   ├── 01_prepare_data.py        # 完整数据准备（已弃用）
│   ├── 01a_generate_digits.py    # 生成数字图像
│   ├── 01b_auto_label.py         # 自动标注
│   ├── 02_verify_labels.py       # 人工验证
│   ├── 03_train_model.py         # 训练模型
│   ├── 04_evaluate_model.py      # 评估模型
│   └── 06_generate_digit_templates.py  # 生成模板
├── data/                       # 数据目录
│   ├── raw/                    # 原始视频
│   ├── processed/              # 处理后数据
│   │   ├── frames/            # 视频帧
│   │   ├── hp/                # HP区域图像
│   │   └── mp/                # MP区域图像
│   ├── digits/                # 单个数字图像
│   └── labels.json            # 标注数据
├── models/                    # 模型目录
│   ├── digit_cnn.keras       # 训练好的模型
│   ├── label_map.json        # 标签映射
│   ├── training_history.png  # 训练历史
│   ├── confusion_matrix.png  # 混淆矩阵
│   └── errors/               # 错误分析
└── logs/                     # 日志目录
```

---

## 💡 最佳实践

### 1. 首次使用

```bash
# 完整流程
python deepai/scripts/00_iterative_labeling.py
# 选择: 1a → 1b → 2 → 3 → 5
```

**建议**：

- 验证至少 500 个样本
- 确保每个数字（0-9）和斜杠都有足够样本
- 关注低置信度样本的验证

### 2. 迭代优化

```bash
# 使用模型加速
python deepai/scripts/00_iterative_labeling.py
# 选择: 4 → 2 → 3 → 5 → (重复)
```

**建议**：

- 每轮只需验证低置信度样本
- 通常 < 100 个样本
- 重复 2-3 轮达到最佳效果

### 3. 数据增强

```bash
# 如果模型准确率不理想
python deepai/scripts/00_iterative_labeling.py
# 选择: 1a → 1b → 2 → 3
```

**建议**：

- 增加更多视频数据
- 覆盖更多场景和数值
- 确保数据多样性

### 4. 最终验收

```bash
# 生成模型和模板
python deepai/scripts/00_iterative_labeling.py
# 选择: 5 → 6
```

**检查清单**：

- ✅ 准确率 ≥ 99%
- ✅ 推理速度 < 20ms
- ✅ 错误样本 < 20 个
- ✅ 所有类别精确率 > 95%

---

## 📊 配置参数说明

### 视频和坐标配置

在 `deepai/config.py` 中配置：

```python
# 游戏录制视频路径
VIDEO_PATH = "deepai/data/raw/game_capture.mp4"

# HP区域坐标 (x1, y1, x2, y2)
HP_REGION = (97, 814, 228, 835)

# MP区域坐标 (x1, y1, x2, y2)
MP_REGION = (1767, 814, 1894, 835)
```

### 数据准备配置

```python
# 帧提取间隔（每N帧提取一次）
FRAME_SAMPLE_INTERVAL = 15

# HP/MP采样率配置
HP_SAMPLE_RATIO = 0.1  # HP区域采样率：10%
MP_SAMPLE_RATIO = 1.0  # MP区域采样率：100%

# 单个数字最小尺寸（过滤噪点）
MIN_DIGIT_WIDTH = 6
MIN_DIGIT_HEIGHT = 10
```

### OCR 自动标注配置

```python
# Tesseract可执行文件路径
TESSERACT_CMD = r"D:\Program Files\Tesseract-OCR\tesseract.exe"

# Tesseract配置（白名单不包含逗号）
TESSERACT_CONFIG = '--psm 7 -c tessedit_char_whitelist=0123456789/'

# 自动标注置信度阈值
AUTO_LABEL_CONFIDENCE = 0.9
```

### 模型训练配置

```python
# 训练参数
BATCH_SIZE = 32
EPOCHS = 20
LEARNING_RATE = 0.001
VALIDATION_SPLIT = 0.2

# 设备配置
USE_GPU = False  # CPU训练完全足够

# 图像尺寸
IMG_WIDTH = 28
IMG_HEIGHT = 28

# 类别数量（0-9 + /）
NUM_CLASSES = 11
```

---

## ✅ 总结

### 核心优势

1. **三种引擎** - 满足不同性能需求
2. **迭代训练** - 逐步提升准确率
3. **智能采样** - HP/MP 区域差异化处理
4. **自动化** - 高置信度样本自动通过
5. **可视化** - 完整的评估和分析

### 预期效果

- **总时间**：~1.5 小时
- **最终准确率**：> 99%
- **识别速度**：< 25ms
- **训练样本**：1500-2000+

### 使用建议

**根据需求选择引擎**：

- **推荐首选** → 模板匹配（最快，无额外依赖）
- 追求准确率 → Keras 模型（需要 TensorFlow）
- 无需训练 → Tesseract OCR（需要 Tesseract）

**开始你的 DeepAI 训练之旅吧！** 🚀
