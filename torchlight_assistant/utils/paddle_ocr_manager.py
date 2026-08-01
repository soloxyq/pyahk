#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A dedicated manager for handling PaddleOCR initialization and execution."""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import re
import numpy as np
from paddleocr import PaddleOCR
from ..core.event_bus import event_bus
from .debug_log import LOG, LOG_INFO, LOG_ERROR


class PaddleOCRManager:
    """Wraps the PaddleOCR instance to control its lifecycle and provide a simple interface.

    线程模型(两条独立流水线,两把独立的锁):
    - full OCR(det+rec, ``self.ocr``): 仅配置阶段使用(GUI 定位文本框/洗练),
      构建与推理都可达秒级 —— 由 ``_full_lock`` 串行化。
    - rec-only(``self.rec_model``): 运行时资源数字识别,由**调度线程**以 200ms 周期
      调用 —— 由 ``_rec_lock`` 串行化。
    拆成两把锁的原因:旧实现共用一把 ``_init_lock``,GUI 触发的 full OCR 构建/推理
    会把调度线程的 rec-only 资源检测堵在同一把锁后面数秒 —— HP/MP 救命药剂检测
    停摆。两条流水线是各自独立的 predictor 对象,不共享 Python 侧可变状态;
    同一 predictor 非重入,故各自锁内仍串行 —— 即上游文档推荐的"一线程一 predictor"用法。
    (paddle-inference 对"不同 predictor 并发 predict"的官方保证未见明文,
    这里按上游推荐用法处理;若将来出现疑似并发崩溃,先怀疑这里。)

    rec 模型按 ``(model_name, device)`` **多槽缓存**:HP 与 MP 允许配成不同模型,
    GUI 测试按钮也可能用另一套参数;单槽实现下这些调用会互相驱逐,
    每次调用都重建一次模型(秒级),资源检测直接停摆。
    模型**构建在锁外**完成,只有发布与 predict 进锁 —— 否则冷启动一次构建
    就会把调度线程的 HP/MP 检测堵住数秒(正是本次拆锁要消除的问题)。
    """

    _instance: Optional["PaddleOCRManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        # 双检锁:GUI 线程与调度线程可能同时首次获取单例;无锁的
        # check-then-create 会产生两个实例或返回未完成 _init_internal 的实例
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super(PaddleOCRManager, cls).__new__(cls)
                    instance._init_internal()
                    cls._instance = instance   # 初始化完成后才发布
        return cls._instance

    def _init_internal(self):
        """内部初始化方法"""
        self._initialized = False
        self._initializing = False
        self._init_error = None
        self.ocr = None
        self._full_ocr_device = None
        self.rec_model = None              # 最近一次使用的 rec-only 模型(仅供状态展示/日志)
        self._rec_model_name = None
        self._rec_device = None
        self._rec_models = {}              # (model_name, device) -> TextRecognition,多槽缓存不互相驱逐
        self._full_lock = threading.Lock()   # full OCR(det+rec)构建+推理
        self._rec_lock = threading.Lock()    # rec-only 构建+推理(运行时热路径)
        self._state_lock = threading.Lock()  # _initializing/_initialized/_init_error(叶子锁,可嵌在 _full_lock 内)

        # 不自动初始化，等待用户手动触发

    def start_async_initialization(self):
        """开始异步初始化OCR引擎"""
        with self._state_lock:
            if self._initializing or self._initialized:
                LOG(f"[PaddleOCR] OCR引擎已在初始化中或已完成初始化")
                return
            self._initializing = True
        LOG_INFO("[PaddleOCR] 开始异步初始化PaddleOCR引擎...")

        def _async_init():
            try:
                LOG("[PaddleOCR] 正在初始化PaddleOCR引擎（这可能需要一些时间）...")
                if not self.ensure_full_ocr():
                    raise RuntimeError(self._init_error or "PaddleOCR初始化失败")
                with self._state_lock:
                    self._initializing = False
                LOG_INFO("[PaddleOCR] PaddleOCR引擎异步初始化成功！")
                event_bus.publish("ocr:init_success")

            except Exception as e:
                with self._state_lock:
                    self._init_error = str(e)
                    self._initializing = False
                LOG_ERROR(f"[PaddleOCR] 异步初始化PaddleOCR引擎失败: {e}")
                event_bus.publish("ocr:init_failed", {"error": str(e)})

        # 在后台线程中初始化
        init_thread = threading.Thread(target=_async_init, daemon=True)
        init_thread.start()

    def get_initialization_status(self) -> dict:
        """获取初始化状态信息"""
        return {
            "initialized": self._initialized,
            "initializing": self._initializing,
            "error": self._init_error,
        }

    def wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """等待初始化完成"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._initialized:
                return True
            if self._init_error:
                return False
            time.sleep(0.1)
        return False

    def is_ready(self) -> bool:
        """检查OCR引擎是否就绪"""
        return self._initialized and self.ocr is not None

    def ensure_full_ocr(self, device: str = "cpu") -> bool:
        """惰性初始化完整 OCR pipeline(det+rec)，仅用于配置阶段定位文本框。"""
        with self._full_lock:
            if self.ocr is not None and self._initialized and self._full_ocr_device == device:
                return True
            try:
                # 构建到局部变量,成功才替换 self.ocr:切 device 失败(如无 GPU)
                # 不应该连原来能用的 CPU 流水线一起销毁。
                new_ocr = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    text_det_limit_side_len=960,
                    # max(限制长边): 小ROI不上采样,定位~0.5s/CPU;若用 min(限制短边)会把
                    # 43px高的数字框上采样~22倍到4303px导致单次det~10s。
                    text_det_limit_type="max",
                    text_recognition_batch_size=6,
                    device=device,
                )
                self.ocr = new_ocr
                self._full_ocr_device = device
                # _initialized/_init_error 一律走 _state_lock(否则这里与
                # _async_init 的失败分支分属两把锁,谁后写谁赢,状态会互相覆盖)
                with self._state_lock:
                    self._initialized = True
                    self._init_error = None
                LOG_INFO(f"[PaddleOCR] full OCR(det+rec) 已就绪 @ {device}")
                return True
            except Exception as e:
                with self._state_lock:
                    self._init_error = str(e)
                    if self.ocr is None:
                        self._initialized = False
                LOG_ERROR(f"[PaddleOCR] full OCR(det+rec) 初始化失败: {e}")
                return False

    def get_text_from_image(self, frame: np.ndarray) -> List[str]:
        """
        Extracts recognized text from a given image frame.

        Args:
            frame: The image frame (numpy array) to process.

        Returns:
            A list of recognized text strings
        """
        if frame is None:
            return []

        # 如果还在初始化中，等待一下
        if self._initializing and not self._initialized:
            LOG(f"[PaddleOCR] OCR引擎正在初始化中，等待完成...")
            if not self.wait_for_initialization(timeout=10.0):
                LOG_ERROR("[PaddleOCR] OCR引擎初始化超时或失败")
                return []

        if not self.is_ready():
            LOG_ERROR("[PaddleOCR] OCR引擎未就绪")
            return []

        try:
            # 性能优化：记录处理时间
            start_time = time.time()
            
            frame = self._ensure_writable(frame)
            with self._full_lock:
                # 使用新版本的predict方法
                result = self.ocr.predict(input=frame)
            
            processing_time = time.time() - start_time
            
            if not result or len(result) == 0:
                LOG(f"[PaddleOCR] 未识别到文本 (处理时间: {processing_time:.3f}s)")
                return []

            # 新版本PaddleOCR返回OCRResult对象列表
            ocr_result = result[0]  # 取第一个结果

            # 安全地提取文字内容
            extracted = []

            # 检查是否有识别到的文本
            if "rec_texts" in ocr_result and "rec_scores" in ocr_result:
                rec_texts = ocr_result["rec_texts"]
                rec_scores = ocr_result["rec_scores"]

                for i, (text, score) in enumerate(zip(rec_texts, rec_scores)):
                    try:
                        if score > 0.5:  # 只保留置信度>0.5的结果
                            extracted.append(text)
                    except (IndexError, TypeError) as e:
                        LOG_ERROR(f"[PaddleOCR] 解析OCR结果第{i}项时出错: {e}")
                        continue

            LOG_INFO(f"[PaddleOCR] 成功提取 {len(extracted)} 个文本 (处理时间: {processing_time:.3f}s): {extracted}")
            return extracted
        except Exception as e:
            LOG_ERROR(f"[PaddleOCR] OCR处理过程中出错: {e}")
            return []

    @staticmethod
    def _get_ocr_payload(result_item: Any) -> Dict[str, Any]:
        """兼容 PaddleOCR/PaddleX Result 顶层字段与官方文档里的 {'res': {...}} 结构。"""
        if not isinstance(result_item, dict):
            return {}
        payload = result_item.get("res")
        if isinstance(payload, dict):
            return payload
        return result_item

    @staticmethod
    def _ensure_writable(arr: Any) -> Any:
        """第三方推理库可能对输入做原地预处理,而 BorderFrameManager 的帧快照是
        **只读共享**的(多个消费者拿到同一个数组对象)。直接递进去要么报错,
        要么污染别人的检测输入 —— 按需复制一次。"""
        if isinstance(arr, np.ndarray) and not arr.flags.writeable:
            return arr.copy()
        return arr

    @staticmethod
    def _normalize_image(image: np.ndarray) -> Optional[np.ndarray]:
        """把 BGRA/灰度输入规范成 PaddleOCR 可直接处理的 BGR ndarray。"""
        if image is None or getattr(image, "size", 0) == 0:
            return None
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = np.repeat(arr[:, :, None], 3, axis=2)
        elif arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        elif arr.ndim != 3 or arr.shape[2] < 3:
            return None
        elif arr.shape[2] > 3:
            arr = arr[:, :, :3]
        return PaddleOCRManager._ensure_writable(np.ascontiguousarray(arr))

    @staticmethod
    def _box_to_rect(box: Any) -> Optional[Tuple[int, int, int, int]]:
        """将 rec_boxes/rec_polys 的一个元素转换为 x1,y1,x2,y2。"""
        if box is None:
            return None
        arr = np.asarray(box)
        if arr.size < 4:
            return None
        if arr.ndim == 1 and arr.size >= 4:
            x1, y1, x2, y2 = arr[:4]
        else:
            points = arr.reshape(-1, 2)
            x1, y1 = np.min(points[:, 0]), np.min(points[:, 1])
            x2, y2 = np.max(points[:, 0]), np.max(points[:, 1])
        return int(np.floor(x1)), int(np.floor(y1)), int(np.ceil(x2)), int(np.ceil(y2))

    def locate_number_box(self, frame: np.ndarray, rough_box: Tuple[int, int, int, int],
                          device: str = "cpu", min_score: float = 0.7,
                          padding: int = 3) -> Optional[Dict[str, Any]]:
        """在用户粗框内用完整 OCR 定位合法的 '当前/最大' 文本行并返回收紧后的绝对坐标。

        该方法只用于 GUI 配置阶段。运行时仍应使用 rec-only 读取收紧后的固定 ROI。
        """
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        h, w = frame.shape[:2]
        rx1, ry1, rx2, ry2 = rough_box
        rx1, rx2 = max(0, min(rx1, rx2)), min(w, max(rx1, rx2))
        ry1, ry2 = max(0, min(ry1, ry2)), min(h, max(ry1, ry2))
        if rx2 <= rx1 or ry2 <= ry1:
            return None

        if not self.ensure_full_ocr(device):
            return None

        roi = self._normalize_image(frame[ry1:ry2, rx1:rx2])
        if roi is None:
            return None
        try:
            start_time = time.time()
            with self._full_lock:
                result = self.ocr.predict(input=roi)
            elapsed_ms = (time.time() - start_time) * 1000
        except Exception as e:
            LOG_ERROR(f"[PaddleOCR] full OCR 定位异常: {e}")
            return None

        if not result:
            LOG("[PaddleOCR] full OCR 未检测到文本")
            return None

        payload = self._get_ocr_payload(result[0])
        texts = list(payload.get("rec_texts", []) or [])
        scores = list(payload.get("rec_scores", []) or [])
        boxes = payload.get("rec_boxes", None)
        polys = payload.get("rec_polys", None)

        rough_cx = (rx2 - rx1) / 2.0
        rough_cy = (ry2 - ry1) / 2.0
        candidates = []
        for i, text in enumerate(texts):
            score = float(scores[i]) if i < len(scores) and scores[i] is not None else 1.0
            if score < min_score:
                continue
            cur, mx = self.parse_number_text(str(text))
            if cur is None or mx is None:
                continue

            rect = None
            if boxes is not None and len(boxes) > i:
                rect = self._box_to_rect(boxes[i])
            if rect is None and polys is not None and len(polys) > i:
                rect = self._box_to_rect(polys[i])
            if rect is None:
                continue

            bx1, by1, bx2, by2 = rect
            cx = (bx1 + bx2) / 2.0
            cy = (by1 + by2) / 2.0
            distance = (cx - rough_cx) ** 2 + (cy - rough_cy) ** 2
            candidates.append((distance, i, str(text), score, cur, mx, rect))

        if not candidates:
            LOG("[PaddleOCR] full OCR 未找到合法的 当前/最大 文本")
            return None

        _, index, text, score, cur, mx, rect = min(candidates, key=lambda item: item[0])
        bx1, by1, bx2, by2 = rect
        pad = max(0, int(padding))
        abs_x1 = max(0, rx1 + bx1 - pad)
        abs_y1 = max(0, ry1 + by1 - pad)
        abs_x2 = min(w, rx1 + bx2 + pad)
        abs_y2 = min(h, ry1 + by2 + pad)
        if abs_x2 <= abs_x1 or abs_y2 <= abs_y1:
            return None

        pct = (cur / mx) * 100.0
        LOG_INFO(
            f"[PaddleOCR] OCR框自动收紧: text='{text}', score={score:.3f}, "
            f"pct={pct:.1f}%, box=({abs_x1},{abs_y1},{abs_x2},{abs_y2}), {elapsed_ms:.1f}ms"
        )
        return {
            "box": (abs_x1, abs_y1, abs_x2, abs_y2),
            "text": text,
            "score": score,
            "current": cur,
            "maximum": mx,
            "percentage": pct,
            "elapsed_ms": elapsed_ms,
            "candidate_index": index,
        }

    # ---- rec-only 资源数字识别(HP/MP 文本检测专用,与全流程 self.ocr 互不影响) ----

    def _get_rec_model(self, model_name: str = "PP-OCRv6_small_rec", device: str = "cpu"):
        """取(必要时构建)指定 (model_name, device) 的 rec-only 模型,失败返回 None。

        调用方必须**持有返回的对象**去 predict,不要回头读 self.rec_model ——
        期间另一线程可能已经换了那个字段(旧实现就是在 ensure→predict 之间放锁,
        另一线程构建失败把 rec_model 置 None,调度线程随后 AttributeError,
        资源检测被兜底成"充足"而不喝药)。
        """
        key = (model_name, device)
        with self._rec_lock:
            model = self._rec_models.get(key)
        if model is not None:
            return model

        try:
            from paddleocr import TextRecognition
            # 锁外构建:冷启动可达数秒,进锁会把调度线程的 HP/MP 检测一并堵住。
            # 并发构建同一 key 最多浪费一次构建(setdefault 保留先到者),不会出错。
            built = TextRecognition(model_name=model_name, device=device)
        except Exception as e:
            LOG_ERROR(f"[PaddleOCR] rec-only 模型初始化失败({model_name}@{device}): {e}")
            return None

        with self._rec_lock:
            model = self._rec_models.setdefault(key, built)
            self.rec_model = model          # 兼容旧字段:最近一次使用的模型
            self._rec_model_name = model_name
            self._rec_device = device
        LOG_INFO(f"[PaddleOCR] rec-only 模型已就绪: {model_name} @ {device}")
        return model

    def ensure_rec_model(self, model_name: str = "PP-OCRv6_small_rec", device: str = "cpu") -> bool:
        """惰性初始化 rec-only 文本识别模型(PP-OCRv6)。保留给外部做"预热"用。"""
        return self._get_rec_model(model_name, device) is not None

    @staticmethod
    def parse_number_text(text: str) -> Tuple[Optional[int], Optional[int]]:
        """解析游戏文本 '当前/最大'(如 '1,269/1,269')→ (current, max)。
        规则: 按 '/'(或被误识别成 '|')切分,两侧只取数字(去千位逗号);
        仅当 max>0 且 current<=max 才算有效,否则返回 (None, None)。"""
        if not text:
            return None, None
        # 按第一个分隔符('/' 或被误识别成 '|')切成左右两段，各自只取数字(去千位逗号/杂字符)
        sep_pos = -1
        for i, ch in enumerate(text):
            if ch in "/|":
                sep_pos = i
                break
        if sep_pos < 0:
            return None, None
        left = re.sub(r"\D", "", text[:sep_pos])
        right = re.sub(r"\D", "", text[sep_pos + 1:])
        if not left or not right:
            return None, None
        try:
            cur, mx = int(left), int(right)
        except ValueError:
            return None, None
        # 当前>最大 或 最大<=0 → 一定是无效识别
        if mx <= 0 or cur > mx:
            return None, None
        return cur, mx

    def recognize_and_parse(self, roi: np.ndarray, model_name: str = "PP-OCRv6_small_rec",
                            device: str = "cpu", min_score: float = 0.5
                            ) -> Tuple[Optional[int], Optional[int], Optional[float]]:
        """对已裁剪好的数字 ROI 做 rec-only 识别并解析为 (current, max, percentage)。
        识别置信度 rec_score < min_score 的结果会被丢弃(防止低置信误读成合法的'数字/数字')。
        任何失败/无效/低置信一律返回 (None, None, None),由上层兜底为'不触发'。"""
        if roi is None or getattr(roi, "size", 0) == 0:
            return None, None, None
        roi = self._normalize_image(roi)
        if roi is None:
            return None, None, None
        model = self._get_rec_model(model_name, device)
        if model is None:
            return None, None, None
        try:
            # 用局部引用 predict:字段可能被并发调用改掉,见 _get_rec_model 说明
            with self._rec_lock:
                out = model.predict(roi)
            text = ""
            score = None
            if out:
                r0 = out[0]
                if isinstance(r0, dict):
                    text = r0.get("rec_text", "")
                    sc = r0.get("rec_score", None)
                    score = float(sc) if sc is not None else None
                else:
                    text = str(r0)
            # 置信度门槛:rec_score 存在且低于阈值 → 不采信(官方返回 rec_text/rec_score)
            if score is not None and score < min_score:
                LOG(f"[PaddleOCR] 置信度过低丢弃: '{text}' score={score:.3f} < {min_score}")
                return None, None, None
            cur, mx = self.parse_number_text(text)
            if cur is None or mx is None:
                return None, None, None
            return cur, mx, (cur / mx) * 100.0
        except Exception as e:
            LOG_ERROR(f"[PaddleOCR] rec-only 识别异常: {e}")
            return None, None, None


def get_paddle_ocr_manager() -> PaddleOCRManager:
    """获取全局OCR管理器实例。

    PaddleOCRManager.__new__ 本身是双检锁单例(GUI 线程与调度线程并发
    首次获取也只会产生一个完成初始化的实例),无需再维护模块级缓存。
    """
    return PaddleOCRManager()
