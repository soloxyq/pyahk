"""Deliver EventBus events to an explicitly owned Qt thread."""

from copy import deepcopy
from functools import partial

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot

from ..utils.debug_log import LOG_ERROR


class UiEventBridge(QObject):
    """Queue notifications; preserve synchronous GUI command transactions.

    EventBus callbacks may run on any Python thread. Only this QObject's slots
    invoke UI handlers. AutoConnection keeps GUI-originated commands (notably
    F8 configuration collection) synchronous and queues worker-originated ones.
    """

    _notification = Signal(object)
    _command = Signal(object)

    def __init__(self, event_bus, parent=None):
        super().__init__(parent)
        self._event_bus = event_bus
        self._subscriptions = []
        self._closed = False
        self._notification.connect(self._deliver, Qt.QueuedConnection)
        self._command.connect(self._deliver, Qt.AutoConnection)
        self._application = QCoreApplication.instance()
        if self._application is not None:
            self._application.aboutToQuit.connect(self.close)

    @property
    def closed(self):
        return self._closed

    def subscribe_notification(self, name, handler, *, guard_factory=None):
        self._subscribe(name, handler, self._notification, guard_factory)

    def subscribe_command(self, name, handler):
        self._subscribe(name, handler, self._command, None)

    def _subscribe(self, name, handler, signal, guard_factory):
        callback = partial(self._post, handler, signal, guard_factory)
        self._subscriptions.append((name, callback))
        self._event_bus.subscribe(name, callback)

    def _post(self, handler, signal, guard_factory, *args, **kwargs):
        if self._closed:
            return
        # Publishers may reuse/mutate dictionaries after publish returns.
        guard = guard_factory() if guard_factory is not None else None
        signal.emit((handler, deepcopy(args), deepcopy(kwargs), guard))

    @Slot(object)
    def _deliver(self, envelope):
        if self._closed:
            return
        handler, args, kwargs, guard = envelope
        try:
            if guard is None or guard():
                handler(*args, **kwargs)
        except Exception as exc:
            LOG_ERROR(f"[UI事件] {getattr(handler, '__name__', type(handler).__name__)} 处理失败: {exc}")

    def close(self):
        """Reject queued deliveries as well as future EventBus publications."""
        if self._closed:
            return
        self._closed = True
        for name, callback in self._subscriptions:
            self._event_bus.unsubscribe(name, callback)
        self._subscriptions.clear()
        if self._application is not None:
            try:
                self._application.aboutToQuit.disconnect(self.close)
            except (RuntimeError, TypeError):
                pass
            self._application = None
