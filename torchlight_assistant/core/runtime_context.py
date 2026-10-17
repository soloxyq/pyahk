"""运行中计算的共同边界：计算可迟到，提交必须仍属于当前生产阶段。"""

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ProductionToken:
    run_epoch: int
    phase_epoch: int
    config_revision: int
    source: object = field(repr=False)


class ProductionContext:
    """在同一把锁下发布 token、失效旧计算、提交有界输入操作。

    调用方在 ``lock`` 内取得 token/配置快照，耗时检测放到锁外，最后在锁内
    ``accepts`` 后发送。停止不必等待 OCR；暂停恢复、换配置都会废弃旧结果。
    """

    def __init__(self, *, running: bool = False, paused: bool = False):
        self.lock = threading.RLock()
        self._run_epoch = 0
        self._phase_epoch = 0
        self._config_revision = 0
        self._source = object()
        self._running = running
        self._paused = paused

    def start(self):
        with self.lock:
            self._run_epoch += 1
            self._phase_epoch += 1
            self._running = True
            self._paused = False

    def stop(self):
        with self.lock:
            self._phase_epoch += 1
            self._running = False
            self._paused = False

    def pause(self):
        with self.lock:
            self._phase_epoch += 1
            self._paused = True

    def resume(self):
        with self.lock:
            self._phase_epoch += 1
            self._paused = False

    def reconfigure(self):
        with self.lock:
            self._config_revision += 1

    def capture(self) -> Optional[ProductionToken]:
        with self.lock:
            if not self._running or self._paused:
                return None
            return ProductionToken(
                self._run_epoch, self._phase_epoch, self._config_revision,
                self._source,
            )

    def accepts(self, token: Optional[ProductionToken]) -> bool:
        with self.lock:
            return token is not None and token == self.capture()
