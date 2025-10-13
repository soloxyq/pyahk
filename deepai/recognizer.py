#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DeepAI识别器 - 提供三种识别引擎的统一接口"""

import numpy as np
import cv2
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tflite_runtime.interpreter as tflite  # type: ignore
    TFLITE_AVAILABLE = True
except ImportError:
    try:
        import tensorflow.lite as tflite  # type: ignore
        TFLITE_AVAILABLE = True
    except ImportError:
        TFLITE_AVAILABLE = False


class TFLiteDigitRecognizer:
    """TFLite数字识别器（单例模式）"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.interpreter = None
        self.input_details = None
        self.output_details = None
        self.idx_to_label = None
        self.img_width = 28
        self.img_height = 28
        self._initialized = False
        
        # 使用pathlib构建路径
        self.project_root = Path(__file__).resolve().parent.parent
        self.models_dir = self.project_root / "deepai" / "models"
    
    def initialize(self, model_path=None, label_map_path=None):
        """初始化识别器"""
        if self._initialized:
            return True
        
        if not TFLITE_AVAILABLE:
            print("❌ TFLite未安装，请运行: pip install tflite-runtime")
            return False
        
        try:
            model_path = self._find_model(model_path)
            if not model_path:
                return False
            
            label_map_path = label_map_path or self.models_dir / "label_map.json"
            if not Path(label_map_path).exists():
                print(f"❌ 标签映射不存在: {label_map_path}")
                return False
            
            # 加载模型
            self.interpreter = tflite.Interpreter(model_path=str(model_path))
            self.interpreter.allocate_tensors()
            self.input_details = self.interpreter.get_input_details()
            self.output_details = self.interpreter.get_output_details()
            
            # 加载标签映射
            with open(label_map_path, 'r', encoding='utf-8') as f:
                self.idx_to_label = {int(k): v for k, v in json.load(f).items()}
            
            self._initialized = True
            print(f"✅ TFLite初始化成功: {model_path.name}")
            return True
            
        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            return False
    
    def _find_model(self, model_path):
        """查找模型文件（优先用户训练 > 预训练）"""
        if model_path:
            model_path = Path(model_path)
            if model_path.exists():
                print(f"✅ 使用指定模型: {model_path}")
                return model_path
        
        # 1. 用户训练的模型
        user_model = self.models_dir / "digit_cnn.tflite"
        if user_model.exists():
            print(f"✅ 使用用户模型: {user_model}")
            return user_model
        
        # 2. 预训练模型
        pretrained = self.models_dir / "digit_pretrained.tflite"
        if pretrained.exists():
            print(f"✅ 使用预训练模型（如不准确请自行训练）")
            return pretrained
        
        # 3. 未找到
        print(f"❌ 未找到模型文件")
        print(f"   运行训练: python deepai/scripts/01_prepare_data.py")
        return None
    
    def preprocess_image(self, img):
        """预处理图像"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        upscaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary
    
    def segment_digits(self, binary, min_width=6, min_height=10):
        """分割单个数字"""
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = sorted([cv2.boundingRect(c) for c in contours 
                       if cv2.boundingRect(c)[2] >= min_width and cv2.boundingRect(c)[3] >= min_height],
                      key=lambda b: b[0])
        return [binary[y:y+h, x:x+w] for x, y, w, h in boxes]
    
    def recognize_digit(self, digit_img):
        """识别单个数字"""
        if not self._initialized:
            return None, 0.0
        
        try:
            # 调整大小并归一化
            resized = cv2.resize(digit_img, (self.img_width, self.img_height), 
                                interpolation=cv2.INTER_AREA)
            normalized = resized.astype(np.float32) / 255.0
            input_data = np.expand_dims(normalized, axis=(0, -1))
            
            # 推理
            if self.interpreter is None or self.input_details is None or self.output_details is None:
                return None, 0.0
            self.interpreter.set_tensor(self.input_details[0]['index'], input_data)
            self.interpreter.invoke()
            output = self.interpreter.get_tensor(self.output_details[0]['index'])[0]
            
            # 结果
            pred_idx = int(np.argmax(output))
            if self.idx_to_label is None:
                return None, 0.0
            return self.idx_to_label.get(int(pred_idx), None), float(output[pred_idx]) if output is not None else 0.0
            
        except Exception as e:
            print(f"❌ 识别失败: {e}")
            return None, 0.0
    
    def recognize_and_parse(self, img):
        """识别并解析HP/MP数字（格式：123/456）
        
        Args:
            img: BGR图像
            
        Returns:
            (当前值, 最大值) 或 (None, None)
        """
        if not self._initialized:
            return None, None
        
        try:
            binary = self.preprocess_image(img)
            digit_images = self.segment_digits(binary)
            
            if not digit_images:
                return None, None
            
            # 识别所有数字
            result = ''.join([label for digit_img in digit_images 
                            if (label := self.recognize_digit(digit_img)[0])])
            
            # 解析为整数对
            if '/' in result:
                parts = result.split('/', 1)
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except ValueError:
                        pass
            
            return None, None
            
        except Exception as e:
            print(f"❌ 识别失败: {e}")
            return None, None


# 全局单例
_recognizer_cache = {}


def get_recognizer(engine='tflite'):
    """获取识别器实例
    
    Args:
        engine: 'tflite' | 'template' | 'tesseract'
    
    Returns:
        识别器实例或None
    """
    if engine not in _recognizer_cache:
        if engine == 'tflite':
            recognizer = TFLiteDigitRecognizer()
            if recognizer.initialize():
                _recognizer_cache['tflite'] = recognizer
            else:
                return None
        elif engine == 'template':
            recognizer = TemplateDigitRecognizer()
            if recognizer.initialize():
                _recognizer_cache['template'] = recognizer
            else:
                return None
        elif engine == 'tesseract':
            raise NotImplementedError("请直接使用 TesseractOcrManager")
        else:
            raise ValueError(f"未知引擎: {engine}")
    
    return _recognizer_cache.get(engine)


class TemplateDigitRecognizer:
    """模板数字识别器（单例）

    来源优先级：
    1) game_char_templates/hp_mp_digits/<char>/template_*.png（推荐，来自真实样本）
    2) game_char_templates/template_*.png（旧版扁平结构）
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        self.project_root = Path(__file__).resolve().parent.parent
        self.templates_dir = self.project_root / "game_char_templates" / "hp_mp_digits"
        self.templates: Dict[str, List[np.ndarray]] = {}
        self.match_threshold: float = 0.75
        self.enable_multiscale: bool = True
        self.scales: Tuple[float, float, float] = (0.95, 1.0, 1.05)
        self._initialized = False

    def initialize(self, match_threshold: Optional[float] = None,
                   enable_multiscale: Optional[bool] = None,
                   scales: Optional[Tuple[float, float, float]] = None) -> bool:
        if self._initialized:
            return True

        if match_threshold is not None:
            self.match_threshold = float(match_threshold)
        if enable_multiscale is not None:
            self.enable_multiscale = bool(enable_multiscale)
        if scales is not None:
            self.scales = scales

        try:
            loaded = self._load_templates()
            if not loaded:
                print("❌ 未找到任何模板，请先生成: deepai/scripts/06_generate_digit_templates.py")
                return False
            self._initialized = True
            print(f"✅ 模板引擎初始化，字符类别: {sorted(self.templates.keys())}")
            return True
        except Exception as e:
            print(f"❌ 模板引擎初始化失败: {e}")
            return False

    def _load_templates(self) -> bool:
        import cv2
        loaded_count = 0

        def add_template(ch: str, img: Optional[np.ndarray]):
            nonlocal loaded_count
            if img is None or img.size == 0:
                return
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # 统一为二值图，提升匹配稳定性
            _, bin_img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            self.templates.setdefault(ch, []).append(bin_img)
            loaded_count += 1

        # 仅支持: hp_mp_digits/<char>/template_XX.png
        if self.templates_dir.exists():
            for ch_dir in self.templates_dir.iterdir():
                if not ch_dir.is_dir():
                    continue
                ch_name = ch_dir.name
                ch = '/' if ch_name.lower() == 'slash' else ch_name
                for p in ch_dir.glob('*.png'):
                    try:
                        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                        add_template(ch, img)
                    except Exception:
                        pass

        return loaded_count > 0

    def preprocess(self, image: np.ndarray, use_adaptive: bool = False) -> np.ndarray:
        import cv2
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
        if use_adaptive:
            binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, 11, -2)
        else:
            _, binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 1))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        return binary

    def extract_char_regions(self, binary_img: np.ndarray) -> List[Tuple[int, int, int, int, np.ndarray]]:
        import cv2
        contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions: List[Tuple[int, int, int, int, np.ndarray]] = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w >= 3 and h >= 8 and w <= 20 and h <= 25:
                regions.append((x, y, w, h, binary_img[y:y+h, x:x+w]))
        regions.sort(key=lambda r: r[0])
        return regions

    def match_single_char(self, char_img: np.ndarray) -> Tuple[Optional[str], float]:
        import cv2
        best_char: Optional[str] = None
        best_score: float = 0.0
        ch_h, ch_w = char_img.shape[:2]
        scaled_images: List[Tuple[np.ndarray, float]] = []
        if self.enable_multiscale:
            for s in self.scales:
                if s == 1.0:
                    scaled_images.append((char_img, s))
                else:
                    nh, nw = max(1, int(ch_h * s)), max(1, int(ch_w * s))
                    scaled_images.append((cv2.resize(char_img, (nw, nh), interpolation=cv2.INTER_AREA), s))
        else:
            scaled_images.append((char_img, 1.0))

        for ch, t_list in self.templates.items():
            for templ in t_list:
                th, tw = templ.shape[:2]
                for test_img, _ in scaled_images:
                    h, w = test_img.shape[:2]
                    try:
                        # 尺寸相近直接 NCC
                        if h == th and w == tw:
                            res = cv2.matchTemplate(test_img, templ, cv2.TM_CCOEFF_NORMED)
                            score = float(res[0, 0])
                        else:
                            if h >= th and w >= tw:
                                res = cv2.matchTemplate(test_img, templ, cv2.TM_CCOEFF_NORMED)
                                _, score, _, _ = cv2.minMaxLoc(res)
                                score = float(score)
                            else:
                                res = cv2.matchTemplate(templ, test_img, cv2.TM_CCOEFF_NORMED)
                                _, score, _, _ = cv2.minMaxLoc(res)
                                score = float(score)
                        if score > best_score:
                            best_score, best_char = score, ch
                    except Exception:
                        continue
        return best_char, best_score

    def recognize_and_parse(self, img: np.ndarray) -> Tuple[Optional[int], Optional[int]]:
        if not self._initialized:
            return None, None
        try:
            binary = self.preprocess(img)
            regions = self.extract_char_regions(binary)
            if not regions:
                return None, None
            text = ""
            for _, _, _, _, ch_img in regions:
                ch, score = self.match_single_char(ch_img)
                if ch is not None and score >= self.match_threshold:
                    text += ch
            # 解析 123/456
            if '/' in text:
                parts = text.split('/', 1)
                if len(parts) == 2:
                    try:
                        return int(parts[0]), int(parts[1])
                    except Exception:
                        return None, None
            return None, None
        except Exception:
            return None, None


if __name__ == "__main__":
    """测试识别器"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python recognizer.py <image_path>")
        sys.exit(1)
    
    recognizer = get_recognizer('tflite')
    if not recognizer:
        print("❌ 识别器初始化失败")
        sys.exit(1)
    
    img = cv2.imread(sys.argv[1])
    if img is None:
        print(f"❌ 无法加载图像: {sys.argv[1]}")
        sys.exit(1)
    
    current, maximum = recognizer.recognize_and_parse(img)
    print(f"识别结果: {current}/{maximum}" if current else "❌ 识别失败")

