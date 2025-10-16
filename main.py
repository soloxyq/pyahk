#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主程序入口"""

import sys
import traceback
from PySide6.QtWidgets import QApplication

from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.utils.sound_manager import SoundManager
from torchlight_assistant.gui.main_window import GameSkillConfigUI
from torchlight_assistant.utils.debug_log import LOG_INFO, LOG_ERROR


def global_exception_handler(exctype, value, tb):
    """全局异常处理器"""
    LOG_ERROR("未捕获的异常:")
    LOG_ERROR(f"异常类型: {exctype.__name__}")
    LOG_ERROR(f"异常信息: {value}")
    LOG_ERROR("异常追踪:")
    for line in traceback.format_exception(exctype, value, tb):
        LOG_ERROR(line.strip())
    LOG_ERROR("=" * 50)


def main():
    """主函数"""
    # 设置全局异常处理器
    sys.excepthook = global_exception_handler

    try:
        app = QApplication(sys.argv)

        sound_manager = SoundManager()
        macro_engine = MacroEngine(sound_manager=sound_manager)
        ui = GameSkillConfigUI(macro_engine, sound_manager)
        ui.show()

        def cleanup_all():
            """清理所有资源"""
            try:
                LOG_INFO("开始清理所有资源...")
                macro_engine.cleanup()
                LOG_INFO("资源清理完成")
            except Exception as e:
                LOG_ERROR(f"清理资源时出错: {e}")
                traceback.print_exc()

        app.aboutToQuit.connect(cleanup_all)

        # 添加异常处理
        try:
            exit_code = app.exec()
            LOG_INFO(f"应用程序正常退出，退出码: {exit_code}")
            sys.exit(exit_code)
        except Exception as e:
            LOG_ERROR(f"应用程序执行异常: {e}")
            traceback.print_exc()
            sys.exit(1)

    except Exception as e:
        LOG_ERROR(f"应用程序初始化失败: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()