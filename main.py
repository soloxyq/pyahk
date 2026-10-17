#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主程序入口"""

import sys
import traceback
from PySide6.QtWidgets import QApplication

from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.core.ahk_event_filter import AHKEventFilter
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
    macro_engine = None
    cleanup_done = False

    def cleanup_all():
        """正常退出和构造/事件循环异常共用同一确定性清理边界。"""
        nonlocal cleanup_done
        if cleanup_done or macro_engine is None:
            return
        cleanup_done = True
        try:
            LOG_INFO("开始清理所有资源...")
            macro_engine.cleanup()
            LOG_INFO("资源清理完成")
        except Exception as e:
            LOG_ERROR(f"清理资源时出错: {e}")
            traceback.print_exc()

    try:
        app = QApplication(sys.argv)
        app.aboutToQuit.connect(cleanup_all)

        # 安装全局 AHK 事件过滤器(替代 main_window / status_window 各自的 nativeEvent)
        # attach 到 app 防 GC,确保过滤器在整个应用生命周期内存活
        app._ahk_event_filter = AHKEventFilter()
        app.installNativeEventFilter(app._ahk_event_filter)

        sound_manager = SoundManager()
        macro_engine = MacroEngine(sound_manager=sound_manager)
        ui = GameSkillConfigUI(macro_engine, sound_manager)
        ui.show()

        exit_code = app.exec()
        LOG_INFO(f"应用程序正常退出，退出码: {exit_code}")
        return exit_code

    except Exception as e:
        LOG_ERROR(f"应用程序执行失败: {e}")
        traceback.print_exc()
        return 1
    finally:
        # UI 构造失败时尚未进入 app.exec，不会触发 aboutToQuit。
        cleanup_all()


if __name__ == "__main__":
    sys.exit(main())
