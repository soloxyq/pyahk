"""Single-owner command transport with bounded admission and native-only waits.

AHK's WM_COPYDATA callback is not reentrant. All callers for one server share
this mailbox; only its worker performs the synchronous wire call. The public
result remains synchronous so existing state transactions retain their commit
boundary. Waiting GUI callers service sent Win32 messages, never Qt tasks.
"""

from collections import deque
from dataclasses import dataclass, field
from threading import Condition, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Callable, Optional, Tuple

from hold_client import SEND_ERROR, SEND_REJECTED, SEND_TIMEOUT, pump_sent_messages


SendResult = Tuple[bool, str]


class TransportCircuit:
    """One failure/recovery state for every facade of the same wire owner."""

    def __init__(self):
        self.lock = Lock()
        self.failure_kind = ""
        self.next_probe_at = 0.0
        self.probe_in_progress = False


@dataclass
class _Command:
    operation: Callable[[], SendResult]
    queue_deadline: float
    safety: bool = False
    done: Event = field(default_factory=Event)
    started_at: Optional[float] = None
    result: SendResult = (False, SEND_REJECTED)
    cancelled: bool = False


class CommandTransport:
    """Serialize one AHK server without holding a caller's lock across IPC."""

    MAX_PENDING = 64
    SAFETY_RESERVE = 4
    QUEUE_TIMEOUT_SECONDS = 0.5
    # The wire call itself has a 500ms deadline. A small dispatch allowance
    # avoids reporting a timeout while its completed result is being published.
    WIRE_WAIT_SECONDS = 0.55
    IDLE_TIMEOUT_SECONDS = 30.0

    def __init__(self):
        self.circuit = TransportCircuit()
        self._condition = Condition()
        self._ordinary = deque()
        self._safety = deque()
        self._worker: Optional[Thread] = None

    def send(
        self,
        operation: Callable[[], SendResult],
        *,
        safety: bool = False,
        barrier: bool = False,
    ) -> SendResult:
        command = _Command(operation, monotonic() + self.QUEUE_TIMEOUT_SECONDS, safety)
        with self._condition:
            # A transport operation must never synchronously submit to itself.
            if current_thread() is self._worker:
                return False, SEND_ERROR
            if barrier:
                # A stop must not be followed by ordinary work admitted before
                # that stop. Cancel before prioritizing the safety command.
                while self._ordinary:
                    self._finish_cancelled(self._ordinary.popleft())
            pending = len(self._ordinary) + len(self._safety)
            limit = self.MAX_PENDING if safety else self.MAX_PENDING - self.SAFETY_RESERVE
            if pending >= limit:
                return False, SEND_ERROR if safety else SEND_REJECTED
            (self._safety if safety else self._ordinary).append(command)
            if self._worker is None:
                self._worker = Thread(
                    target=self._run, name="AHKCommandTransport", daemon=True
                )
                self._worker.start()
            self._condition.notify()

        while not command.done.is_set():
            # Event.wait alone would block the GUI's reverse WM_COPYDATA and
            # recreate a cross-process wait cycle. PeekMessage processes only
            # sent messages; queued Qt slots/timers stay outside this transaction.
            pump_sent_messages()
            with self._condition:
                if command.done.is_set():
                    break
                now = monotonic()
                if command.started_at is None:
                    deadline = command.queue_deadline
                    if now >= deadline:
                        queue = self._safety if safety else self._ordinary
                        try:
                            queue.remove(command)
                        except ValueError:
                            pass
                        self._finish_cancelled(command, expired=True)
                        return command.result
                else:
                    deadline = command.started_at + self.WIRE_WAIT_SECONDS
                    if now >= deadline:
                        # In-flight delivery is uncertain. Never retry it or
                        # permit a second worker to overlap the unfinished call.
                        return False, SEND_TIMEOUT
            command.done.wait(min(0.005, max(0.0, deadline - monotonic())))
        return command.result

    @staticmethod
    def _finish_cancelled(command: _Command, *, expired: bool = False) -> None:
        command.cancelled = True
        kind = SEND_TIMEOUT if expired and command.safety else SEND_REJECTED
        command.result = (False, kind)
        command.done.set()

    def _run(self):
        while True:
            with self._condition:
                if not self._safety and not self._ordinary:
                    self._condition.wait(self.IDLE_TIMEOUT_SECONDS)
                    if not self._safety and not self._ordinary:
                        self._worker = None
                        return
                queue = self._safety if self._safety else self._ordinary
                command = queue.popleft()
                if command.cancelled or monotonic() >= command.queue_deadline:
                    self._finish_cancelled(command, expired=True)
                    continue
                command.started_at = monotonic()
            try:
                result = command.operation()
            except Exception:
                result = (False, SEND_ERROR)
            with self._condition:
                command.result = result
                command.done.set()


_transports = {}
_transports_lock = Lock()


def get_command_transport(window_title: str) -> CommandTransport:
    """Multiple sender facades targeting the same server share one wire owner."""
    with _transports_lock:
        transport = _transports.get(window_title)
        if transport is None:
            transport = _transports[window_title] = CommandTransport()
        return transport
