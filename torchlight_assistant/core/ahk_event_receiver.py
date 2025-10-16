"""
AHK事件接收器 - 临时恢复文件监控用于调试
"""

import os
import time
import threading
from typing import Optional

from torchlight_assistant.core.signal_bridge import ahk_signal_bridge


class AHKEventReceiver:
    """临时的文件监控事件接收器"""

    def __init__(self, event_file: str = "ahk_events.txt"):
        self.event_file = event_file
        self.running = False
        self.receiver_thread: Optional[threading.Thread] = None

    def start(self):
        """启动事件接收器"""
        if self.running:
            return

        self.running = True
        self.receiver_thread = threading.Thread(
            target=self._run_receiver, daemon=True, name="AHKEventReceiver"
        )
        self.receiver_thread.start()
        print("[AHK事件] 文件监控接收器已启动（调试模式）")

    def stop(self):
        """停止事件接收器"""
        if not self.running:
            return

        self.running = False
        if self.receiver_thread and self.receiver_thread != threading.current_thread():
            self.receiver_thread.join(timeout=2)
        print("[AHK事件] 文件监控接收器已停止")

    def _run_receiver(self):
        """运行事件接收循环"""
        while self.running:
            try:
                if os.path.exists(self.event_file):
                    events = []

                    try:
                        with open(self.event_file, "r", encoding="utf-8") as f:
                            events = f.readlines()
                    except:
                        pass

                    if events:
                        try:
                            os.remove(self.event_file)
                        except:
                            pass

                        for event in events:
                            event = event.strip()
                            if event:
                                print(f"[文件事件] 收到: {event}")
                                ahk_signal_bridge.ahk_event.emit(event)

                time.sleep(0.01)

            except Exception as e:
                print(f"[AHK事件] 文件监控错误: {e}")
                time.sleep(0.1)

    def get_stats(self) -> dict:
        return {"debug_mode": True}
