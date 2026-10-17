"""GUI thread ownership, target preservation and edit transaction regressions."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from torchlight_assistant.core.states import MacroState
from torchlight_assistant.gui import main_window
from torchlight_assistant.gui.config_widgets import WindowActivationWidget
from torchlight_assistant.gui.event_bridge import UiEventBridge
from torchlight_assistant.gui.skill_config_widget import SimplifiedSkillWidget
from torchlight_assistant.utils.window_utils import WindowUtils


class LocalBus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    def unsubscribe(self, name, handler):
        self.handlers[name].remove(handler)

    def publish(self, name, *args, **kwargs):
        for handler in list(self.handlers.get(name, [])):
            handler(*args, **kwargs)


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    for _ in range(3):
        application.processEvents()


def from_worker(callback):
    errors = []

    def run():
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(3)
    assert not thread.is_alive()
    assert not errors


def test_notifications_from_plain_worker_reach_gui_and_snapshot_payload(app):
    bus = LocalBus()
    bridge = UiEventBridge(bus)
    received = []
    bridge.subscribe_notification(
        "config", lambda payload: received.append((threading.get_ident(), payload))
    )
    payload = {"key": "q", "nested": [1]}
    from_worker(lambda: bus.publish("config", payload))
    payload["nested"].append(2)
    assert received == []
    app.processEvents()
    assert received == [(threading.get_ident(), {"key": "q", "nested": [1]})]
    bridge.close()


def test_commands_keep_gui_transactions_synchronous_and_queue_workers(app):
    bus = LocalBus()
    bridge = UiEventBridge(bus)
    received = []
    bridge.subscribe_command("f8", lambda: received.append(threading.get_ident()))
    bus.publish("f8")
    assert received == [threading.get_ident()]
    from_worker(lambda: bus.publish("f8"))
    assert len(received) == 1
    app.processEvents()
    assert received == [threading.get_ident()] * 2
    bridge.close()


def test_close_discards_pending_notifications_and_unsubscribes(app):
    bus = LocalBus()
    bridge = UiEventBridge(bus)
    callback = Mock()
    bridge.subscribe_notification("event", callback)
    from_worker(lambda: bus.publish("event"))
    bridge.close()
    bridge.close()
    app.processEvents()
    callback.assert_not_called()
    assert bus.handlers["event"] == []


class MinimalWindow(main_window.GameSkillConfigUI):
    """Real Qt affinity and actual subscriptions without an AHK/game engine."""

    def __init__(self):
        QMainWindow.__init__(self)
        self.state = MacroState.RUNNING
        self.macro_engine = SimpleNamespace(
            _runtime_start_epoch=1,
            get_current_state=lambda: self.state,
            affix_reroll_manager=SimpleNamespace(_run_generation=1),
        )
        self.delivered = []
        self._connect_to_engine()

    def _perform_macro_state_changed_ui(self, state):
        self.delivered.append(("state", state, threading.get_ident()))

    def _perform_macro_status_updated_ui(self, status):
        self.delivered.append(("status", status["state"], threading.get_ident()))

    def _on_config_update(self, skills, config):
        self.delivered.append(("config", config, threading.get_ident()))

    def _on_ocr_init_success(self):
        self.delivered.append(("ocr", None, threading.get_ident()))

    def _on_affix_reroll_status_updated(self, status):
        self.delivered.append(("affix", status, threading.get_ident()))


def test_main_window_notification_subscriptions_filter_old_run(app, monkeypatch):
    bus = LocalBus()
    monkeypatch.setattr(main_window, "event_bus", bus)
    window = MinimalWindow()
    try:
        def publish_old_run():
            bus.publish("engine:state_changed", MacroState.RUNNING, MacroState.READY)
            bus.publish("engine:status_updated", {"state": MacroState.RUNNING})

        from_worker(publish_old_run)
        window.macro_engine._runtime_start_epoch = 2
        for _ in range(3):
            app.processEvents()
        assert window.delivered == []
        from_worker(lambda: bus.publish("engine:status_updated", {"state": MacroState.RUNNING}))
        from_worker(lambda: bus.publish("engine:config_updated", {}, {"sample": 1}))
        from_worker(lambda: bus.publish("ocr:init_success"))
        from_worker(lambda: bus.publish("affix_reroll:status_updated", {"is_running": True}))
        app.processEvents()
        assert [item[0] for item in window.delivered] == ["status", "config", "ocr", "affix"]
        assert all(item[2] == threading.get_ident() for item in window.delivered)
        window.delivered.clear()
        from_worker(lambda: bus.publish("affix_reroll:status_updated", {"is_running": False}))
        window.macro_engine.affix_reroll_manager._run_generation += 1
        app.processEvents()
        assert window.delivered == []
    finally:
        window.close()


@pytest.fixture
def target_ui(app, monkeypatch):
    processes = [SimpleNamespace(info={"name": name}) for name in ("AEditor.exe", "MuMuNxDevice.exe")]
    monkeypatch.setattr("psutil.process_iter", lambda *_: processes)
    probe = Mock(return_value="DifferentDetectedWindow")
    monkeypatch.setattr(WindowUtils, "get_window_class_by_process", probe)
    widget = WindowActivationWidget()
    ui = SimpleNamespace(window_activation=widget, _updating_ui=False)
    widget.widgets["exe"].currentTextChanged.connect(
        lambda text: main_window.GameSkillConfigUI._on_process_selection_changed(ui, text)
    )
    yield ui, widget, probe
    widget.close()


@pytest.mark.parametrize("exe", ["", "MuMuNxDevice.exe", "NotRunning.exe"])
def test_refresh_only_changes_candidates_not_target(target_ui, exe):
    ui, widget, probe = target_ui
    original = {"enabled": True, "ahk_class": "SpecificWindow", "ahk_exe": exe}
    widget.update_from_config({"window_activation": original})
    main_window.GameSkillConfigUI._populate_process_list(ui)
    assert widget.get_config()["window_activation"] == original
    probe.assert_not_called()


@pytest.mark.parametrize("handles", [[], [1, 2]])
def test_mumu_missing_or_ambiguous_preserves_target(target_ui, monkeypatch, handles):
    ui, widget, probe = target_ui
    original = {"enabled": True, "ahk_class": "OriginalWindow", "ahk_exe": "Original.exe"}
    widget.update_from_config({"window_activation": original})
    monkeypatch.setattr(WindowUtils, "find_windows_by_criteria", lambda **_: handles)
    main_window.GameSkillConfigUI._set_mumu_config(ui)
    assert widget.get_config()["window_activation"] == original
    probe.assert_not_called()


def test_mumu_uses_unique_actual_device_window(target_ui, monkeypatch):
    ui, widget, probe = target_ui
    finder = Mock(return_value=[123])
    monkeypatch.setattr(WindowUtils, "find_windows_by_criteria", finder)
    monkeypatch.setattr("win32gui.GetClassName", lambda hwnd: "Qt5156QWindowIcon")
    main_window.GameSkillConfigUI._set_mumu_config(ui)
    finder.assert_called_once_with(ahk_exe="MuMuNxDevice.exe")
    target = widget.get_config()["window_activation"]
    assert (target["ahk_class"], target["ahk_exe"]) == ("Qt5156QWindowIcon", "MuMuNxDevice.exe")
    probe.assert_not_called()


def test_invalid_skill_field_blocks_collection_instead_of_reenabling(app):
    original = {"Enabled": True, "Key": "q", "Timer": 195, "TriggerMode": 0}
    widget = SimplifiedSkillWidget(None, "Skill1", original, None)
    try:
        widget._ui_widgets["Enabled"].setChecked(False)
        widget._ui_widgets["Key"].setText("w")
        widget._ui_widgets["Timer"].setText("200ms")
        with pytest.raises(ValueError, match="Skill1.*Timer"):
            widget.get_current_config()
        # Enum visibility changes must still work while numbers are incomplete.
        widget._ui_widgets["TriggerModeCombo"].setCurrentIndex(1)
        widget._ui_widgets["Timer"].setText("200")
        result = widget.get_current_config()
        assert result["Enabled"] is False
        assert (result["Key"], result["Timer"], result["TriggerMode"]) == ("w", 200, 1)
    finally:
        widget.close()


@pytest.mark.parametrize("operation", ["f8", "save"])
def test_invalid_skill_edit_never_publishes_start_or_save(app, monkeypatch, operation):
    widget = SimplifiedSkillWidget(
        None, "Skill1", {"Enabled": True, "Key": "q", "Timer": 195, "TriggerMode": 0}, None
    )
    widget._ui_widgets["Enabled"].setChecked(False)
    widget._ui_widgets["Timer"].setText("200ms")
    published = Mock()
    monkeypatch.setattr(main_window, "event_bus", SimpleNamespace(publish=published))
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", lambda *_: ("unused.json", ""))
    errors = Mock()
    monkeypatch.setattr(main_window.QMessageBox, "critical", errors)
    pending = []
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda _ms, callback: pending.append(callback))
    ui = SimpleNamespace(
        macro_engine=SimpleNamespace(get_current_state=lambda: MacroState.STOPPED),
        _gather_current_config_from_ui=lambda: {"skills": {"Skill1": widget.get_current_config()}},
    )
    try:
        if operation == "f8":
            main_window.GameSkillConfigUI._toggle_visibility_and_macro(ui)
            assert len(pending) == 1
            pending[0]()
        else:
            main_window.GameSkillConfigUI._save_config_file(ui)
        published.assert_not_called()
        assert "Timer" in errors.call_args.args[2]
    finally:
        widget.close()
