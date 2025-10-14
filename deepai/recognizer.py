#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DeepAI识别器 - 提供三种识别引擎的统一接口"""

import sys
import numpy as np
import cv2
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tensorflow as tf
    from tensorflow import keras

    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False


class KerasDigitRecognizer:
    """Keras数字识别器（单例模式）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        self.model: Optional[keras.Model] = None
        self.idx_to_label: Optional[Dict[int, str]] = None
        self.img_width = 28
        self.img_height = 28
        self._initialized = False
        self._model_timestamp = None  # 记录模型文件的修改时间

        self.project_root = Path(__file__).resolve().parent.parent
        self.models_dir = self.project_root / "deepai" / "models"

    def initialize(
        self, model_path: Optional[str] = None, label_map_path: Optional[str] = None, force_reload: bool = False
    ) -> bool:
        """初始化识别器
        
        Args:
            model_path: 模型文件路径
            label_map_path: 标签映射文件路径
            force_reload: 是否强制重新加载模型
        """
        if self._initialized and not force_reload:
            # 检查模型文件是否被更新
            if self._check_model_updated():
                print("🔄 检测到模型文件已更新，重新加载...")
                force_reload = True
            else:
                return True
        
        if force_reload:
            self._initialized = False

        if not TENSORFLOW_AVAILABLE:
            print("❌ TensorFlow未安装，请运行: pip install tensorflow")
            return False

        try:
            found_model_path = self._find_model(model_path)
            if not found_model_path:
                return False

            label_map_path = label_map_path or self.models_dir / "label_map.json"
            if not Path(label_map_path).exists():
                print(f"❌ 标签映射不存在: {label_map_path}")
                return False

            # 加载Keras模型
            self.model = keras.models.load_model(str(found_model_path))
            
            # 记录模型文件的修改时间
            self._model_path = found_model_path
            self._model_timestamp = found_model_path.stat().st_mtime

            # 加载标签映射
            with open(label_map_path, "r", encoding="utf-8") as f:
                self.idx_to_label = {int(k): v for k, v in json.load(f).items()}

            # 预热模型（触发图编译，避免首次推理慢）
            dummy_input = np.zeros((1, self.img_height, self.img_width, 1), dtype=np.float32)
            self.model.predict(dummy_input, verbose=0)

            self._initialized = True
            print(f"✅ Keras模型初始化成功: {found_model_path.name}")
            return True

        except Exception as e:
            print(f"❌ Keras模型初始化失败: {e}")
            return False
    
    def _check_model_updated(self) -> bool:
        """检查模型文件是否被更新"""
        if not hasattr(self, '_model_path') or not hasattr(self, '_model_timestamp'):
            return False
        
        if not self._model_path.exists():
            return False
        
        current_timestamp = self._model_path.stat().st_mtime
        return current_timestamp > self._model_timestamp

    def _find_model(self, model_path: Optional[str]) -> Optional[Path]:
        """查找模型文件（.keras或.h5）"""
        if model_path:
            p = Path(model_path)
            if p.exists():
                print(f"✅ 使用指定模型: {p}")
                return p

        # 优先使用.keras格式
        user_model_keras = self.models_dir / "digit_cnn.keras"
        if user_model_keras.exists():
            print(f"✅ 使用用户训练的Keras模型: {user_model_keras.name}")
            return user_model_keras

        # 兼容旧的.h5格式
        user_model_h5 = self.models_dir / "digit_cnn.h5"
        if user_model_h5.exists():
            print(f"✅ 使用用户训练的H5模型: {user_model_h5.name}")
            return user_model_h5

        print(f"❌ 未找到模型文件 (.keras 或 .h5)")
        print(f"   请先运行训练脚本: python deepai/scripts/03_train_model.py")
        return None

    def preprocess_image(self, img: np.ndarray) -> np.ndarray:
        """预处理图像，通过开运算去除逗号等噪点"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 使用2x2的核进行开运算，可以有效去除逗号和小的噪点
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

        return opened

    def segment_digits(
        self, binary: np.ndarray, min_width: int = 6, min_height: int = 10
    ) -> List[np.ndarray]:
        """分割单个数字"""
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes = sorted(
            [
                cv2.boundingRect(c)
                for c in contours
                if cv2.boundingRect(c)[2] >= min_width
                and cv2.boundingRect(c)[3] >= min_height
            ],
            key=lambda b: b[0],
        )
        return [binary[y : y + h, x : x + w] for x, y, w, h in boxes]

    def recognize_digit(self, digit_img: np.ndarray) -> Tuple[Optional[str], float]:
        """识别单个数字"""
        if not self._initialized or self.model is None:
            return None, 0.0

        try:
            resized = cv2.resize(
                digit_img,
                (self.img_width, self.img_height),
                interpolation=cv2.INTER_AREA,
            )
            normalized = resized.astype(np.float32) / 255.0
            input_data = np.expand_dims(normalized, axis=(0, -1))

            output = self.model.predict(input_data, verbose=0)[0]

            pred_idx = int(np.argmax(output))
            if self.idx_to_label is None:
                return None, 0.0
            return self.idx_to_label.get(pred_idx), float(output[pred_idx])

        except Exception as e:
            print(f"❌ Keras识别失败: {e}")
            return None, 0.0

    def recognize_digits_batch(self, digit_imgs: List[np.ndarray]) -> List[Tuple[Optional[str], float]]:
        """批量识别多个数字（性能优化）"""
        if not self._initialized or self.model is None:
            return [(None, 0.0)] * len(digit_imgs)

        if not digit_imgs:
            return []

        try:
            # 批量预处理
            batch_data = []
            for digit_img in digit_imgs:
                resized = cv2.resize(
                    digit_img,
                    (self.img_width, self.img_height),
                    interpolation=cv2.INTER_AREA,
                )
                normalized = resized.astype(np.float32) / 255.0
                batch_data.append(normalized)

            # 批量推理（一次性处理所有数字）
            batch_input = np.array(batch_data)[..., np.newaxis]
            outputs = self.model.predict(batch_input, verbose=0)

            # 解析结果
            results = []
            for output in outputs:
                pred_idx = int(np.argmax(output))
                if self.idx_to_label is None:
                    results.append((None, 0.0))
                else:
                    results.append((self.idx_to_label.get(pred_idx), float(output[pred_idx])))

            return results

        except Exception as e:
            print(f"❌ Keras批量识别失败: {e}")
            return [(None, 0.0)] * len(digit_imgs)

    def recognize_and_parse(
        self, img: np.ndarray
    ) -> Tuple[Optional[int], Optional[int]]:
        """识别并解析HP/MP数字（格式：123/456）"""
        if not self._initialized:
            return None, None

        try:
            binary = self.preprocess_image(img)
            digit_images = self.segment_digits(binary)

            if not digit_images:
                return None, None

            # 使用批量识别（性能优化）
            results = self.recognize_digits_batch(digit_images)
            result = "".join([label for label, _ in results if label])

            if "/" in result:
                parts = result.split("/", 1)
                if len(parts) == 2:
                    try:
                        current = int(parts[0].replace(",", ""))
                        maximum = int(parts[1].replace(",", ""))
                        return current, maximum
                    except ValueError:
                        pass
            return None, None

        except Exception as e:
            print(f"❌ Keras解析失败: {e}")
            return None, None


class TemplateDigitRecognizer:
    """模板匹配数字识别器（单例模式）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        self.templates: Dict[str, np.ndarray] = {}  # 每个字符只有一个模板
        self._initialized = False

        self.project_root = Path(__file__).resolve().parent.parent
        self.templates_dir = self.project_root / "game_char_templates"

    def initialize(self, templates_dir: Optional[str] = None) -> bool:
        """初始化识别器"""
        if self._initialized:
            return True

        try:
            templates_path = Path(templates_dir) if templates_dir else self.templates_dir
            
            if not templates_path.exists():
                print(f"❌ 模板目录不存在: {templates_path}")
                return False

            # 加载单模板文件（格式：template_0_11x16.png, template_slash_6x16.png）
            template_files = list(templates_path.glob("template_*.png"))
            
            if not template_files:
                print(f"❌ 未找到模板文件（格式：template_X_WxH.png）")
                return False
            
            template_count = 0
            
            for template_file in template_files:
                # 解析文件名
                name = template_file.stem  # 去掉 .png
                parts = name.split('_')
                
                if len(parts) < 2:
                    continue
                
                # 获取字符标签
                label = parts[1]
                if label == 'slash':
                    label = '/'
                
                # 加载模板图像
                img = cv2.imread(str(template_file), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.templates[label] = img
                    template_count += 1

            if not self.templates:
                print(f"❌ 未找到任何有效模板")
                return False

            self._initialized = True
            print(f"✅ 模板识别器初始化成功: 加载了 {template_count} 个模板")
            print(f"   字符: {sorted(self.templates.keys())}")
            return True

        except Exception as e:
            print(f"❌ 模板识别器初始化失败: {e}")
            return False

    def preprocess_image(self, img: np.ndarray) -> np.ndarray:
        """预处理图像"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        return opened

    def segment_digits(
        self, binary: np.ndarray, min_width: int = 6, min_height: int = 10
    ) -> List[np.ndarray]:
        """分割单个数字"""
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes = sorted(
            [
                cv2.boundingRect(c)
                for c in contours
                if cv2.boundingRect(c)[2] >= min_width
                and cv2.boundingRect(c)[3] >= min_height
            ],
            key=lambda b: b[0],
        )
        return [binary[y : y + h, x : x + w] for x, y, w, h in boxes]

    def recognize_digit(self, digit_img: np.ndarray) -> Tuple[Optional[str], float]:
        """识别单个数字"""
        if not self._initialized:
            return None, 0.0

        try:
            best_label = None
            best_score = 0.0

            for label, template in self.templates.items():
                # 调整大小匹配
                resized = cv2.resize(digit_img, (template.shape[1], template.shape[0]))
                
                # 模板匹配
                result = cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED)
                score = result[0][0]
                
                if score > best_score:
                    best_score = score
                    best_label = label

            return best_label, float(best_score)

        except Exception as e:
            print(f"❌ 模板识别失败: {e}")
            return None, 0.0

    def recognize_and_parse(
        self, img: np.ndarray
    ) -> Tuple[Optional[int], Optional[int]]:
        """识别并解析HP/MP数字（格式：123/456）"""
        if not self._initialized:
            return None, None

        try:
            binary = self.preprocess_image(img)
            digit_images = self.segment_digits(binary)

            if not digit_images:
                return None, None

            result = "".join(
                [
                    label
                    for digit_img in digit_images
                    if (label := self.recognize_digit(digit_img)[0])
                ]
            )

            if "/" in result:
                parts = result.split("/", 1)
                if len(parts) == 2:
                    try:
                        current = int(parts[0].replace(",", ""))
                        maximum = int(parts[1].replace(",", ""))
                        return current, maximum
                    except ValueError:
                        pass
            return None, None

        except Exception as e:
            print(f"❌ 模板解析失败: {e}")
            return None, None


_recognizer_cache = {}


def get_recognizer(engine: str = "keras"):
    """获取识别器实例
    
    Args:
        engine: 引擎类型，支持 'keras' 或 'template'
    """
    if engine not in _recognizer_cache:
        if engine == "keras":
            recognizer = KerasDigitRecognizer()
            if not recognizer.initialize():
                return None
            _recognizer_cache["keras"] = recognizer
        elif engine == "template":
            recognizer = TemplateDigitRecognizer()
            if not recognizer.initialize():
                return None
            _recognizer_cache["template"] = recognizer
        elif engine == "tesseract":
            raise NotImplementedError("Tesseract引擎请直接使用TesseractOcrManager")
        else:
            raise ValueError(f"未知引擎: {engine}，支持 'keras' 或 'template'")
    return _recognizer_cache.get(engine)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python recognizer.py <image_path>")
        sys.exit(1)

    recognizer = get_recognizer("keras")
    if not recognizer:
        sys.exit(1)

    img = cv2.imread(sys.argv[1])
    if img is None:
        print(f"❌ 无法加载图像: {sys.argv[1]}")
        sys.exit(1)

    current, maximum = recognizer.recognize_and_parse(img)
    print(f"识别结果: {current}/{maximum}" if current is not None else "❌ 识别失败")
