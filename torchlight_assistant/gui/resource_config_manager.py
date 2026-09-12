#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资源配置管理器 - 统一HP/MP配置构建和解析逻辑"""

from copy import deepcopy
from typing import Dict, Any, Optional, Tuple
from torchlight_assistant.utils.debug_log import LOG_INFO
from torchlight_assistant.utils.config_values import config_int
from torchlight_assistant.utils.key_names import normalize_key_name


class ResourceConfigManager:
    """资源配置管理器 - 提取配置逻辑，消除重复代码"""

    # 默认坐标配置
    DEFAULT_COORDS = {
        "hp": {
            "rectangle": (136, 910, 213, 1004),
            "circle": (174, 957, 47),
            "text_ocr": (97, 814, 218, 835),
        },
        "mp": {
            "rectangle": (1552, 910, 1560, 1004),
            "circle": (1746, 957, 47),
            "text_ocr": (1767, 814, 1894, 835),
        },
    }

    @staticmethod
    def build_resource_config(
        resource_type: str,
        widgets: Dict[str, Any],
        detection_mode: str,
        circle_config: Dict[str, Any],
        timing_manager=None,
        existing_config: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        统一的资源配置构建方法，消除HP/MP重复逻辑

        Args:
            resource_type: "hp" 或 "mp"
            widgets: 对应的UI控件字典
            detection_mode: 检测模式
            circle_config: 圆形配置缓存
            timing_manager: 时间管理器（用于获取冷却时间）
            existing_config: 当前已加载的该资源配置。**必须传**，否则 UI 没有暴露的字段
                （ocr_model / ocr_device 等）会在保存或 F8 同步时被静默抹掉。
        """
        # ⚠️ 从已加载的配置开始合并，而不是从空字典重建。
        # 这个函数只知道 UI 上有哪些控件；JSON 里还有一批**没有对应控件**的字段
        # (ocr_model / ocr_device 是文档化的用户设置，见 wiki/04)。从零重建的话，
        # 手工配好 GPU 或 medium 模型的用户，一按 F8 或点保存就被静默退回默认值，
        # 而且没有任何提示 —— 表现为"我明明配了 GPU，怎么还是 CPU 在跑"。
        # 合并是安全的:UI 拥有的字段在下面全部会被覆盖写一遍。
        config = deepcopy(existing_config) if isinstance(existing_config, dict) else {}
        config.update({
            "enabled": widgets["enabled"].isChecked(),
            "key": normalize_key_name(widgets["key"].text()),
            "threshold": widgets["threshold"].value(),
            "cooldown": ResourceConfigManager._get_cooldown_from_timing(
                resource_type, timing_manager
            ),
        })

        # 添加容差配置
        tolerance_h, tolerance_s, tolerance_v = (
            ResourceConfigManager._get_tolerance_from_widgets(resource_type, widgets)
        )
        config.update(
            {
                "tolerance_h": tolerance_h,
                "tolerance_s": tolerance_s,
                "tolerance_v": tolerance_v,
            }
        )

        # 根据检测模式添加相应配置
        if detection_mode == "text_ocr":
            ResourceConfigManager._add_text_ocr_config(config, resource_type, widgets)
        elif detection_mode == "circle":
            ResourceConfigManager._add_circle_config(
                config, resource_type, widgets, circle_config
            )
        elif detection_mode == "rectangle":
            ResourceConfigManager._add_rectangle_config(config, resource_type, widgets)
        else:
            # 未知值可能来自更新版本，也可能是手工配置错误。UI 不应把它
            # 悄悄改写成 rectangle 并启用一个不同的检测器；保留原值，由
            # ResourceManager 明确 fail-closed，等用户主动选择受支持模式。
            config["detection_mode"] = detection_mode

        # ``colors`` 是旧版未接入执行路径的死字段。当前算法以 F8 时锁定的
        # HSV 模板 + tolerance_* 为准；保存时显式迁移掉，避免界面/配置暗示
        # 多颜色列表会改变药剂判定。
        config.pop("colors", None)

        return config

    @staticmethod
    def _get_cooldown_from_timing(resource_type: str, timing_manager=None) -> int:
        """从时间管理器获取冷却时间"""
        if timing_manager and hasattr(timing_manager, "get_config"):
            try:
                timing_config = timing_manager.get_config()
                if resource_type == "hp":
                    return timing_config.get("hp_cooldown", 5000)
                elif resource_type == "mp":
                    return timing_config.get("mp_cooldown", 8000)
            except (AttributeError, KeyError) as e:
                LOG_INFO(f"[异常] 捕获到(AttributeError, KeyError): {e}")
        # 默认值
        return 5000 if resource_type == "hp" else 8000

    @staticmethod
    def _get_tolerance_from_widgets(
        resource_type: str, widgets: Dict[str, Any]
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """从UI控件获取容差设置"""
        tolerance_input = widgets.get("tolerance_input")
        if tolerance_input:
            try:
                tolerance_text = tolerance_input.text().strip()
                if tolerance_text:
                    values = [config_int(x.strip()) for x in tolerance_text.split(",")]
                    if len(values) == 3 and all(
                        0 <= value <= maximum
                        for value, maximum in zip(values, (179, 255, 255))
                    ):
                        return tuple(values)
            except (ValueError, AttributeError) as e:
                LOG_INFO(f"[异常] 捕获到(ValueError, AttributeError): {e}")
            # 错误输入保留为无效值，运行时将跳过检测。不能把用户的错误输入
            # 静默改成可执行的默认容差，导致 F8 后按另一组参数触发药剂。
            return (None, None, None)
        # 无控件的兼容调用才使用默认容差。
        return (10, 30, 50)

    @staticmethod
    def _add_text_ocr_config(
        config: Dict[str, Any], resource_type: str, widgets: Dict[str, Any]
    ):
        """添加文本OCR配置"""
        coord_input = widgets.get("coord_input")
        coords = ResourceConfigManager._parse_coordinates(
            coord_input,
            4,
            ResourceConfigManager.DEFAULT_COORDS[resource_type]["text_ocr"],
        )

        text_x1, text_y1, text_x2, text_y2 = coords

        # OCR引擎选择
        # currentData=None 表示加载的是本 UI 尚不认识的未来/拼错值。
        # 保留原值让运行时 fail-closed，而不是被上一 profile 的下拉选择污染。
        ocr_engine = config.get("ocr_engine", "template")
        ocr_combo = widgets.get("ocr_engine_combo")
        if ocr_combo and ocr_combo.currentData() is not None:
            ocr_engine = ocr_combo.currentData()

        # match_threshold 是**用户可调**的识别置信度门槛(会传给 recognize_and_parse
        # 的 min_score,见 wiki/04)。UI 上没有这个控件,写死 0.70 等于每次保存都把
        # 用户调过的阈值改回默认值。已有值优先。
        rect_default = ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"]
        config.update(
            {
                "detection_mode": "text_ocr",
                "text_x1": text_x1,
                "text_y1": text_y1,
                "text_x2": text_x2,
                "text_y2": text_y2,
                "ocr_engine": ocr_engine,
                "match_threshold": config.get("match_threshold", 0.70),
                # 保留矩形配置作为备份:优先沿用用户自己框过的矩形,没有才用默认值
                "region_x1": config.get("region_x1", rect_default[0]),
                "region_y1": config.get("region_y1", rect_default[1]),
                "region_x2": config.get("region_x2", rect_default[2]),
                "region_y2": config.get("region_y2", rect_default[3]),
            }
        )

    @staticmethod
    def _add_circle_config(
        config: Dict[str, Any],
        resource_type: str,
        widgets: Dict[str, Any],
        circle_config: Dict[str, Any],
    ):
        """添加圆形配置"""
        if circle_config and resource_type in circle_config:
            # 使用自动检测的圆形配置
            circle_data = circle_config[resource_type]
            config.update(
                {
                    "detection_mode": "circle",
                    "center_x": circle_data.get("center_x"),
                    "center_y": circle_data.get("center_y"),
                    "radius": circle_data.get("radius"),
                }
            )
        else:
            # 从输入框解析手动输入的圆形坐标
            coord_input = widgets.get("coord_input")
            coords = ResourceConfigManager._parse_coordinates(
                coord_input,
                3,
                ResourceConfigManager.DEFAULT_COORDS[resource_type]["circle"],
            )
            center_x, center_y, radius = coords

            config.update(
                {
                    "detection_mode": "circle",
                    "center_x": center_x,
                    "center_y": center_y,
                    "radius": radius,
                }
            )

        # 🔧 保留 half(半圆方向)字段:GUI 无对应控件,从圆形缓存带出,避免保存时丢失
        # (PoE2 角落球需要 HP="right"/MP="left",与 D4 默认相反;在 JSON 里设置)
        if circle_config and resource_type in circle_config:
            half = circle_config[resource_type].get("half")
            if half:
                config["half"] = half

    @staticmethod
    def _add_rectangle_config(
        config: Dict[str, Any], resource_type: str, widgets: Dict[str, Any]
    ):
        """添加矩形配置"""
        coord_input = widgets.get("coord_input")
        coords = ResourceConfigManager._parse_coordinates(
            coord_input,
            4,
            ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"],
        )
        x1, y1, x2, y2 = coords

        config.update(
            {
                "detection_mode": "rectangle",
                "region_x1": x1,
                "region_y1": y1,
                "region_x2": x2,
                "region_y2": y2,
            }
        )

    @staticmethod
    def _parse_coordinates(
        coord_input, expected_count: int, default_coords: Tuple
    ) -> Tuple:
        """解析坐标输入框，返回坐标元组"""
        if not coord_input:
            return default_coords

        try:
            coord_text = coord_input.text().strip()
            if coord_text:
                coords = [int(x.strip()) for x in coord_text.split(",")]
                if len(coords) == expected_count:
                    return tuple(coords)
        except (ValueError, AttributeError) as e:
            LOG_INFO(f"[异常] 捕获到(ValueError, AttributeError): {e}")

        # 控件存在但内容为空/错误时不能悄悄换成一个可执行的硬编码区域。
        # 加载缺失配置时 update_widget_from_config 会显式填入 default_coords；
        # 用户随后清空或输错则保存为无效零尺寸，让运行时安全跳过。
        return tuple(0 for _ in range(expected_count))

    @staticmethod
    def update_widget_from_config(
        widgets: Dict[str, Any],
        resource_config: Dict[str, Any],
        detection_mode_attr_name: str,
        circle_config_attr_name: str,
        resource_type: str,
        widget_owner,
    ):
        """
        从配置更新UI控件

        Args:
            widgets: UI控件字典
            resource_config: 资源配置字典
            detection_mode_attr_name: 检测模式属性名
            circle_config_attr_name: 圆形配置属性名
            resource_type: 资源类型
            widget_owner: 控件的拥有者对象
        """
        if not widgets:
            return

        if not isinstance(resource_config, dict):
            resource_config = {}

        setattr(widget_owner, circle_config_attr_name, {})

        # 更新基础配置
        # 配置段缺失时保持关闭，不能让 UI 默认值在下一次保存/F8 时
        # 无提示地启用药剂自动输入。
        widgets["enabled"].setChecked(resource_config.get("enabled") is True)
        widgets["key"].setText(
            normalize_key_name(
                resource_config.get("key", "1" if resource_type == "hp" else "2")
            )
        )

        try:
            threshold = config_int(resource_config.get("threshold", 50))
            if not 0 <= threshold <= 100:
                raise ValueError("资源阈值必须为 0..100")
        except ValueError:
            # 0 是“永不因低资源触发”的安全值。显式错误不能变成默认 50%，
            # 或由 SpinBox 把超范围值钳成 100% 后触发所有未满资源。
            threshold = 0
        widgets["threshold"].setValue(threshold)

        # 更新容差输入框
        tolerance_h = resource_config.get("tolerance_h", 10)
        tolerance_s = resource_config.get("tolerance_s", 30)
        tolerance_v = resource_config.get("tolerance_v", 50)
        tolerance_input = getattr(
            widget_owner, f"{resource_type}_tolerance_input", None
        )
        if tolerance_input:
            tolerance_input.setText(f"{tolerance_h},{tolerance_s},{tolerance_v}")

        # 根据检测模式更新配置
        detection_mode = str(
            resource_config.get("detection_mode", "rectangle") or ""
        ).lower()

        # 设置模式下拉框
        mode_combo = widgets.get("mode_combo")
        if mode_combo:
            mode_index = {"rectangle": 0, "circle": 1, "text_ocr": 2}.get(
                detection_mode, -1
            )
            mode_combo.setCurrentIndex(mode_index)

        # 更新检测模式状态
        setattr(widget_owner, detection_mode_attr_name, detection_mode)

        # OCR 选择虽只在 text_ocr 模式显示，但它仍属于当前 profile 的状态。
        # 每次加载都同步，避免 rectangle/circle profile 暗中继承上一份配置的引擎。
        ocr_engine = resource_config.get("ocr_engine", "template")
        ocr_combo = widgets.get("ocr_engine_combo")
        if ocr_combo:
            matched = False
            for i in range(ocr_combo.count()):
                if ocr_combo.itemData(i) == ocr_engine:
                    ocr_combo.setCurrentIndex(i)
                    matched = True
                    break
            if not matched:
                ocr_combo.setCurrentIndex(-1)

        # 更新坐标输入框
        coord_input = widgets.get("coord_input")
        if coord_input:
            if detection_mode == "text_ocr":
                text_coords = (
                    resource_config.get("text_x1"),
                    resource_config.get("text_y1"),
                    resource_config.get("text_x2"),
                    resource_config.get("text_y2"),
                )
                if all(c is not None for c in text_coords):
                    coord_input.setText(
                        f"{text_coords[0]},{text_coords[1]},{text_coords[2]},{text_coords[3]}"
                    )
                else:
                    coord_input.setText(
                        ",".join(
                            str(value)
                            for value in (
                                (0, 0, 0, 0) if resource_config else
                                ResourceConfigManager.DEFAULT_COORDS[resource_type]["text_ocr"]
                            )
                        )
                    )

            elif detection_mode == "circle":
                circle_coords = (
                    resource_config.get("center_x"),
                    resource_config.get("center_y"),
                    resource_config.get("radius"),
                )
                if all(c is not None for c in circle_coords):
                    coord_input.setText(
                        f"{circle_coords[0]},{circle_coords[1]},{circle_coords[2]}"
                    )

                    # 更新圆形配置缓存
                    circle_data = {
                        "center_x": circle_coords[0],
                        "center_y": circle_coords[1],
                        "radius": circle_coords[2],
                    }
                    # 🔧 把 half(半圆方向)一并存入缓存,GUI 保存时再带回配置,避免丢失
                    half_val = resource_config.get("half")
                    if half_val:
                        circle_data["half"] = half_val
                    setattr(
                        widget_owner,
                        circle_config_attr_name,
                        {resource_type: circle_data},
                    )
                else:
                    coord_input.setText(
                        ",".join(
                            str(value)
                            for value in (
                                (0, 0, 0) if resource_config else
                                ResourceConfigManager.DEFAULT_COORDS[resource_type]["circle"]
                            )
                        )
                    )

            elif detection_mode == "rectangle":
                rect_coords = (
                    resource_config.get("region_x1"),
                    resource_config.get("region_y1"),
                    resource_config.get("region_x2"),
                    resource_config.get("region_y2"),
                )
                if all(c is not None for c in rect_coords):
                    coord_input.setText(
                        f"{rect_coords[0]},{rect_coords[1]},{rect_coords[2]},{rect_coords[3]}"
                    )
                else:
                    coord_input.setText(
                        ",".join(
                            str(value)
                            for value in (
                                (0, 0, 0, 0) if resource_config else
                                ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"]
                            )
                        )
                    )
            else:
                # 与未知模式的下拉空选择一致；不要显示上一份 profile 的坐标。
                coord_input.clear()
