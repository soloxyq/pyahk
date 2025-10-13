# DeepAI 一次到位的操作步骤（Windows/PowerShell）

本指南只保留你需要的最小步骤，从零开始到在主程序中使用高性能数字识别（Tesseract/TFLite）。模板生成亦包含在一次训练流程中（识别器后续集成）。

---

## 0. 环境准备（一次性）

- Python 3.9–3.11（建议 3.10）
- 可选：NVIDIA GPU（仅训练提速，CPU已足够）
- 可选：Tesseract（自动标注用，路径在 `deepai/config.py` 中可改）

安装依赖：

```powershell
# 仅推理（TFLite）
pip install -r deepai/requirements_inference.txt

# 训练（TensorFlow + 辅助包）
pip install -r deepai/requirements_cpu.txt
```

---

## 1. 必要配置（只需一次）

编辑 `deepai/config.py`：

- 录制视频路径（默认）：
  - `VIDEO_PATH = "deepai/data/raw/game_capture.mp4"`
- HP/MP 区域坐标（示例，仅供参考，建议用主程序区域选择工具重新测量）：
  - `HP_REGION = (97, 814, 218, 835)`
  - `MP_REGION = (1767, 814, 1894, 835)`
- 训练参数与数据增强已内置，普通用户无需改动。

注意：`config.py` 已使用 pathlib，路径跨平台稳健。

---

## 2. 采集素材（1–3分钟）

录制游戏视频，覆盖 HP/MP 的变化及数字 0–9、斜杠 `/`，保存为：

```
deepai/data/raw/game_capture.mp4
```

建议：1080p、30fps、数字清晰无遮挡。

---

## 3. 一次训练，产出两种模型

按顺序执行以下脚本（每步完成才进行下一步）：

```powershell
# 步骤1：数据准备（抽帧/裁剪/分割/自动标注）
python -m deepai.scripts.01_prepare_data
python deepai/scripts/01_prepare_data.py

# 步骤2：标注校验（可选，推荐对低置信度样本核对）
python deepai/scripts/02_verify_labels.py

# 步骤3：训练轻量级CNN（CPU约1–5分钟）
python deepai/scripts/03_train_model.py

# 步骤4：转换为 TFLite（量化优化，~50KB）
python deepai/scripts/05_convert_to_tflite.py

# 步骤5：生成数字模板（供后续模板引擎使用）
python deepai/scripts/06_generate_digit_templates.py

# 步骤6：评估（混淆矩阵/错误样本/速度统计）
python deepai/scripts/04_evaluate_model.py
```

产出物：

- `deepai/models/digit_cnn.tflite`（TFLite 模型，>98%）
- `deepai/models/label_map.json`
- `game_char_templates/hp_mp_digits/...`（0–9、slash 模板集）

---

## 4. 在主程序中使用（推荐 TFLite）

代码方式（推荐统一入口）：

```python
from deepai import get_recognizer

# 获取 TFLite 识别器
recognizer = get_recognizer('tflite')
if recognizer:
    current, maximum = recognizer.recognize_and_parse(hp_or_mp_bgr_image)
```

说明：

- 识别器会自动优先使用用户训练模型 `deepai/models/digit_cnn.tflite`；
- 若无该文件，可放置 `deepai/models/digit_pretrained.tflite` 作为后备；
- 模板引擎识别器将于后续集成，模板已生成可用于调优对照。

---

## 5. 三种引擎怎么选

- Tesseract（默认兜底）：0 训练成本，准确率高，速度最慢（~240ms/次）。
- TFLite（推荐）：需一次训练，>98% 准确率，8–12ms/次。
- 数字模板（已生成模板，识别器待接入）：90–95% 准确率，50–80ms/次，无额外依赖。

---

## 6. 常见问题速查

- 未安装 TFLite Runtime：
  ```powershell
  pip install tflite-runtime
  # 或安装完整 TensorFlow（只用到其 TFLite 部分）
  pip install tensorflow
  ```
- 训练很慢？
  - 数据量控制在 500–2000 样本足矣（视频 1–3 分钟）。
  - CPU 训练即可（1–5 分钟）；GPU 仅在频繁训练时使用。
- 识别不准？
  - 运行步骤2校验标注，或增加视频时长至 3–5 分钟。
  - 重新转换 TFLite 并评估（步骤4、6）。
- 未找到模型？
  - 确认 `deepai/models/digit_cnn.tflite` 是否存在；
  - 或放置 `deepai/models/digit_pretrained.tflite`；
  - 或重新执行训练流程（第3节）。

---

## 7. 清理与重训

```powershell
# 清理旧模型与模板
Remove-Item deepai\models\digit_cnn.h5 -ErrorAction SilentlyContinue
Remove-Item deepai\models\digit_cnn.tflite -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force game_char_templates\hp_mp_digits -ErrorAction SilentlyContinue

# 重新训练全流程（参考第3节）
python deepai/scripts/03_train_model.py
python deepai/scripts/05_convert_to_tflite.py
python deepai/scripts/06_generate_digit_templates.py
```

---

## 8. 你该做什么（最简清单）

1) 录视频 → 放到 `deepai/data/raw/game_capture.mp4`
2) 在 `deepai/config.py` 写好 HP/MP 坐标
3) 执行 01 → 03 → 05 → 06（02、04 为可选但推荐）
4) 代码中使用：`from deepai import get_recognizer`，选择 `'tflite'`

完成。
