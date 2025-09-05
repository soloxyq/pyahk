#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主程序入口"""

import sys
from PySide6.QtWidgets import QApplication

from torchlight_assistant.core.macro_engine import MacroEngine
from torchlight_assistant.utils.hotkey_manager import CtypesHotkeyManager
from torchlight_assistant.utils.sound_manager import SoundManager
from torchlight_assistant.gui.main_window import GameSkillConfigUI
from torchlight_assistant.utils.debug_log import LOG_INFO


def main():
    """主函数"""
    app = QApplication(sys.argv)

    hotkey_listener = CtypesHotkeyManager()
    sound_manager = SoundManager()
    macro_engine = MacroEngine(hotkey_manager=hotkey_listener, sound_manager=sound_manager)
    ui = GameSkillConfigUI(hotkey_listener, macro_engine, sound_manager)
    ui.show()

    def cleanup_all():
        """清理所有资源"""
        try:
            macro_engine.cleanup()
        except Exception as e:
            print(f"清理资源时出错: {e}")

    app.aboutToQuit.connect(cleanup_all)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()