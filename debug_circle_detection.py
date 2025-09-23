"""
半圆形血量检测调试工具

这个脚本帮助调试半圆形血量检测功能，提供详细的调试信息和可视化显示。
"""

import cv2
import numpy as np
import json
import time
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager
from torchlight_assistant.core.resource_manager import ResourceManager

def debug_circle_detection():
    """调试半圆形检测功能"""
    print("=== 半圆形血量检测调试工具 ===")
    
    # 加载配置
    config_file = "poe2_锐眼.json"
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        print(f"✓ 已加载配置文件: {config_file}")
    except Exception as e:
        print(f"✗ 加载配置文件失败: {e}")
        return
    
    # 创建管理器（ResourceManager不需要InputHandler进行检测）
    border_manager = BorderFrameManager()
    
    # 直接创建一个简化的资源管理器用于调试
    class DebugResourceManager:
        def __init__(self, border_manager):
            self.border_frame_manager = border_manager
            self.hp_config = {}
            self.mp_config = {}
        
        def update_config(self, resource_config):
            self.hp_config = resource_config.get("hp_config", {})
            self.mp_config = resource_config.get("mp_config", {})
        
        def get_current_resource_percentage(self, resource_type: str, cached_frame = None):
            config = self.hp_config if resource_type == "hp" else self.mp_config
            
            if not config.get("enabled", False):
                return 100.0
            
            detection_mode = config.get("detection_mode", "rectangle")
            
            if detection_mode == "circle":
                center_x = config.get("center_x")
                center_y = config.get("center_y")
                radius = config.get("radius")
                
                if center_x is None or center_y is None or radius is None:
                    return 100.0
                
                frame = cached_frame
                if frame is None:
                    frame = self.border_frame_manager.get_current_frame()
                
                if frame is None:
                    return 100.0
                
                return self.border_frame_manager.compare_resource_circle(
                    frame, center_x, center_y, radius, resource_type, 0.0, config
                )
            return 100.0
    
    resource_manager = DebugResourceManager(border_manager)
    
    # 更新配置
    resource_config = config.get("resource_management", {})
    resource_manager.update_config(resource_config)
    
    print("\n=== 配置信息 ===")
    hp_config = resource_config.get("hp_config", {})
    mp_config = resource_config.get("mp_config", {})
    
    print(f"HP配置:")
    print(f"  检测模式: {hp_config.get('detection_mode', 'N/A')}")
    print(f"  圆心坐标: ({hp_config.get('center_x', 'N/A')}, {hp_config.get('center_y', 'N/A')})")
    print(f"  半径: {hp_config.get('radius', 'N/A')}")
    print(f"  阈值: {hp_config.get('threshold', 'N/A')}%")
    
    print(f"\nMP配置:")
    print(f"  检测模式: {mp_config.get('detection_mode', 'N/A')}")
    print(f"  圆心坐标: ({mp_config.get('center_x', 'N/A')}, {mp_config.get('center_y', 'N/A')})")
    print(f"  半径: {mp_config.get('radius', 'N/A')}")
    print(f"  阈值: {mp_config.get('threshold', 'N/A')}%")
    
    print("\n=== 开始实时检测 ===")
    print("按 'q' 键退出, 'c' 键捕获调试图像, 's' 键保存当前帧")
    
    # BorderFrameManager会自动初始化，无需手动调用initialize
    
    while True:
        try:
            # 获取当前帧
            frame = border_manager.get_current_frame()
            if frame is None:
                print("⚠ 无法获取游戏画面，请确保游戏窗口处于前台")
                time.sleep(1)
                continue
            
            # 检测HP和MP
            hp_percentage = resource_manager.get_current_resource_percentage("hp", frame)
            mp_percentage = resource_manager.get_current_resource_percentage("mp", frame)
            
            # 创建调试显示
            debug_frame = frame.copy()
            
            # 绘制HP圆形区域
            if hp_config.get('detection_mode') == 'circle':
                center_x = hp_config.get('center_x', 0)
                center_y = hp_config.get('center_y', 0)
                radius = hp_config.get('radius', 0)
                
                # 绘制完整圆形（蓝色）
                cv2.circle(debug_frame, (center_x, center_y), radius, (255, 0, 0), 2)
                
                # 绘制半圆形检测区域（绿色）
                # HP检测左半边
                mask = np.zeros(debug_frame.shape[:2], dtype=np.uint8)
                cv2.circle(mask, (center_x, center_y), radius, 255, -1)
                # 遮盖右半边
                cv2.rectangle(mask, (center_x, center_y - radius), 
                             (center_x + radius, center_y + radius), 0, -1)
                
                # 在调试帧上叠加半圆形区域
                overlay = debug_frame.copy()
                overlay[mask > 0] = [0, 255, 0]  # 绿色
                cv2.addWeighted(debug_frame, 0.8, overlay, 0.2, 0, debug_frame)
                
                # 显示HP百分比
                cv2.putText(debug_frame, f"HP: {hp_percentage:.1f}%", 
                           (center_x - 50, center_y - radius - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            
            # 绘制MP圆形区域
            if mp_config.get('detection_mode') == 'circle':
                center_x = mp_config.get('center_x', 0)
                center_y = mp_config.get('center_y', 0)
                radius = mp_config.get('radius', 0)
                
                # 绘制完整圆形（红色）
                cv2.circle(debug_frame, (center_x, center_y), radius, (0, 0, 255), 2)
                
                # 绘制半圆形检测区域（青色）
                # MP检测右半边
                mask = np.zeros(debug_frame.shape[:2], dtype=np.uint8)
                cv2.circle(mask, (center_x, center_y), radius, 255, -1)
                # 遮盖左半边
                cv2.rectangle(mask, (center_x - radius, center_y - radius), 
                             (center_x, center_y + radius), 0, -1)
                
                # 在调试帧上叠加半圆形区域
                overlay = debug_frame.copy()
                overlay[mask > 0] = [255, 255, 0]  # 青色
                cv2.addWeighted(debug_frame, 0.8, overlay, 0.2, 0, debug_frame)
                
                # 显示MP百分比
                cv2.putText(debug_frame, f"MP: {mp_percentage:.1f}%", 
                           (center_x - 50, center_y - radius - 10), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            
            # 显示整体状态
            status_text = f"HP: {hp_percentage:.1f}% | MP: {mp_percentage:.1f}%"
            cv2.putText(debug_frame, status_text, (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            
            # 显示帮助信息
            cv2.putText(debug_frame, "Press: 'q'=quit, 'c'=capture, 's'=save", 
                       (10, debug_frame.shape[0] - 10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            # 缩放显示（如果屏幕太大）
            height, width = debug_frame.shape[:2]
            if width > 1600:
                scale = 1600 / width
                new_width = int(width * scale)
                new_height = int(height * scale)
                debug_frame = cv2.resize(debug_frame, (new_width, new_height))
            
            # 显示调试窗口
            cv2.imshow("半圆形血量检测调试", debug_frame)
            
            # 控制台输出
            print(f"\r当前状态 - HP: {hp_percentage:5.1f}% | MP: {mp_percentage:5.1f}%", end="")
            
            # 处理按键
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('c'):
                # 捕获并保存调试区域图像
                timestamp = int(time.time())
                
                # 保存HP区域
                if hp_config.get('detection_mode') == 'circle':
                    center_x = hp_config.get('center_x', 0)
                    center_y = hp_config.get('center_y', 0)
                    radius = hp_config.get('radius', 0)
                    x1, y1 = max(0, center_x - radius), max(0, center_y - radius)
                    x2, y2 = min(frame.shape[1], center_x + radius), min(frame.shape[0], center_y + radius)
                    hp_region = frame[y1:y2, x1:x2]
                    cv2.imwrite(f"debug_hp_region_{timestamp}.png", hp_region)
                    print(f"\n✓ 已保存HP区域图像: debug_hp_region_{timestamp}.png")
                
                # 保存MP区域
                if mp_config.get('detection_mode') == 'circle':
                    center_x = mp_config.get('center_x', 0)
                    center_y = mp_config.get('center_y', 0)
                    radius = mp_config.get('radius', 0)
                    x1, y1 = max(0, center_x - radius), max(0, center_y - radius)
                    x2, y2 = min(frame.shape[1], center_x + radius), min(frame.shape[0], center_y + radius)
                    mp_region = frame[y1:y2, x1:x2]
                    cv2.imwrite(f"debug_mp_region_{timestamp}.png", mp_region)
                    print(f"✓ 已保存MP区域图像: debug_mp_region_{timestamp}.png")
                    
            elif key == ord('s'):
                # 保存完整帧
                timestamp = int(time.time())
                cv2.imwrite(f"debug_full_frame_{timestamp}.png", frame)
                cv2.imwrite(f"debug_overlay_frame_{timestamp}.png", debug_frame)
                print(f"\n✓ 已保存完整帧: debug_full_frame_{timestamp}.png")
                print(f"✓ 已保存调试帧: debug_overlay_frame_{timestamp}.png")
        
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n✗ 检测过程中发生错误: {e}")
            break
    
    print("\n\n=== 调试完成 ===")
    cv2.destroyAllWindows()

if __name__ == "__main__":
    debug_circle_detection()