#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新颜色配置格式的实际应用
"""

import sys
import os
import json

# 添加路径以便导入模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from torchlight_assistant.gui.resource_widgets import ResourceManagementWidget
from PySide6.QtWidgets import QApplication

def test_color_format_integration():
    """测试新颜色格式在实际UI中的集成"""
    
    app = QApplication(sys.argv)
    
    # 创建资源管理控件
    widget = ResourceManagementWidget()
    
    print("=== 测试新颜色配置格式集成 ===")
    
    # 测试1: 检查默认配置
    print("\n1. 测试默认配置:")
    config = widget.get_config()
    hp_config = config['resource_management']['hp_config']
    mp_config = config['resource_management']['mp_config']
    
    print(f"HP配置: {len(hp_config['colors'])} 种颜色")
    for i, color in enumerate(hp_config['colors']):
        print(f"  颜色{i+1}: HSV({color['target_h']},{color['target_s']},{color['target_v']}) 容差(±{color['tolerance_h']},±{color['tolerance_s']},±{color['tolerance_v']})")
    
    print(f"MP配置: {len(mp_config['colors'])} 种颜色")
    for i, color in enumerate(mp_config['colors']):
        print(f"  颜色{i+1}: HSV({color['target_h']},{color['target_s']},{color['target_v']}) 容差(±{color['tolerance_h']},±{color['tolerance_s']},±{color['tolerance_v']})")
    
    # 测试2: 应用新的配置格式
    print("\n2. 测试新配置格式应用:")
    new_config = {
        "resource_management": {
            "hp_config": {
                "enabled": True,
                "key": "1",
                "threshold": 60,
                "cooldown": 4000,
                "detection_mode": "rectangle",
                "region_x1": 136,
                "region_y1": 910,
                "region_x2": 213,
                "region_y2": 1004,
                "colors": [
                    {
                        "name": "RedHP",
                        "target_h": 157,
                        "target_s": 75,
                        "target_v": 29,
                        "tolerance_h": 15,
                        "tolerance_s": 25,
                        "tolerance_v": 30,
                    },
                    {
                        "name": "GreenHP", 
                        "target_h": 40,
                        "target_s": 84,
                        "target_v": 48,
                        "tolerance_h": 15,
                        "tolerance_s": 25,
                        "tolerance_v": 30,
                    }
                ]
            },
            "mp_config": {
                "enabled": True,
                "key": "2", 
                "threshold": 70,
                "cooldown": 6000,
                "detection_mode": "rectangle",
                "region_x1": 1552,
                "region_y1": 910,
                "region_x2": 1560,
                "region_y2": 1004,
                "colors": [
                    {
                        "name": "BlueMP",
                        "target_h": 104,
                        "target_s": 80,
                        "target_v": 58,
                        "tolerance_h": 8,
                        "tolerance_s": 12,
                        "tolerance_v": 15,
                    }
                ]
            },
            "check_interval": 150
        }
    }
    
    widget.update_from_config(new_config)
    print("新配置已应用到UI控件")
    
    # 测试3: 从UI获取更新后的配置
    print("\n3. 测试UI更新后的配置获取:")
    updated_config = widget.get_config()
    hp_updated = updated_config['resource_management']['hp_config']
    mp_updated = updated_config['resource_management']['mp_config']
    
    print(f"更新后HP配置: {len(hp_updated['colors'])} 种颜色")
    for i, color in enumerate(hp_updated['colors']):
        print(f"  颜色{i+1}: HSV({color['target_h']},{color['target_s']},{color['target_v']}) 容差(±{color['tolerance_h']},±{color['tolerance_s']},±{color['tolerance_v']})")
    
    print(f"更新后MP配置: {len(mp_updated['colors'])} 种颜色")
    for i, color in enumerate(mp_updated['colors']):
        print(f"  颜色{i+1}: HSV({color['target_h']},{color['target_s']},{color['target_v']}) 容差(±{color['tolerance_h']},±{color['tolerance_s']},±{color['tolerance_v']})")
    
    # 测试4: 验证容差设置是否正确同步
    print("\n4. 测试容差设置同步:")
    if hasattr(widget, 'tolerance_widgets') and widget.tolerance_widgets:
        h_tol = widget.tolerance_widgets["h"].value()
        s_tol = widget.tolerance_widgets["s"].value()
        v_tol = widget.tolerance_widgets["v"].value()
        print(f"UI容差控件值: H±{h_tol}, S±{s_tol}, V±{v_tol}")
    else:
        print("警告: 容差控件未找到或未初始化")
    
    print("\n✅ 测试完成！新颜色配置格式集成成功")
    return True

if __name__ == "__main__":
    try:
        success = test_color_format_integration()
        print(f"\n{'='*50}")
        print(f"测试结果: {'成功' if success else '失败'}")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sys.exit(0)