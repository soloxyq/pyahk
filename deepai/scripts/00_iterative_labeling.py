#!/usr/bin/env python
# -*- coding: utf-8 -*-
# pylint: disable=invalid-name
"""迭代式标注流程 - 主动学习控制中心"""

import os
import sys
from pathlib import Path
import json
import subprocess

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from deepai.config import DATA_DIR


def get_label_stats():
    """获取并打印详细的标注统计信息"""
    labels_path = Path(DATA_DIR) / "labels.json"
    if not labels_path.exists():
        return None

    with open(labels_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total = len(data)
    if total == 0:
        return None

    labeled = sum(1 for d in data if d.get("label") and d.get("label").strip())
    verified = sum(1 for d in data if d.get("verified", False))
    needs_verify = sum(1 for d in data if d.get("needs_verification", False))
    high_conf = sum(1 for d in data if d.get("confidence", 0) >= 0.95)

    stats = {
        "total": total,
        "labeled": labeled,
        "verified": verified,
        "needs_verify": needs_verify,
        "high_conf": high_conf,
        "unlabeled": total - labeled,
    }

    print(f"\n📊 当前标注状态:")
    print(f"  总样本数: {stats['total']}")
    print(f"  已标注: {stats['labeled']} ({stats['labeled']/stats['total']:.1%})")
    print(f"  人工验证: {stats['verified']} ({stats['verified']/stats['total']:.1%})")
    print(f"  待验证 (模型预标注): {stats['needs_verify']}")
    print(f"  高置信度 (≥95%): {stats['high_conf']}")
    return stats


def run_script(script_path):
    """运行一个外部Python脚本"""
    # 确保使用绝对路径
    script_path = Path(script_path).resolve()
    project_root = Path(__file__).resolve().parent.parent.parent

    cmd = [sys.executable, str(script_path)]
    print(f"\n{'='*80}")
    print(f"🚀 正在运行: {script_path.name}")
    print(f"{'='*80}\n")

    # 在项目根目录下运行，确保能找到 deepai 模块
    subprocess.run(cmd, cwd=str(project_root), check=True)


def relabel_with_model():
    """使用训练好的模型进行主动学习标注"""
    print("🤖 开始使用模型进行主动学习标注...")
    try:
        from deepai.recognizer import KerasDigitRecognizer
        import cv2
    except ImportError as e:
        print(f"❌ 导入模块失败: {e}。请确保已安装所有依赖。")
        return

    # 强制重新加载最新模型
    recognizer = KerasDigitRecognizer()
    print("🔄 重新加载最新训练的模型...")
    if not recognizer.initialize(force_reload=True):
        print("❌ 模型初始化失败，无法进行重新标注。")
        return

    print("✅ 已加载最新模型")

    labels_path = Path(DATA_DIR) / "labels.json"
    with open(labels_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    to_relabel = [d for d in data if not d.get("verified", False)]
    print(f"将使用模型对 {len(to_relabel)} 个未经验证的样本进行预标注...")
    print()

    labeled_count = 0
    low_confidence_count = 0

    for i, item in enumerate(to_relabel):
        img = cv2.imread(item["image_path"], cv2.IMREAD_GRAYSCALE)
        if img is None:
            # 输出加载失败
            img_name = os.path.basename(item["image_path"])
            print(f"  [{i+1:4d}/{len(to_relabel)}] ✗ {img_name:30s} → (加载失败)")
            continue

        label, conf = recognizer.recognize_digit(img)
        item["label"] = label
        item["confidence"] = conf
        item["needs_verification"] = conf < 0.95
        item["relabeled_by_model"] = True

        # 输出识别结果
        img_name = os.path.basename(item["image_path"])
        status = "✓" if conf >= 0.95 else "?"
        if label:
            print(
                f"  [{i+1:4d}/{len(to_relabel)}] {status} {img_name:30s} → '{label}' ({conf:.1%})"
            )
            labeled_count += 1
            if conf < 0.95:
                low_confidence_count += 1
        else:
            print(f"  [{i+1:4d}/{len(to_relabel)}] ✗ {img_name:30s} → (识别失败)")

    with open(labels_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print()
    print(f"✅ 重新标注完成!")
    print(f"   已标注: {labeled_count}, 需要验证: {low_confidence_count}")
    print(f"   现在可以运行 '人工验证标注' 来检查低置信度的样本。")


def main():
    """主交互菜单"""
    scripts_dir = Path(__file__).parent

    while True:
        get_label_stats()
        print("\n--- 迭代式AI训练工作流 ---")
        print("1a. 从视频生成数字图像 (提取帧 → 裁剪 → 分割)")
        print("1b. Tesseract自动标注 (对生成的图像进行初始标注)")
        print("2.  人工验证标注 (核心步骤，提升模型精度)")
        print("3.  训练模型 (当有足够已验证的标注时)")
        print("4.  模型辅助标注 (用训练好的模型加速标注)")
        print("5.  评估模型 (查看模型性能和准确率)")
        print("6.  生成最终产物 (评估模型 + 生成模板)")
        print("0.  退出")
        choice = input("\n请选择您的操作: ").strip()

        try:
            if choice == "1a":
                # 只运行数据生成，不进行自动标注
                print("\n💡 提示: 此步骤只生成数字图像，不进行标注")
                print("   完成后请运行 '1b. Tesseract自动标注'")
                if input("确认继续? [y/N]: ").lower() == "y":
                    run_script(scripts_dir / "01a_generate_digits.py")
            elif choice == "1b":
                # 只运行自动标注
                labels_path = Path(DATA_DIR) / "labels.json"
                if not labels_path.exists():
                    print("❌ 错误: 未找到数字图像数据")
                    print("   请先运行 '1a. 从视频生成数字图像'")
                    continue
                run_script(scripts_dir / "01b_auto_label.py")
            elif choice == "2":
                run_script(scripts_dir / "02_verify_labels.py")
            elif choice == "3":
                run_script(scripts_dir / "03_train_model.py")
            elif choice == "4":
                relabel_with_model()
            elif choice == "5":
                # 只评估模型
                run_script(scripts_dir / "04_evaluate_model.py")
            elif choice == "6":
                # 生成最终产物
                print("将依次执行: 评估模型 -> 生成模板")
                if input("确认? [y/N]: ").lower() == "y":
                    run_script(scripts_dir / "04_evaluate_model.py")
                    run_script(scripts_dir / "06_generate_digit_templates.py")
            elif choice == "0":
                print("👋 已退出.")
                break
            else:
                print("❌ 无效选项，请重试。")
        except (Exception, KeyboardInterrupt) as e:
            print(f"\n❌ 操作被中断或发生错误: {e}")


if __name__ == "__main__":
    main()
