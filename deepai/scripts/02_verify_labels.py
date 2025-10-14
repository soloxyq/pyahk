#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=invalid-name
"""步骤2: 标注验证 - 优先验证低置信度样本"""

import cv2
import json
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deepai.config import DATA_DIR, CLASS_NAMES


class LabelVerifier:
    """标注验证GUI工具"""

    def __init__(self, labels_path):
        self.labels_path = labels_path
        self.data = self._load_labels()
        self.current_idx = 0

        # 只验证需要验证的样本（低置信度和识别失败的）
        # 高置信度样本自动标记为已验证，不需要人工检查
        self.to_verify = [
            d
            for d in self.data
            if d.get("needs_verification", False) and not d.get("verified", False)
        ]

        # 自动标记高置信度样本为已验证
        high_confidence_count = 0
        for d in self.data:
            if not d.get("verified", False) and not d.get("needs_verification", False):
                if d.get("label"):  # 有标注且高置信度
                    d["verified"] = True
                    high_confidence_count += 1

        if high_confidence_count > 0:
            print(f"✅ 自动标记 {high_confidence_count} 个高置信度样本为已验证")
            # 保存更新
            self._save_labels()

        self.root = tk.Tk()
        self.root.title("DeepAI 标注验证工具")
        self._create_widgets()
        self._show_current()

    def _load_labels(self):
        with open(self.labels_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_labels(self):
        with open(self.labels_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def _create_widgets(self):
        """创建GUI组件"""
        # 顶部信息栏
        info_frame = ttk.Frame(self.root)
        info_frame.pack(fill=tk.X, padx=10, pady=5)

        self.info_label = ttk.Label(info_frame, text="", font=("Arial", 12))
        self.info_label.pack()

        # 图像显示区域
        img_frame = ttk.Frame(self.root)
        img_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(img_frame, bg="gray", width=800, height=400)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 标注输入区域
        label_frame = ttk.Frame(self.root)
        label_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(label_frame, text="当前标注:", font=("Arial", 12)).pack(
            side=tk.LEFT, padx=5
        )

        self.label_var = tk.StringVar()
        self.label_entry = ttk.Entry(
            label_frame, textvariable=self.label_var, font=("Arial", 16), width=10
        )
        self.label_entry.pack(side=tk.LEFT, padx=5)
        self.label_entry.bind("<Return>", lambda e: self._save_and_next())

        # 置信度显示
        self.confidence_label = ttk.Label(label_frame, text="", font=("Arial", 10))
        self.confidence_label.pack(side=tk.LEFT, padx=10)

        # 按钮区域
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(btn_frame, text="上一个 (←)", command=self._prev).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(
            btn_frame, text="保存并下一个 (→)", command=self._save_and_next
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="跳过 (Space)", command=self._next).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="删除 (Delete)", command=self._delete).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(
            btn_frame, text="保存退出 (Ctrl+S)", command=self._save_and_quit
        ).pack(side=tk.RIGHT, padx=5)

        # 快速标注按钮（从config.py读取支持的字符）
        quick_frame = ttk.LabelFrame(self.root, text="快速标注")
        quick_frame.pack(fill=tk.X, padx=10, pady=5)

        # 动态创建按钮，支持所有CLASS_NAMES中的字符
        for char in CLASS_NAMES:
            ttk.Button(
                quick_frame,
                text=char,
                width=3,
                command=lambda c=char: self._quick_label(c),
            ).pack(side=tk.LEFT, padx=2)

        # 键盘快捷键
        self.root.bind("<Left>", lambda e: self._prev())
        self.root.bind("<Right>", lambda e: self._save_and_next())
        self.root.bind("<space>", lambda e: self._next())
        self.root.bind("<Delete>", lambda e: self._delete())
        self.root.bind("<Control-s>", lambda e: self._save_and_quit())

        # 动态绑定所有字符的快捷键
        for char in CLASS_NAMES:
            self.root.bind(char, lambda e, c=char: self._quick_label(c))

    def _show_current(self):
        """显示当前图像"""
        if not self.to_verify or self.current_idx >= len(self.to_verify):
            self.info_label.config(text="✅ 所有样本已验证!")
            print("✅ 所有样本已验证!")
            return

        data = self.to_verify[self.current_idx]

        # 更新信息
        info_text = f"进度: {self.current_idx + 1}/{len(self.to_verify)} | 总数: {len(self.data)}"
        self.info_label.config(text=info_text)

        # 加载图像
        img_path = data["image_path"]
        img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)

        if img is None:
            self.info_label.config(text=f"❌ 无法加载图像: {img_path}")
            return

        # 放大显示
        scale = 10
        h, w = img.shape
        img_resized = cv2.resize(
            img, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST
        )

        # 转换为PIL格式
        img_pil = Image.fromarray(img_resized)
        self.photo = ImageTk.PhotoImage(img_pil)

        # 显示在Canvas中心
        self.canvas.delete("all")
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        x = max(0, (canvas_width - img_resized.shape[1]) // 2)
        y = max(0, (canvas_height - img_resized.shape[0]) // 2)
        self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo)

        # 更新标注输入
        self.label_var.set(data.get("label", ""))
        self.label_entry.focus()
        self.label_entry.select_range(0, tk.END)

        # 更新置信度
        confidence = data.get("confidence", 0.0)
        self.confidence_label.config(text=f"置信度: {confidence:.2%}")

    def _save_and_next(self):
        if not self.to_verify or self.current_idx >= len(self.to_verify):
            return

        data = self.to_verify[self.current_idx]
        new_label = self.label_var.get().strip()

        # 更新原始数据列表中的对应项
        for d in self.data:
            if d["image_path"] == data["image_path"]:
                d["label"] = new_label
                d["verified"] = True
                d["needs_verification"] = False
                break

        self.current_idx += 1
        self._show_current()

    def _quick_label(self, label):
        """快速标注"""
        self.label_var.set(label)
        self._save_and_next()

    def _next(self):
        """下一个（不保存）"""
        self.current_idx += 1
        self._show_current()

    def _prev(self):
        """上一个"""
        if self.current_idx > 0:
            self.current_idx -= 1
            self._show_current()

    def _delete(self):
        """删除当前标注"""
        if not self.to_verify or self.current_idx >= len(self.to_verify):
            return

        data = self.to_verify[self.current_idx]

        # 从原数据中删除
        self.data = [d for d in self.data if d["image_path"] != data["image_path"]]

        # 从待验证列表中删除
        self.to_verify.pop(self.current_idx)

        # 显示下一个（索引不变）
        self._show_current()

    def _save_and_quit(self):
        self._save_labels()
        print("✅ 标注已保存.")
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    labels_path = Path(DATA_DIR) / "labels.json"
    if not labels_path.exists():
        print(f"❌ 错误: 标注文件不存在 - {labels_path}")
        return

    print(f"\n{'='*80}")
    print("🔍 DeepAI 标注验证工具")
    print(f"{'='*80}")
    print("📝 工作模式:")
    print("   ✅ 高置信度样本 - 自动标记为已验证（无需人工检查）")
    print("   ⚠️  低置信度样本 - 需要人工验证")
    print("   ✗ 识别失败样本 - 需要人工标注")
    print(f"{'='*80}\n")

    verifier = LabelVerifier(str(labels_path))

    if not verifier.to_verify:
        print("✅ 所有样本都是高置信度，无需人工验证！")
        print("   可以直接进行模型训练。")
        return

    print(f"需要验证的样本数: {len(verifier.to_verify)}")
    print("开始验证...\n")

    verifier.run()


if __name__ == "__main__":
    main()
