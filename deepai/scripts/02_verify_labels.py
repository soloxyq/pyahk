#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""步骤2: 标注验证 - 人工验证和修正自动标注结果"""

import cv2
import json
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from deepai.config import DATA_DIR


class LabelVerifier:
    """标注验证GUI工具"""
    
    def __init__(self, labels_path):
        self.labels_path = labels_path
        self.data = self._load_labels()
        self.current_idx = 0
        
        # 过滤需要验证的数据
        self.needs_verify = [d for d in self.data if d.get('needs_verification', False)]
        
        # 创建GUI
        self.root = tk.Tk()
        self.root.title("DeepAI 标注验证工具")
        self.root.geometry("800x600")
        
        self._create_widgets()
        self._show_current()
    
    def _load_labels(self):
        """加载标注数据"""
        with open(self.labels_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _save_labels(self):
        """保存标注数据"""
        with open(self.labels_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def _create_widgets(self):
        """创建GUI组件"""
        # 顶部信息栏
        info_frame = ttk.Frame(self.root)
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.info_label = ttk.Label(info_frame, text="", font=('Arial', 12))
        self.info_label.pack()
        
        # 图像显示区域
        img_frame = ttk.Frame(self.root)
        img_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.canvas = tk.Canvas(img_frame, bg='gray')
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # 标注输入区域
        label_frame = ttk.Frame(self.root)
        label_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(label_frame, text="当前标注:", font=('Arial', 12)).pack(side=tk.LEFT, padx=5)
        
        self.label_var = tk.StringVar()
        self.label_entry = ttk.Entry(label_frame, textvariable=self.label_var, font=('Arial', 16), width=10)
        self.label_entry.pack(side=tk.LEFT, padx=5)
        self.label_entry.bind('<Return>', lambda e: self._save_and_next())
        
        # 置信度显示
        self.confidence_label = ttk.Label(label_frame, text="", font=('Arial', 10))
        self.confidence_label.pack(side=tk.LEFT, padx=10)
        
        # 按钮区域
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(btn_frame, text="上一个 (←)", command=self._prev).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存并下一个 (→)", command=self._save_and_next).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="跳过 (Space)", command=self._next).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="删除 (Delete)", command=self._delete).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="保存退出 (Ctrl+S)", command=self._save_and_quit).pack(side=tk.RIGHT, padx=5)
        
        # 快速标注按钮
        quick_frame = ttk.LabelFrame(self.root, text="快速标注")
        quick_frame.pack(fill=tk.X, padx=10, pady=5)
        
        for i in range(10):
            ttk.Button(quick_frame, text=str(i), width=3, 
                      command=lambda n=str(i): self._quick_label(n)).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(quick_frame, text="/", width=3, 
                  command=lambda: self._quick_label("/")).pack(side=tk.LEFT, padx=2)
        
        # 键盘快捷键
        self.root.bind('<Left>', lambda e: self._prev())
        self.root.bind('<Right>', lambda e: self._save_and_next())
        self.root.bind('<space>', lambda e: self._next())
        self.root.bind('<Delete>', lambda e: self._delete())
        self.root.bind('<Control-s>', lambda e: self._save_and_quit())
        
        # 数字键快速标注
        for i in range(10):
            self.root.bind(str(i), lambda e, n=str(i): self._quick_label(n))
        self.root.bind('/', lambda e: self._quick_label('/'))
    
    def _show_current(self):
        """显示当前图像"""
        if not self.needs_verify:
            self.info_label.config(text="✅ 所有标注已验证完成!")
            return
        
        if self.current_idx >= len(self.needs_verify):
            self.info_label.config(text="✅ 所有待验证标注已完成!")
            return
        
        data = self.needs_verify[self.current_idx]
        
        # 更新信息
        info_text = f"进度: {self.current_idx + 1}/{len(self.needs_verify)} | 总数: {len(self.data)}"
        self.info_label.config(text=info_text)
        
        # 加载图像
        img_path = data['image_path']
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            self.info_label.config(text=f"❌ 无法加载图像: {img_path}")
            return
        
        # 放大显示
        scale = 10
        h, w = img.shape
        img_resized = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
        
        # 转换为PIL格式
        img_pil = Image.fromarray(img_resized)
        self.photo = ImageTk.PhotoImage(img_pil)
        
        # 显示在Canvas中心
        self.canvas.delete('all')
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        x = (canvas_width - img_resized.shape[1]) // 2
        y = (canvas_height - img_resized.shape[0]) // 2
        self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo)
        
        # 更新标注输入
        self.label_var.set(data.get('label', ''))
        self.label_entry.focus()
        self.label_entry.select_range(0, tk.END)
        
        # 更新置信度
        confidence = data.get('confidence', 0.0)
        self.confidence_label.config(text=f"置信度: {confidence:.2%}")
    
    def _quick_label(self, label):
        """快速标注"""
        self.label_var.set(label)
        self._save_and_next()
    
    def _save_and_next(self):
        """保存并下一个"""
        if not self.needs_verify or self.current_idx >= len(self.needs_verify):
            return
        
        data = self.needs_verify[self.current_idx]
        new_label = self.label_var.get().strip()
        
        # 更新原数据
        for d in self.data:
            if d['image_path'] == data['image_path']:
                d['label'] = new_label
                d['needs_verification'] = False
                d['verified'] = True
                break
        
        self._next()
    
    def _next(self):
        """下一个"""
        self.current_idx += 1
        self._show_current()
    
    def _prev(self):
        """上一个"""
        if self.current_idx > 0:
            self.current_idx -= 1
            self._show_current()
    
    def _delete(self):
        """删除当前标注"""
        if not self.needs_verify or self.current_idx >= len(self.needs_verify):
            return
        
        data = self.needs_verify[self.current_idx]
        
        # 从原数据中删除
        self.data = [d for d in self.data if d['image_path'] != data['image_path']]
        
        # 从待验证列表中删除
        self.needs_verify.pop(self.current_idx)
        
        # 显示下一个（索引不变）
        self._show_current()
    
    def _save_and_quit(self):
        """保存并退出"""
        self._save_labels()
        
        # 统计信息
        total = len(self.data)
        verified = sum(1 for d in self.data if d.get('verified', False))
        needs_verify = sum(1 for d in self.data if d.get('needs_verification', False))
        
        print(f"\n{'='*80}")
        print(f"✅ 标注已保存!")
        print(f"{'='*80}")
        print(f"总数: {total}")
        print(f"已验证: {verified}")
        print(f"待验证: {needs_verify}")
        print(f"{'='*80}\n")
        
        self.root.destroy()
    
    def run(self):
        """运行GUI"""
        self.root.mainloop()


def main():
    """主函数"""
    labels_path = Path(DATA_DIR) / 'labels.json'
    
    if not labels_path.exists():
        print(f"❌ 错误: 标注文件不存在 - {labels_path}")
        print(f"请先运行数据准备脚本: python deepai/scripts/01_prepare_data.py")
        return
    
    print(f"\n{'='*80}")
    print(f"🔍 DeepAI 标注验证工具")
    print(f"{'='*80}\n")
    print(f"快捷键:")
    print(f"  0-9, /  : 快速标注")
    print(f"  ←       : 上一个")
    print(f"  →/Enter : 保存并下一个")
    print(f"  Space   : 跳过")
    print(f"  Delete  : 删除")
    print(f"  Ctrl+S  : 保存退出")
    print(f"{'='*80}\n")
    
    verifier = LabelVerifier(str(labels_path))
    verifier.run()


if __name__ == "__main__":
    main()
