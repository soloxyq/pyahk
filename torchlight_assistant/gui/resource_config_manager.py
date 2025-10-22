#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资源配置管理器 - 统一HP/MP配置构建和解析逻辑"""

from typing import Dict, Any, Tuple


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
        }
    }

    @staticmethod
    def build_resource_config(
        resource_type: str,
        widgets: Dict[str, Any],
        detection_mode: str,
        circle_config: Dict[str, Any],
        timing_manager=None
    ) -> Dict[str, Any]:
        """
        统一的资源配置构建方法，消除HP/MP重复逻辑
        
        Args:
            resource_type: "hp" 或 "mp"
            widgets: 对应的UI控件字典
            detection_mode: 检测模式
            circle_config: 圆形配置缓存
            timing_manager: 时间管理器（用于获取冷却时间）
        """
        # 基础配置
        config = {
            "enabled": widgets["enabled"].isChecked(),
            "key": widgets["key"].text().strip(),
            "threshold": widgets["threshold"].value(),
            "cooldown": ResourceConfigManager._get_cooldown_from_timing(
                resource_type, timing_manager
            ),
        }

        # 添加容差配置
        tolerance_h, tolerance_s, tolerance_v = ResourceConfigManager._get_tolerance_from_widgets(
            resource_type, widgets
        )
        config.update({
            "tolerance_h": tolerance_h,
            "tolerance_s": tolerance_s,
            "tolerance_v": tolerance_v,
        })

        # 根据检测模式添加相应配置
        if detection_mode == "text_ocr":
            ResourceConfigManager._add_text_ocr_config(config, resource_type, widgets)
        elif detection_mode == "circle":
            ResourceConfigManager._add_circle_config(config, resource_type, widgets, circle_config)
        else:  # rectangle
            ResourceConfigManager._add_rectangle_config(config, resource_type, widgets)

        # 添加颜色配置
        ResourceConfigManager._add_colors_config(config, resource_type)

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
            except (AttributeError, KeyError):
                pass
        # 默认值
        return 5000 if resource_type == "hp" else 8000

    @staticmethod
    def _get_tolerance_from_widgets(resource_type: str, widgets: Dict[str, Any]) -> Tuple[int, int, int]:
        """从UI控件获取容差设置"""
        tolerance_input = widgets.get("tolerance_input")
        if tolerance_input:
            try:
                tolerance_text = tolerance_input.text().strip()
                if tolerance_text:
                    values = [int(x.strip()) for x in tolerance_text.split(",") if x.strip()]
                    if len(values) == 3:
                        return tuple(values)
            except (ValueError, AttributeError):
                pass
        # 默认容差
        return (10, 30, 50)

    @staticmethod
    def _add_text_ocr_config(config: Dict[str, Any], resource_type: str, widgets: Dict[str, Any]):
        """添加文本OCR配置"""
        coord_input = widgets.get("coord_input")
        coords = ResourceConfigManager._parse_coordinates(
            coord_input, 4, ResourceConfigManager.DEFAULT_COORDS[resource_type]["text_ocr"]
        )
        
        text_x1, text_y1, text_x2, text_y2 = coords

        # OCR引擎选择
        ocr_engine = "template"
        ocr_combo = widgets.get("ocr_engine_combo")
        if ocr_combo:
            ocr_engine = ocr_combo.currentData() or "template"

        config.update({
            "detection_mode": "text_ocr",
            "text_x1": text_x1,
            "text_y1": text_y1,
            "text_x2": text_x2,
            "text_y2": text_y2,
            "ocr_engine": ocr_engine,
            "match_threshold": 0.70,
            # 保留矩形配置作为备份
            "region_x1": ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"][0],
            "region_y1": ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"][1],
            "region_x2": ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"][2],
            "region_y2": ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"][3],
        })

    @staticmethod
    def _add_circle_config(config: Dict[str, Any], resource_type: str, widgets: Dict[str, Any], circle_config: Dict[str, Any]):
        """添加圆形配置"""
        if circle_config and resource_type in circle_config:
            # 使用自动检测的圆形配置
            circle_data = circle_config[resource_type]
            config.update({
                "detection_mode": "circle",
                "center_x": circle_data.get("center_x"),
                "center_y": circle_data.get("center_y"),
                "radius": circle_data.get("radius"),
            })
        else:
            # 从输入框解析手动输入的圆形坐标
            coord_input = widgets.get("coord_input")
            coords = ResourceConfigManager._parse_coordinates(
                coord_input, 3, ResourceConfigManager.DEFAULT_COORDS[resource_type]["circle"]
            )
            center_x, center_y, radius = coords

            config.update({
                "detection_mode": "circle",
                "center_x": center_x,
                "center_y": center_y,
                "radius": radius,
            })

    @staticmethod
    def _add_rectangle_config(config: Dict[str, Any], resource_type: str, widgets: Dict[str, Any]):
        """添加矩形配置"""
        coord_input = widgets.get("coord_input")
        coords = ResourceConfigManager._parse_coordinates(
            coord_input, 4, ResourceConfigManager.DEFAULT_COORDS[resource_type]["rectangle"]
        )
        x1, y1, x2, y2 = coords

        config.update({
            "detection_mode": "rectangle",
            "region_x1": x1,
            "region_y1": y1,
            "region_x2": x2,
            "region_y2": y2,
        })

    @staticmethod
    def _parse_coordinates(coord_input, expected_count: int, default_coords: Tuple) -> Tuple:
        """解析坐标输入框，返回坐标元组"""
        if not coord_input:
            return default_coords

        try:
            coord_text = coord_input.text().strip()
            if coord_text:
                coords = [int(x.strip()) for x in coord_text.split(",")]
                if len(coords) >= expected_count:
                    return tuple(coords[:expected_count])
        except (ValueError, AttributeError):
            pass
        
        return default_coords

    @staticmethod
    def _add_colors_config(config: Dict[str, Any], resource_type: str):
        """添加颜色配置"""
        # 使用默认颜色配置
        if resource_type == "hp":
            default_colors = "157,75,29\n40,84,48"  # HP默认：红色+绿色
        else:
            default_colors = "104,80,58"  # MP默认：蓝色

        colors = ResourceConfigManager._parse_colors_to_list(default_colors)
        config["colors"] = colors

    @staticmethod
    def _parse_colors_to_list(colors_text: str, default_tolerance: Tuple[int, int, int] = (10, 30, 50)) -> list:
        """将颜色配置文本解析为颜色列表"""
        colors = []
        try:
            lines = [line.strip() for line in colors_text.strip().split("\n") if line.strip()]

            for i, line in enumerate(lines, 1):
                color_values = [int(x.strip()) for x in line.split(",") if x.strip()]
                if len(color_values) == 3:
                    h, s, v = color_values
                    h_tol, s_tol, v_tol = default_tolerance
                    color = {
                        "name": f"Color{i}",
                        "target_h": h,
                        "target_s": s,
                        "target_v": v,
                        "tolerance_h": h_tol,
                        "tolerance_s": s_tol,
                        "tolerance_v": v_tol,
                    }
                    colors.append(color)
        except (ValueError, AttributeError):
            pass

        # 如果解析失败，返回默认配置
        if not colors:
            h_tol, s_tol, v_tol = default_tolerance
            colors = [{
                "name": "Default",
                "target_h": 314 if "157" in colors_text else 104,  # HP红色或MP蓝色
                "target_s": 75 if "157" in colors_text else 80,
                "target_v": 29 if "157" in colors_text else 58,
                "tolerance_h": h_tol,
                "tolerance_s": s_tol,
                "tolerance_v": v_tol,
            }]

        return colors

    @staticmethod
    def update_widget_from_config(
        widgets: Dict[str, Any], 
        resource_config: Dict[str, Any], 
        detection_mode_attr_name: str,
        circle_config_attr_name: str,
        resource_type: str,
        widget_owner
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

        # 更新基础配置
        widgets["enabled"].setChecked(resource_config.get("enabled", True))
        widgets["key"].setText(resource_config.get("key", "1" if resource_type == "hp" else "2"))
        widgets["threshold"].setValue(resource_config.get("threshold", 50))

        # 更新容差输入框
        tolerance_h = resource_config.get("tolerance_h", 10)
        tolerance_s = resource_config.get("tolerance_s", 30)
        tolerance_v = resource_config.get("tolerance_v", 50)
        tolerance_input = getattr(widget_owner, f"{resource_type}_tolerance_input", None)
        if tolerance_input:
            tolerance_input.setText(f"{tolerance_h},{tolerance_s},{tolerance_v}")

        # 根据检测模式更新配置
        detection_mode = resource_config.get("detection_mode", "rectangle")
        
        # 设置模式下拉框
        mode_combo = widgets.get("mode_combo")
        if mode_combo:
            mode_index = {"rectangle": 0, "circle": 1, "text_ocr": 2}.get(detection_mode, 0)
            mode_combo.setCurrentIndex(mode_index)

        # 更新检测模式状态
        setattr(widget_owner, detection_mode_attr_name, detection_mode)

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
                    coord_input.setText(f"{text_coords[0]},{text_coords[1]},{text_coords[2]},{text_coords[3]}")
                    
                # 设置OCR引擎
                ocr_engine = resource_config.get("ocr_engine", "template")
                ocr_combo = widgets.get("ocr_engine_combo")
                if ocr_combo:
                    for i in range(ocr_combo.count()):
                        if ocr_combo.itemData(i) == ocr_engine:
                            ocr_combo.setCurrentIndex(i)
                            break

            elif detection_mode == "circle":
                circle_coords = (
                    resource_config.get("center_x"),
                    resource_config.get("center_y"),
                    resource_config.get("radius"),
                )
                if all(c is not None for c in circle_coords):
                    coord_input.setText(f"{circle_coords[0]},{circle_coords[1]},{circle_coords[2]}")
                    
                    # 更新圆形配置缓存
                    circle_data = {
                        "center_x": circle_coords[0],
                        "center_y": circle_coords[1],
                        "radius": circle_coords[2],
                    }
                    setattr(widget_owner, circle_config_attr_name, {resource_type: circle_data})

            else:  # rectangle
                rect_coords = (
                    resource_config.get("region_x1"),
                    resource_config.get("region_y1"),
                    resource_config.get("region_x2"),
                    resource_config.get("region_y2"),
                )
                if all(c is not None for c in rect_coords):
                    coord_input.setText(f"{rect_coords[0]},{rect_coords[1]},{rect_coords[2]},{rect_coords[3]}")

    @staticmethod
    def colors_list_to_text(colors_list: list) -> str:
        """将颜色列表转换为文本格式"""
        if not colors_list:
            return ""

        color_lines = []
        for color in colors_list:
            color_line = f"{color.get('target_h', 0)},{color.get('target_s', 75)},{color.get('target_v', 29)}"
            color_lines.append(color_line)

        return "\n".join(color_lines)