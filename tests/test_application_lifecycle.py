"""Application composition cleans up even before the Qt event loop starts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main as application


@pytest.mark.parametrize("failure", ["ui", "event_loop", None])
def test_application_has_one_cleanup_boundary(monkeypatch, failure):
    cleanup = Mock()
    about_to_quit = []

    def run_loop():
        if failure == "event_loop":
            raise RuntimeError("event loop failed")
        for callback in about_to_quit:
            callback()
        return 0

    app = SimpleNamespace(
        aboutToQuit=SimpleNamespace(connect=about_to_quit.append),
        installNativeEventFilter=lambda _: None,
        exec=run_loop,
    )

    def build_ui(*_args):
        if failure == "ui":
            raise RuntimeError("UI construction failed")
        return SimpleNamespace(show=lambda: None)

    # Restore the global hook when the fixture is torn down.
    monkeypatch.setattr(application.sys, "excepthook", application.sys.excepthook)
    monkeypatch.setattr(application, "QApplication", lambda _args: app)
    monkeypatch.setattr(application, "AHKEventFilter", lambda: object())
    monkeypatch.setattr(application, "SoundManager", lambda: object())
    monkeypatch.setattr(application, "MacroEngine", lambda **_: SimpleNamespace(cleanup=cleanup))
    monkeypatch.setattr(application, "GameSkillConfigUI", build_ui)
    assert application.main() == (1 if failure else 0)
    cleanup.assert_called_once_with()


@pytest.mark.parametrize("failure", ["border", "resource", "skill"])
def test_partial_engine_constructor_cleans_completed_components(monkeypatch, failure):
    """main cannot receive a half-built engine, so its constructor must unwind."""
    from torchlight_assistant.core import macro_engine as core
    from torchlight_assistant.core import resource_manager as resources

    app = SimpleNamespace(
        aboutToQuit=SimpleNamespace(connect=lambda _callback: None),
        installNativeEventFilter=lambda _filter: None,
    )
    ahk = SimpleNamespace(cleanup=Mock())
    border = SimpleNamespace(cleanup=Mock())
    resource = SimpleNamespace(
        cleanup=Mock(), stop=Mock(), check_interval=200,
        check_and_execute_resources=Mock(),
    )
    created = []

    def construct(name, component):
        def build(*_args, **_kwargs):
            if failure == name:
                raise RuntimeError(f"{name} initialization failed")
            created.append(component)
            return component

        return build

    monkeypatch.setattr(application.sys, "excepthook", application.sys.excepthook)
    monkeypatch.setattr(application, "QApplication", lambda _args: app)
    monkeypatch.setattr(application, "AHKEventFilter", lambda: object())
    monkeypatch.setattr(application, "SoundManager", lambda: object())
    build_ui = Mock()
    monkeypatch.setattr(application, "GameSkillConfigUI", build_ui)
    monkeypatch.setattr(core, "AHKInputHandler", construct("ahk", ahk))
    monkeypatch.setattr(core, "BorderFrameManager", construct("border", border))
    monkeypatch.setattr(resources, "ResourceManager", construct("resource", resource))
    monkeypatch.setattr(core, "SkillManager", construct("skill", None))
    monkeypatch.setattr(
        core.MacroEngine, "_resolve_initial_config_file", lambda _self, path: path
    )

    assert application.main() == 1
    build_ui.assert_not_called()
    assert created
    for component in created:
        component.cleanup.assert_called_once_with()
