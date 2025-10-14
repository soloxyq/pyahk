#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全面性能基准测试 - 测试所有frames图片的识别准确率和性能"""

import cv2
import time
import os
import sys
from pathlib import Path
from collections import defaultdict
import json

# 设置UTF-8编码（Windows兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试配置
FRAMES_DIR = "deepai/data/processed/frames"
TEMPLATE_IMAGE = "debug_frame_0002.png"  # 作为模板的图片（假设满血满蓝）
HP_COORDS = (97, 814, 228, 835)
MP_COORDS = (1767, 814, 1894, 835)

# 矩形检测区域（HP/MP条的颜色区域）
RECT_REGIONS = {
    "hp": (113, 896, 124, 1051),
    "mp": (1787, 894, 1800, 1053)
}

# HSV容差配置
HSV_TOLERANCE = {
    "h_tolerance": 10,
    "s_tolerance": 20,
    "v_tolerance": 20
}

# 结果统计
results = {
    "template": {"hp": [], "mp": [], "times": [], "errors": []},
    "keras": {"hp": [], "mp": [], "times": [], "errors": []},
    "tesseract": {"hp": [], "mp": [], "times": [], "errors": []},
    "rectangle": {"hp": [], "mp": [], "times": [], "errors": []},
}

# 一致性检查（四个方法结果对比）
consistency_check = []

# 全局模板缓存
template_cache = {}


def load_template(template_path):
    """加载模板图片并提取HP/MP区域的HSV数据"""
    print(f"\n{'='*80}")
    print(f"加载模板图片: {template_path}")
    print(f"{'='*80}")
    
    img = cv2.imread(template_path)
    if img is None:
        print(f"错误: 无法加载模板图片 {template_path}")
        return False
    
    print(f"模板图片尺寸: {img.shape[1]}x{img.shape[0]}")
    
    # 提取HP区域
    hp_x1, hp_y1, hp_x2, hp_y2 = RECT_REGIONS["hp"]
    hp_region = img[hp_y1:hp_y2, hp_x1:hp_x2]
    
    if hp_region.shape[2] == 4:  # BGRA
        hp_region = cv2.cvtColor(hp_region, cv2.COLOR_BGRA2BGR)
    hp_hsv = cv2.cvtColor(hp_region, cv2.COLOR_BGR2HSV)
    
    template_cache["hp"] = {
        "image": hp_hsv.copy(),
        "width": hp_x2 - hp_x1,
        "height": hp_y2 - hp_y1
    }
    print(f"HP模板区域: ({hp_x1}, {hp_y1}) -> ({hp_x2}, {hp_y2}), 尺寸: {hp_hsv.shape[1]}x{hp_hsv.shape[0]}")
    
    # 提取MP区域
    mp_x1, mp_y1, mp_x2, mp_y2 = RECT_REGIONS["mp"]
    mp_region = img[mp_y1:mp_y2, mp_x1:mp_x2]
    
    if mp_region.shape[2] == 4:  # BGRA
        mp_region = cv2.cvtColor(mp_region, cv2.COLOR_BGRA2BGR)
    mp_hsv = cv2.cvtColor(mp_region, cv2.COLOR_BGR2HSV)
    
    template_cache["mp"] = {
        "image": mp_hsv.copy(),
        "width": mp_x2 - mp_x1,
        "height": mp_y2 - mp_y1
    }
    print(f"MP模板区域: ({mp_x1}, {mp_y1}) -> ({mp_x2}, {mp_y2}), 尺寸: {mp_hsv.shape[1]}x{mp_hsv.shape[0]}")
    
    return True


def create_enhanced_color_mask(hsv_region, template_hsv, resource_type, h_tolerance, s_tolerance, v_tolerance):
    """创建增强的颜色掩码，支持红色双区间处理"""
    import numpy as np
    
    # 对于HP资源，使用红色双区间处理
    if resource_type == 'hp':
        template_h = template_hsv[:, :, 0]
        region_h = hsv_region[:, :, 0]
        
        h_match1 = np.abs(region_h.astype(np.int16) - template_h.astype(np.int16)) <= h_tolerance
        h_diff = np.abs(region_h.astype(np.int16) - template_h.astype(np.int16))
        h_diff_wrap = np.minimum(h_diff, 180 - h_diff)
        h_match2 = h_diff_wrap <= h_tolerance
        
        h_match = h_match1 | h_match2
    else:
        h_diff = np.abs(hsv_region[:, :, 0].astype(np.int16) - template_hsv[:, :, 0].astype(np.int16))
        h_diff = np.minimum(h_diff, 180 - h_diff)
        h_match = h_diff <= h_tolerance
    
    s_diff = np.abs(hsv_region[:, :, 1].astype(np.int16) - template_hsv[:, :, 1].astype(np.int16))
    v_diff = np.abs(hsv_region[:, :, 2].astype(np.int16) - template_hsv[:, :, 2].astype(np.int16))
    
    s_match = s_diff <= s_tolerance
    v_match = v_diff <= v_tolerance
    
    return h_match & s_match & v_match


def get_rectangle_percentage(img, resource_type):
    """使用矩形检测获取百分比（模拟真实程序的实现）"""
    import numpy as np
    
    try:
        template_data = template_cache.get(resource_type)
        if template_data is None:
            return None
        
        template_hsv = template_data["image"]
        t_width = template_data["width"]
        t_height = template_data["height"]
        
        x1, y1, x2, y2 = RECT_REGIONS[resource_type]
        region = img[y1:y2, x1:x2]
        
        if region.shape[2] == 4:
            region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
        hsv_region = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        
        if hsv_region.shape != template_hsv.shape:
            hsv_region = cv2.resize(hsv_region, (template_hsv.shape[1], template_hsv.shape[0]))
        
        h_tolerance = HSV_TOLERANCE["h_tolerance"]
        s_tolerance = HSV_TOLERANCE["s_tolerance"]
        v_tolerance = HSV_TOLERANCE["v_tolerance"]
        
        pixel_match = create_enhanced_color_mask(
            hsv_region, template_hsv, resource_type, h_tolerance, s_tolerance, v_tolerance
        )
        
        vertical_sum = np.sum(pixel_match, axis=1)
        row_threshold = t_width * 0.6
        is_filled = vertical_sum > row_threshold
        
        max_len = 0
        current_len = 0
        for i in range(t_height-1, -1, -1):
            if is_filled[i]:
                current_len += 1
                if current_len > max_len:
                    max_len = current_len
            else:
                current_len = 0
        
        percentage = (max_len / t_height) * 100.0 if t_height > 0 else 0.0
        return percentage
    
    except Exception as e:
        return None


def test_ocr_engines(img, hp_roi, mp_roi, retry=False):
    """测试所有OCR引擎，返回结果字典"""
    results = {
        "template": {"hp": None, "mp": None, "time": 0},
        "keras": {"hp": None, "mp": None, "time": 0},
        "tesseract": {"hp": None, "mp": None, "time": 0},
    }
    
    retry_label = " (重试)" if retry else ""
    
    # 测试Template引擎
    print(f"\n--- Template 引擎{retry_label} ---")
    try:
        from deepai import get_recognizer
        recognizer = get_recognizer("template")
        
        start = time.time()
        hp_current, hp_max = recognizer.recognize_and_parse(hp_roi)
        mp_current, mp_max = recognizer.recognize_and_parse(mp_roi)
        elapsed = (time.time() - start) * 1000
        
        if hp_current is not None and hp_max is not None:
            hp_result = f"{hp_current}/{hp_max}"
            print(f"  HP: {hp_result}")
            results["template"]["hp"] = hp_result
        else:
            print(f"  HP: 识别失败")
        
        if mp_current is not None and mp_max is not None:
            mp_result = f"{mp_current}/{mp_max}"
            print(f"  MP: {mp_result}")
            results["template"]["mp"] = mp_result
        else:
            print(f"  MP: 识别失败")
        
        print(f"  耗时: {elapsed:.1f}ms")
        results["template"]["time"] = elapsed
        
    except Exception as e:
        print(f"  ❌ 错误: {e}")
    
    # 测试Keras引擎
    print(f"\n--- Keras 引擎{retry_label} ---")
    try:
        from deepai import get_recognizer
        recognizer = get_recognizer("keras")
        
        start = time.time()
        hp_current, hp_max = recognizer.recognize_and_parse(hp_roi)
        mp_current, mp_max = recognizer.recognize_and_parse(mp_roi)
        elapsed = (time.time() - start) * 1000
        
        if hp_current is not None and hp_max is not None:
            hp_result = f"{hp_current}/{hp_max}"
            print(f"  HP: {hp_result}")
            results["keras"]["hp"] = hp_result
        else:
            print(f"  HP: 识别失败")
        
        if mp_current is not None and mp_max is not None:
            mp_result = f"{mp_current}/{mp_max}"
            print(f"  MP: {mp_result}")
            results["keras"]["mp"] = mp_result
        else:
            print(f"  MP: 识别失败")
        
        print(f"  耗时: {elapsed:.1f}ms")
        results["keras"]["time"] = elapsed
        
    except Exception as e:
        print(f"  ❌ 错误: {e}")
    
    # 测试Tesseract引擎
    print(f"\n--- Tesseract 引擎{retry_label} ---")
    try:
        from torchlight_assistant.utils.tesseract_ocr_manager import get_tesseract_ocr_manager
        ocr_manager = get_tesseract_ocr_manager({})
        
        start = time.time()
        hp_text, hp_pct = ocr_manager.recognize_and_parse(img, HP_COORDS, debug=False)
        mp_text, mp_pct = ocr_manager.recognize_and_parse(img, MP_COORDS, debug=False)
        elapsed = (time.time() - start) * 1000
        
        if hp_text and hp_pct >= 0:
            print(f"  HP: {hp_text}")
            results["tesseract"]["hp"] = hp_text
        else:
            print(f"  HP: 识别失败")
        
        if mp_text and mp_pct >= 0:
            print(f"  MP: {mp_text}")
            results["tesseract"]["mp"] = mp_text
        else:
            print(f"  MP: 识别失败")
        
        print(f"  耗时: {elapsed:.1f}ms")
        results["tesseract"]["time"] = elapsed
        
    except Exception as e:
        print(f"  ❌ 错误: {e}")
    
    return results


def check_ocr_consistency(hp_results, mp_results):
    """检查OCR结果是否一致"""
    # 检查HP一致性
    hp_values = [r for r in hp_results if r is not None]
    hp_consistent = len(set(hp_values)) <= 1 if len(hp_values) >= 2 else True
    
    # 检查MP一致性
    mp_values = [r for r in mp_results if r is not None]
    mp_consistent = len(set(mp_values)) <= 1 if len(mp_values) >= 2 else True
    
    return hp_consistent, mp_consistent


def test_frame(frame_path, frame_name):
    """测试单个frame"""
    print(f"\n{'='*60}")
    print(f"测试: {frame_name}")
    print(f"{'='*60}")
    
    img = cv2.imread(frame_path)
    if img is None:
        print(f"❌ 无法读取图片")
        return
    
    # 提取ROI
    x1, y1, x2, y2 = HP_COORDS
    hp_roi = img[y1:y2, x1:x2]
    
    x1, y1, x2, y2 = MP_COORDS
    mp_roi = img[y1:y2, x1:x2]
    
    # 第一次测试
    ocr_results = test_ocr_engines(img, hp_roi, mp_roi, retry=False)
    
    # 检查一致性
    hp_results = [
        ocr_results["template"]["hp"],
        ocr_results["keras"]["hp"],
        ocr_results["tesseract"]["hp"]
    ]
    mp_results = [
        ocr_results["template"]["mp"],
        ocr_results["keras"]["mp"],
        ocr_results["tesseract"]["mp"]
    ]
    
    hp_consistent, mp_consistent = check_ocr_consistency(hp_results, mp_results)
    
    # 如果不一致，重试一次
    if not hp_consistent or not mp_consistent:
        print(f"\n⚠️  OCR结果不一致，重试一次...")
        if not hp_consistent:
            print(f"   HP不一致: {[r for r in hp_results if r is not None]}")
        if not mp_consistent:
            print(f"   MP不一致: {[r for r in mp_results if r is not None]}")
        
        ocr_results_retry = test_ocr_engines(img, hp_roi, mp_roi, retry=True)
        
        # 检查重试后的一致性
        hp_results_retry = [
            ocr_results_retry["template"]["hp"],
            ocr_results_retry["keras"]["hp"],
            ocr_results_retry["tesseract"]["hp"]
        ]
        mp_results_retry = [
            ocr_results_retry["template"]["mp"],
            ocr_results_retry["keras"]["mp"],
            ocr_results_retry["tesseract"]["mp"]
        ]
        
        hp_consistent_retry, mp_consistent_retry = check_ocr_consistency(hp_results_retry, mp_results_retry)
        
        # 如果重试后一致，使用重试结果
        if hp_consistent_retry and not hp_consistent:
            print(f"   ✅ HP重试后一致: {[r for r in hp_results_retry if r is not None][0]}")
            for engine in ["template", "keras", "tesseract"]:
                ocr_results[engine]["hp"] = ocr_results_retry[engine]["hp"]
        
        if mp_consistent_retry and not mp_consistent:
            print(f"   ✅ MP重试后一致: {[r for r in mp_results_retry if r is not None][0]}")
            for engine in ["template", "keras", "tesseract"]:
                ocr_results[engine]["mp"] = ocr_results_retry[engine]["mp"]
        
        # 如果重试后仍不一致，标记
        if not hp_consistent_retry and not hp_consistent:
            print(f"   ❌ HP重试后仍不一致")
        if not mp_consistent_retry and not mp_consistent:
            print(f"   ❌ MP重试后仍不一致")
    
    # 构建frame结果
    frame_results = {
        "frame": frame_name,
        "template": ocr_results["template"],
        "keras": ocr_results["keras"],
        "tesseract": ocr_results["tesseract"],
        "rectangle": {"hp": None, "mp": None, "time": 0},
    }
    
    # 记录到全局结果
    for engine in ["template", "keras", "tesseract"]:
        if ocr_results[engine]["hp"]:
            results[engine]["hp"].append(ocr_results[engine]["hp"])
        else:
            results[engine]["errors"].append(f"{frame_name} - HP")
        
        if ocr_results[engine]["mp"]:
            results[engine]["mp"].append(ocr_results[engine]["mp"])
        else:
            results[engine]["errors"].append(f"{frame_name} - MP")
        
        results[engine]["times"].append(ocr_results[engine]["time"])
    

    
    # 测试矩形检测
    print("\n--- 矩形检测 ---")
    try:
        start = time.time()
        hp_pct = get_rectangle_percentage(img, "hp")
        mp_pct = get_rectangle_percentage(img, "mp")
        elapsed = (time.time() - start) * 1000
        
        if hp_pct is not None:
            hp_result = f"{hp_pct:.1f}%"
            print(f"  HP: {hp_result}")
            results["rectangle"]["hp"].append(hp_result)
            frame_results["rectangle"]["hp"] = hp_result
        else:
            print(f"  HP: 检测失败")
            results["rectangle"]["errors"].append(f"{frame_name} - HP")
        
        if mp_pct is not None:
            mp_result = f"{mp_pct:.1f}%"
            print(f"  MP: {mp_result}")
            results["rectangle"]["mp"].append(mp_result)
            frame_results["rectangle"]["mp"] = mp_result
        else:
            print(f"  MP: 检测失败")
            results["rectangle"]["errors"].append(f"{frame_name} - MP")
        
        print(f"  耗时: {elapsed:.2f}ms")
        results["rectangle"]["times"].append(elapsed)
        frame_results["rectangle"]["time"] = elapsed
        
    except Exception as e:
        print(f"  ❌ 错误: {e}")
        results["rectangle"]["errors"].append(f"{frame_name} - Exception: {e}")
    
    # 一致性检查
    consistency_check.append(frame_results)
    
    # 计算OCR共识值和矩形检测误差
    hp_template = frame_results["template"]["hp"]
    hp_keras = frame_results["keras"]["hp"]
    hp_tesseract = frame_results["tesseract"]["hp"]
    hp_rectangle = frame_results["rectangle"]["hp"]
    
    mp_template = frame_results["template"]["mp"]
    mp_keras = frame_results["keras"]["mp"]
    mp_tesseract = frame_results["tesseract"]["mp"]
    mp_rectangle = frame_results["rectangle"]["mp"]
    
    # 计算HP误差
    hp_ocr_pct = get_consensus_ocr(hp_template, hp_keras, hp_tesseract)
    hp_rect_pct = parse_rectangle_result(hp_rectangle)
    
    if hp_ocr_pct is not None and hp_rect_pct is not None:
        hp_diff = abs(hp_rect_pct - hp_ocr_pct)
        frame_results["hp_ocr_pct"] = hp_ocr_pct
        frame_results["hp_rect_pct"] = hp_rect_pct
        frame_results["hp_diff"] = hp_diff
        print(f"\nHP: OCR={hp_ocr_pct:.1f}%, 矩形={hp_rect_pct:.1f}%, 误差={hp_diff:.1f}%")
    else:
        frame_results["hp_ocr_pct"] = None
        frame_results["hp_rect_pct"] = None
        frame_results["hp_diff"] = None
        print(f"\nHP: 无法计算误差")
    
    # 计算MP误差
    mp_ocr_pct = get_consensus_ocr(mp_template, mp_keras, mp_tesseract)
    mp_rect_pct = parse_rectangle_result(mp_rectangle)
    
    if mp_ocr_pct is not None and mp_rect_pct is not None:
        mp_diff = abs(mp_rect_pct - mp_ocr_pct)
        frame_results["mp_ocr_pct"] = mp_ocr_pct
        frame_results["mp_rect_pct"] = mp_rect_pct
        frame_results["mp_diff"] = mp_diff
        print(f"MP: OCR={mp_ocr_pct:.1f}%, 矩形={mp_rect_pct:.1f}%, 误差={mp_diff:.1f}%")
    else:
        frame_results["mp_ocr_pct"] = None
        frame_results["mp_rect_pct"] = None
        frame_results["mp_diff"] = None
        print(f"MP: 无法计算误差")


def parse_ocr_result(ocr_str):
    """解析OCR结果字符串，返回百分比"""
    if not ocr_str:
        return None
    try:
        parts = ocr_str.split('/')
        if len(parts) == 2:
            current = float(parts[0])
            maximum = float(parts[1])
            if maximum > 0:
                return (current / maximum) * 100.0
    except:
        pass
    return None


def parse_rectangle_result(rect_str):
    """解析矩形检测结果字符串，返回百分比"""
    if not rect_str:
        return None
    try:
        return float(rect_str.rstrip('%'))
    except:
        pass
    return None


def get_consensus_ocr(template, keras, tesseract):
    """获取OCR共识值（优先使用一致的结果）"""
    results = []
    
    template_pct = parse_ocr_result(template)
    keras_pct = parse_ocr_result(keras)
    tesseract_pct = parse_ocr_result(tesseract)
    
    if template_pct is not None:
        results.append(template_pct)
    if keras_pct is not None:
        results.append(keras_pct)
    if tesseract_pct is not None:
        results.append(tesseract_pct)
    
    if not results:
        return None
    
    # 如果所有结果都接近（误差<5%），返回平均值
    if len(results) >= 2:
        max_diff = max(results) - min(results)
        if max_diff < 5:
            return sum(results) / len(results)
    
    # 否则返回中位数（更稳健）
    results.sort()
    mid = len(results) // 2
    if len(results) % 2 == 0:
        return (results[mid-1] + results[mid]) / 2
    else:
        return results[mid]


def print_summary():
    """打印测试总结"""
    print("\n" + "="*80)
    print("测试总结")
    print("="*80)
    
    for engine in ["template", "keras", "tesseract", "rectangle"]:
        print(f"\n{'='*80}")
        print(f"{engine.upper()} 引擎")
        print(f"{'='*80}")
        
        data = results[engine]
        
        # 成功率
        total_tests = len(consistency_check) * 2  # HP + MP
        successful_tests = len(data["hp"]) + len(data["mp"])
        success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
        
        print(f"总测试数: {total_tests} (HP: {len(consistency_check)}, MP: {len(consistency_check)})")
        print(f"成功识别: {successful_tests}")
        print(f"识别失败: {len(data['errors'])}")
        print(f"成功率: {success_rate:.1f}%")
        
        # 性能统计
        if data["times"]:
            avg_time = sum(data["times"]) / len(data["times"])
            min_time = min(data["times"])
            max_time = max(data["times"])
            print(f"\n性能统计:")
            print(f"  平均耗时: {avg_time:.1f}ms")
            print(f"  最快: {min_time:.1f}ms")
            print(f"  最慢: {max_time:.1f}ms")
        
        # 错误列表
        if data["errors"]:
            print(f"\n错误列表:")
            for error in data["errors"][:5]:  # 只显示前5个
                print(f"  - {error}")
            if len(data["errors"]) > 5:
                print(f"  ... 还有 {len(data['errors']) - 5} 个错误")
    
    # 矩形检测准确性分析（从已计算的误差中提取）
    print(f"\n{'='*80}")
    print("矩形检测准确性分析（与OCR基准值对比）")
    print(f"{'='*80}")
    
    hp_diffs = []
    mp_diffs = []
    hp_cases = []
    mp_cases = []
    hp_diffs_valid = []  # 只包含OCR合理的案例
    mp_diffs_valid = []
    hp_ocr_errors = []  # OCR错误案例
    mp_ocr_errors = []
    
    for frame_result in consistency_check:
        frame = frame_result['frame']
        
        # HP分析（使用已计算的值）
        if frame_result.get('hp_diff') is not None:
            hp_ocr_pct = frame_result['hp_ocr_pct']
            hp_rect_pct = frame_result['hp_rect_pct']
            hp_diff = frame_result['hp_diff']
            
            hp_diffs.append(hp_diff)
            hp_cases.append({
                'frame': frame,
                'ocr': hp_ocr_pct,
                'rect': hp_rect_pct,
                'diff': hp_diff
            })
            
            # 判断OCR是否合理（0-100%范围内）
            if 0 <= hp_ocr_pct <= 100:
                hp_diffs_valid.append(hp_diff)
            else:
                hp_ocr_errors.append({
                    'frame': frame,
                    'ocr': hp_ocr_pct,
                    'rect': hp_rect_pct
                })
        
        # MP分析（使用已计算的值）
        if frame_result.get('mp_diff') is not None:
            mp_ocr_pct = frame_result['mp_ocr_pct']
            mp_rect_pct = frame_result['mp_rect_pct']
            mp_diff = frame_result['mp_diff']
            
            mp_diffs.append(mp_diff)
            mp_cases.append({
                'frame': frame,
                'ocr': mp_ocr_pct,
                'rect': mp_rect_pct,
                'diff': mp_diff
            })
            
            # 判断OCR是否合理（0-100%范围内）
            if 0 <= mp_ocr_pct <= 100:
                mp_diffs_valid.append(mp_diff)
            else:
                mp_ocr_errors.append({
                    'frame': frame,
                    'ocr': mp_ocr_pct,
                    'rect': mp_rect_pct
                })
    
    # HP统计
    print(f"\nHP检测准确性:")
    print(f"  总测试数: {len(hp_diffs)}")
    print(f"  OCR合理案例: {len(hp_diffs_valid)} (OCR在0-100%范围内)")
    print(f"  OCR错误案例: {len(hp_ocr_errors)} (OCR超出0-100%范围)")
    
    if hp_diffs_valid:
        print(f"\n  【仅统计OCR合理的案例】")
        print(f"  平均误差: {sum(hp_diffs_valid)/len(hp_diffs_valid):.2f}%")
        print(f"  最大误差: {max(hp_diffs_valid):.2f}%")
        print(f"  最小误差: {min(hp_diffs_valid):.2f}%")
        print(f"  误差<5%的比例: {sum(1 for d in hp_diffs_valid if d < 5)/len(hp_diffs_valid)*100:.1f}%")
        print(f"  误差<10%的比例: {sum(1 for d in hp_diffs_valid if d < 10)/len(hp_diffs_valid)*100:.1f}%")
        print(f"  误差<15%的比例: {sum(1 for d in hp_diffs_valid if d < 15)/len(hp_diffs_valid)*100:.1f}%")
    
    if hp_ocr_errors:
        print(f"\n  【OCR错误案例】（这些案例中矩形检测更可靠）:")
        for i, case in enumerate(hp_ocr_errors[:5], 1):
            print(f"    {i}. {case['frame']}: OCR={case['ocr']:.1f}% (错误), 矩形={case['rect']:.1f}% (合理)")
    
    if hp_diffs_valid:
        # 显示最差的5个合理案例
        hp_cases_valid = [c for c in hp_cases if 0 <= c['ocr'] <= 100]
        hp_cases_valid.sort(key=lambda x: x['diff'], reverse=True)
        print(f"\n  【OCR合理案例中】最差的5个:")
        print(f"  {'Frame':<20} {'OCR%':<10} {'矩形%':<10} {'误差%':<10}")
        print(f"  {'-'*50}")
        for case in hp_cases_valid[:5]:
            print(f"  {case['frame']:<20} {case['ocr']:>8.1f}% {case['rect']:>9.1f}% {case['diff']:>9.1f}%")
    
    # MP统计
    print(f"\nMP检测准确性:")
    print(f"  总测试数: {len(mp_diffs)}")
    print(f"  OCR合理案例: {len(mp_diffs_valid)} (OCR在0-100%范围内)")
    print(f"  OCR错误案例: {len(mp_ocr_errors)} (OCR超出0-100%范围)")
    
    if mp_diffs_valid:
        print(f"\n  【仅统计OCR合理的案例】")
        print(f"  平均误差: {sum(mp_diffs_valid)/len(mp_diffs_valid):.2f}%")
        print(f"  最大误差: {max(mp_diffs_valid):.2f}%")
        print(f"  最小误差: {min(mp_diffs_valid):.2f}%")
        print(f"  误差<5%的比例: {sum(1 for d in mp_diffs_valid if d < 5)/len(mp_diffs_valid)*100:.1f}%")
        print(f"  误差<10%的比例: {sum(1 for d in mp_diffs_valid if d < 10)/len(mp_diffs_valid)*100:.1f}%")
        print(f"  误差<15%的比例: {sum(1 for d in mp_diffs_valid if d < 15)/len(mp_diffs_valid)*100:.1f}%")
    
    if mp_ocr_errors:
        print(f"\n  【OCR错误案例】（这些案例中矩形检测更可靠）:")
        for i, case in enumerate(mp_ocr_errors[:5], 1):
            print(f"    {i}. {case['frame']}: OCR={case['ocr']:.1f}% (错误), 矩形={case['rect']:.1f}% (合理)")
    
    if mp_diffs_valid:
        # 显示最差的5个合理案例
        mp_cases_valid = [c for c in mp_cases if 0 <= c['ocr'] <= 100]
        mp_cases_valid.sort(key=lambda x: x['diff'], reverse=True)
        print(f"\n  【OCR合理案例中】最差的5个:")
        print(f"  {'Frame':<20} {'OCR%':<10} {'矩形%':<10} {'误差%':<10}")
        print(f"  {'-'*50}")
        for case in mp_cases_valid[:5]:
            print(f"  {case['frame']:<20} {case['ocr']:>8.1f}% {case['rect']:>9.1f}% {case['diff']:>9.1f}%")
    
    # 综合评估
    all_diffs_valid = hp_diffs_valid + mp_diffs_valid
    all_ocr_errors = hp_ocr_errors + mp_ocr_errors
    
    print(f"\n综合评估:")
    print(f"  总测试数: {len(hp_diffs) + len(mp_diffs)}")
    print(f"  OCR合理案例: {len(all_diffs_valid)}")
    print(f"  OCR错误案例: {len(all_ocr_errors)}")
    
    if all_diffs_valid:
        print(f"\n  【仅统计OCR合理的案例】")
        print(f"  平均误差: {sum(all_diffs_valid)/len(all_diffs_valid):.2f}%")
        print(f"  最大误差: {max(all_diffs_valid):.2f}%")
        print(f"  误差<5%的比例: {sum(1 for d in all_diffs_valid if d < 5)/len(all_diffs_valid)*100:.1f}%")
        print(f"  误差<10%的比例: {sum(1 for d in all_diffs_valid if d < 10)/len(all_diffs_valid)*100:.1f}%")
    
    if all_ocr_errors:
        print(f"\n  ⚠️  发现 {len(all_ocr_errors)} 个OCR错误案例")
        print(f"      在这些案例中，矩形检测给出的是合理值（0-100%）")
        print(f"      而OCR识别出了错误的数字，导致百分比超出范围")
        print(f"      这说明：矩形检测在这些情况下比OCR更可靠！")
    
    # OCR一致性分析
    print(f"\n{'='*80}")
    print("OCR一致性分析")
    print(f"{'='*80}")
    
    hp_consistent = 0
    hp_inconsistent = 0
    mp_consistent = 0
    mp_inconsistent = 0
    
    for frame_result in consistency_check:
        # HP一致性
        hp_results = [
            frame_result["template"]["hp"],
            frame_result["keras"]["hp"],
            frame_result["tesseract"]["hp"]
        ]
        hp_results = [r for r in hp_results if r is not None]
        
        if len(hp_results) >= 2:
            if len(set(hp_results)) == 1:
                hp_consistent += 1
            else:
                hp_inconsistent += 1
        
        # MP一致性
        mp_results = [
            frame_result["template"]["mp"],
            frame_result["keras"]["mp"],
            frame_result["tesseract"]["mp"]
        ]
        mp_results = [r for r in mp_results if r is not None]
        
        if len(mp_results) >= 2:
            if len(set(mp_results)) == 1:
                mp_consistent += 1
            else:
                mp_inconsistent += 1
    
    print(f"HP一致: {hp_consistent}, 不一致: {hp_inconsistent}")
    print(f"MP一致: {mp_consistent}, 不一致: {mp_inconsistent}")
    
    if hp_inconsistent > 0 or mp_inconsistent > 0:
        print(f"\n发现不一致的识别结果，详细信息:")
        for frame_result in consistency_check:
            hp_results = [
                frame_result["template"]["hp"],
                frame_result["keras"]["hp"],
                frame_result["tesseract"]["hp"]
            ]
            hp_results = [r for r in hp_results if r is not None]
            
            if len(hp_results) >= 2 and len(set(hp_results)) > 1:
                print(f"\n  {frame_result['frame']} - HP不一致:")
                print(f"    Template: {frame_result['template']['hp']}")
                print(f"    Keras: {frame_result['keras']['hp']}")
                print(f"    Tesseract: {frame_result['tesseract']['hp']}")
            
            mp_results = [
                frame_result["template"]["mp"],
                frame_result["keras"]["mp"],
                frame_result["tesseract"]["mp"]
            ]
            mp_results = [r for r in mp_results if r is not None]
            
            if len(mp_results) >= 2 and len(set(mp_results)) > 1:
                print(f"\n  {frame_result['frame']} - MP不一致:")
                print(f"    Template: {frame_result['template']['mp']}")
                print(f"    Keras: {frame_result['keras']['mp']}")
                print(f"    Tesseract: {frame_result['tesseract']['mp']}")
    
    # 保存详细结果到JSON
    output_file = "performance_test/benchmark_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            "summary": {
                "total_frames": len(consistency_check),
                "engines": {
                    engine: {
                        "success_rate": (len(results[engine]["hp"]) + len(results[engine]["mp"])) / (len(consistency_check) * 2) * 100,
                        "avg_time": sum(results[engine]["times"]) / len(results[engine]["times"]) if results[engine]["times"] else 0,
                        "errors": len(results[engine]["errors"])
                    }
                    for engine in ["template", "keras", "tesseract", "rectangle"]
                },
                "rectangle_accuracy": {
                    "hp": {
                        "total_tests": len(hp_diffs),
                        "valid_tests": len(hp_diffs_valid),
                        "ocr_errors": len(hp_ocr_errors),
                        "avg_error": sum(hp_diffs_valid) / len(hp_diffs_valid) if hp_diffs_valid else 0,
                        "max_error": max(hp_diffs_valid) if hp_diffs_valid else 0,
                        "min_error": min(hp_diffs_valid) if hp_diffs_valid else 0,
                        "error_lt_5_pct": sum(1 for d in hp_diffs_valid if d < 5) / len(hp_diffs_valid) * 100 if hp_diffs_valid else 0,
                        "error_lt_10_pct": sum(1 for d in hp_diffs_valid if d < 10) / len(hp_diffs_valid) * 100 if hp_diffs_valid else 0
                    },
                    "mp": {
                        "total_tests": len(mp_diffs),
                        "valid_tests": len(mp_diffs_valid),
                        "ocr_errors": len(mp_ocr_errors),
                        "avg_error": sum(mp_diffs_valid) / len(mp_diffs_valid) if mp_diffs_valid else 0,
                        "max_error": max(mp_diffs_valid) if mp_diffs_valid else 0,
                        "min_error": min(mp_diffs_valid) if mp_diffs_valid else 0,
                        "error_lt_5_pct": sum(1 for d in mp_diffs_valid if d < 5) / len(mp_diffs_valid) * 100 if mp_diffs_valid else 0,
                        "error_lt_10_pct": sum(1 for d in mp_diffs_valid if d < 10) / len(mp_diffs_valid) * 100 if mp_diffs_valid else 0
                    },
                    "overall": {
                        "total_tests": len(hp_diffs) + len(mp_diffs),
                        "valid_tests": len(all_diffs_valid),
                        "ocr_errors": len(all_ocr_errors),
                        "avg_error": sum(all_diffs_valid) / len(all_diffs_valid) if all_diffs_valid else 0,
                        "max_error": max(all_diffs_valid) if all_diffs_valid else 0,
                        "error_lt_5_pct": sum(1 for d in all_diffs_valid if d < 5) / len(all_diffs_valid) * 100 if all_diffs_valid else 0,
                        "error_lt_10_pct": sum(1 for d in all_diffs_valid if d < 10) / len(all_diffs_valid) * 100 if all_diffs_valid else 0
                    }
                }
            },
            "detailed_results": consistency_check
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*80}")
    print(f"详细结果已保存到: {output_file}")
    print(f"{'='*80}")


def main():
    """主函数"""
    print("="*80)
    print("全面性能基准测试 - 包含矩形检测")
    print("="*80)
    print(f"测试目录: {FRAMES_DIR}")
    print(f"OCR HP坐标: {HP_COORDS}")
    print(f"OCR MP坐标: {MP_COORDS}")
    print(f"矩形 HP区域: {RECT_REGIONS['hp']}")
    print(f"矩形 MP区域: {RECT_REGIONS['mp']}")
    print("="*80)
    
    # 1. 加载模板
    if not load_template(TEMPLATE_IMAGE):
        print("错误: 无法加载模板图片")
        return
    
    # 获取所有frame文件
    frames_path = Path(FRAMES_DIR)
    if not frames_path.exists():
        print(f"❌ 目录不存在: {FRAMES_DIR}")
        return
    
    frame_files = sorted(frames_path.glob("frame_*.png"))
    
    if not frame_files:
        print(f"❌ 未找到frame文件")
        return
    
    print(f"\n找到 {len(frame_files)} 个frame文件")
    
    # 测试每个frame
    for i, frame_file in enumerate(frame_files, 1):
        print(f"\n进度: {i}/{len(frame_files)}")
        test_frame(str(frame_file), frame_file.name)
    
    # 打印总结
    print_summary()


if __name__ == "__main__":
    main()
