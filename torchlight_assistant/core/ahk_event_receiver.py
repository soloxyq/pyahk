"""
AHK事件接收器
负责接收AHK发送的事件并分发到EventBus
"""

import os
import time
import threading
from typing import Optional

from torchlight_assistant.core.signal_bridge import ahk_signal_bridge


class AHKEventReceiver:
    """
    AHK事件接收器
    
    职责:
    - 监控ahk_events.txt文件
    - 解析事件并通过Qt信号发射，确保线程安全
    """
    
    def __init__(self, event_file: str = "ahk_events.txt"):
        self.event_file = event_file
        self.running = False
        self.receiver_thread: Optional[threading.Thread] = None
        
        # 统计信息
        self.events_received = 0
        self.events_processed = 0
        self.events_failed = 0
        
        # 确保事件文件目录存在且可写
        self._ensure_file_access()
    
    def start(self):
        """启动事件接收器"""
        if self.running:
            return
        
        self.running = True
        self.receiver_thread = threading.Thread(
            target=self._run_receiver,
            daemon=True,
            name="AHKEventReceiver"
        )
        self.receiver_thread.start()
        print("[AHK事件] 接收器已启动")
    
    def stop(self):
        """停止事件接收器"""
        if not self.running:
            return
        
        self.running = False
        # 只在非当前线程时才join
        if self.receiver_thread and self.receiver_thread != threading.current_thread():
            self.receiver_thread.join(timeout=2)
        print("[AHK事件] 接收器已停止")
    
    def _run_receiver(self):
        """运行事件接收循环"""
        print(f"[AHK事件] 事件接收器已启动，监控文件: {self.event_file}")
        while self.running:
            try:
                # 检查事件文件是否存在
                if os.path.exists(self.event_file):
                    events = []
                    
                    # 尝试读取文件，带重试机制
                    for attempt in range(3):
                        try:
                            with open(self.event_file, "r", encoding="utf-8") as f:
                                events = f.readlines()
                            break
                        except PermissionError:
                            if attempt < 2:
                                time.sleep(0.005)  # 等待5ms后重试
                                continue
                            else:
                                # 最后一次尝试失败，跳过这次处理
                                break
                        except Exception as e:
                            print(f"[AHK事件] 读取文件错误: {e}")
                            break

                    # 尝试删除文件，带重试机制
                    if events:
                        for attempt in range(3):
                            try:
                                os.remove(self.event_file)
                                break
                            except PermissionError:
                                if attempt < 2:
                                    time.sleep(0.005)  # 等待5ms后重试
                                    continue
                                else:
                                    # 删除失败，但不影响事件处理
                                    break
                            except Exception:
                                break

                        # 处理事件
                        self.events_received += len(events)
                        for event in events:
                            event = event.strip()
                            if event:
                                # 使用Qt信号发射事件，而不是直接发布
                                ahk_signal_bridge.ahk_event.emit(event)
                                self.events_processed += 1

                # 短暂休眠
                time.sleep(0.01)  # 10ms检查一次

            except Exception as e:
                print(f"[AHK事件] 接收错误: {e}")
                self.events_failed += 1
                time.sleep(0.1)
    
    def _ensure_file_access(self):
        """确保事件文件可以正常访问"""
        try:
            # 如果文件存在且被锁定，尝试删除
            if os.path.exists(self.event_file):
                try:
                    # 尝试打开文件进行写入测试
                    with open(self.event_file, "a", encoding="utf-8") as f:
                        pass
                except PermissionError:
                    # 文件被锁定，尝试重命名后删除
                    import time
                    backup_name = f"{self.event_file}.backup_{int(time.time())}"
                    try:
                        os.rename(self.event_file, backup_name)
                        os.remove(backup_name)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[AHK事件] 文件访问检查失败: {e}")

    def get_stats(self) -> dict:
        """获取接收器统计信息"""
        return {
            "received": self.events_received,
            "processed": self.events_processed,
            "failed": self.events_failed,
            "success_rate": (
                self.events_processed / self.events_received * 100
                if self.events_received > 0 else 0
            )
        }
