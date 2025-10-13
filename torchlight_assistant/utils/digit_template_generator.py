#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数字模板生成器 - 使用游戏字体生成0-9和/的模板图"""

import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Optional
from pathlib import Path


class DigitTemplateGenerator:
    """生成数字字符模板用于模板匹配"""
    
    def __init__(self, font_path: str, font_size: int = 16, color: tuple = (255, 255, 255)):
        """
        初始化模板生成器
        
        Args:
            font_path: 字体文件路径
            font_size: 字体大小（像素）
            color: 文本颜色RGB，默认白色
        """
        self.font_path = font_path
        self.font_size = font_size
        self.color = color
        
        # 检查字体文件是否存在
        if not os.path.exists(font_path):
            raise FileNotFoundError(f"字体文件不存在: {font_path}")
        
        # 加载字体
        try:
            self.font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            raise RuntimeError(f"加载字体失败: {e}")
        
        # 模板缓存
        self._templates: Dict[str, np.ndarray] = {}
        self._templates_generated = False
    
    def generate_digit_template(self, char: str) -> np.ndarray:
        """
        生成单个字符的灰度模板
        
        Args:
            char: 要生成的字符 ('0'-'9' 或 '/')
            
        Returns:
            灰度图像 (numpy array)
        """
        if char in self._templates:
            return self._templates[char]
        
        # 创建临时图像来测量文字尺寸
        temp_img = Image.new('L', (100, 100), color=0)
        temp_draw = ImageDraw.Draw(temp_img)
        
        # 获取文字边界框
        bbox = temp_draw.textbbox((0, 0), char, font=self.font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # 添加一些边距
        padding = 4
        img_width = text_width + padding * 2
        img_height = text_height + padding * 2
        
        # 创建黑色背景图像
        img = Image.new('L', (img_width, img_height), color=0)
        draw = ImageDraw.Draw(img)
        
        # 绘制白色文字
        # 注意：需要调整y坐标以对齐基线
        draw.text((padding - bbox[0], padding - bbox[1]), char, 
                 fill=255, font=self.font)
        
        # 转换为numpy数组
        template = np.array(img, dtype=np.uint8)
        
        # 缓存模板
        self._templates[char] = template
        
        return template
    
    def get_all_templates(self) -> Dict[str, np.ndarray]:
        """
        生成所有数字和斜杠的模板
        
        Returns:
            字典: {字符: 模板图像}
        """
        if self._templates_generated:
            return self._templates
        
        # 生成0-9和/
        chars = '0123456789/'
        for char in chars:
            self.generate_digit_template(char)
        
        self._templates_generated = True
        return self._templates
    
    def save_templates_to_disk(self, output_dir: str):
        """
        保存所有模板到磁盘（调试用）
        
        Args:
            output_dir: 输出目录路径
        """
        os.makedirs(output_dir, exist_ok=True)
        
        templates = self.get_all_templates()
        
        for char, template in templates.items():
            # 斜杠用下划线替代文件名
            filename = f"template_{char if char != '/' else 'slash'}.png"
            filepath = os.path.join(output_dir, filename)
            cv2.imwrite(filepath, template)
        
        print(f"[DigitTemplateGenerator] 已保存 {len(templates)} 个模板到: {output_dir}")
    
    def get_template_info(self) -> Dict[str, tuple]:
        """
        获取所有模板的尺寸信息
        
        Returns:
            字典: {字符: (宽度, 高度)}
        """
        templates = self.get_all_templates()
        info = {}
        for char, template in templates.items():
            h, w = template.shape[:2]
            info[char] = (w, h)
        return info


def create_default_generator() -> DigitTemplateGenerator:
    """
    创建默认的模板生成器（使用游戏真实字符模板）
    
    Returns:
        DigitTemplateGenerator实例
    """
    # 获取项目根目录
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent
    
    # 优先使用真实游戏字符模板
    real_template_dir = project_root / "game_char_templates"
    
    if real_template_dir.exists():
        # 使用真实游戏字符模板
        print(f"[DigitTemplateGenerator] 使用真实游戏字符模板: {real_template_dir}")
        
        # 创建一个伪生成器来加载模板
        class RealTemplateGenerator:
            def __init__(self, template_dir):
                self._templates = {}
                self._load_templates(template_dir)
            
            def _load_templates(self, template_dir):
                """从目录加载真实游戏字符模板"""
                import cv2
                for template_file in template_dir.glob('template_*.png'):
                    if '_4x' in template_file.name:
                        continue
                    
                    # 解析字符名
                    name_parts = template_file.stem.split('_')
                    if len(name_parts) >= 2:
                        char = name_parts[1]
                        if char == 'slash':
                            char = '/'
                        
                        # 加载模板
                        template = cv2.imread(str(template_file), cv2.IMREAD_GRAYSCALE)
                        if template is not None:
                            self._templates[char] = template
            
            def get_all_templates(self):
                return self._templates
            
            def get_template_info(self):
                info = {}
                for char, template in self._templates.items():
                    h, w = template.shape[:2]
                    info[char] = (w, h)
                return info
        
        generator = RealTemplateGenerator(real_template_dir)
        print(f"[DigitTemplateGenerator] 加载了 {len(generator.get_all_templates())} 个真实游戏字符模板")
        return generator
    
    # 回退到字体生成
    font_path = project_root / "wiki" / "fonts" / "方正准圆.ttf"
    
    if not font_path.exists():
        raise FileNotFoundError(f"游戏字体文件不存在: {font_path}")
    
    # 创建生成器（字号16px，白色）
    generator = DigitTemplateGenerator(
        font_path=str(font_path),
        font_size=16,
        color=(255, 255, 255)
    )
    
    # 预生成所有模板
    generator.get_all_templates()
    
    return generator


# 全局单例
_global_generator: Optional[DigitTemplateGenerator] = None


def get_digit_template_generator() -> DigitTemplateGenerator:
    """获取全局模板生成器实例"""
    global _global_generator
    if _global_generator is None:
        _global_generator = create_default_generator()
    return _global_generator


if __name__ == "__main__":
    # 测试代码
    print("测试数字模板生成器...")
    
    generator = create_default_generator()
    
    # 显示模板信息
    info = generator.get_template_info()
    print("\n模板尺寸信息:")
    for char, (w, h) in info.items():
        print(f"  '{char}': {w}x{h}px")
    
    # 保存到临时目录
    output_dir = "digit_templates_test"
    generator.save_templates_to_disk(output_dir)
    print(f"\n✅ 测试完成！模板已保存到 {output_dir}/")
