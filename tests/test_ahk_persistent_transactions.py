"""Real AHK timer/WM_COPYDATA contracts; every input edge is replaced by a log.

No production hotkeys are registered and no foreground window is activated.
Unlike function-only stubs these tests exercise the real sent-message reentry.
"""

import ctypes
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

import pytest

from hold_client import send_ahk_cmd_ex
from torchlight_assistant.config.ahk_commands import (
    CMD_PING, CMD_SET_ACCEPTING_ACTIONS, CMD_SET_MACRO_STEPS,
    CMD_START_MACRO, CMD_SET_SKILL_HOLD_KEYS,
)


ROOT = Path(__file__).resolve().parents[1]
AHK = next((path for path in (
    Path(r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"),
    Path(r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe"),
    Path(r"D:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"),
    Path(r"D:\Program Files\AutoHotkey\v2\AutoHotkey.exe"),
) if path.is_file()), None)
pytestmark = pytest.mark.skipif(AHK is None, reason="AutoHotkey v2 required")


def _function(source, name):
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if re.match(rf"^{name}\(.*\)\s*\{{\s*$", line))
    depth = 0
    result = []
    for line in lines[start:]:
        result.append(line)
        code = re.sub(r";.*$", "", line)
        depth += code.count("{") - code.count("}")
        if depth == 0:
            return "\n".join(result)
    raise AssertionError(name)


@pytest.fixture
def isolated_server(tmp_path):
    source = (ROOT / "hold_server_extended.ahk").read_text(encoding="utf-8")
    title = "pyahk_transaction_probe_" + uuid.uuid4().hex
    source = source.replace("HoldServer_Window_UniqueName_12345", title)
    source = source.replace("#SingleInstance Force", "#SingleInstance Off")
    source = source.replace("#Include ahk_commands.ahk",
                            f'#Include {ROOT / "ahk_commands.ahk"}')
    source = re.sub(r'^Hotkey\("\$0.*$', '', source, flags=re.MULTILINE)
    source = source.replace(_function(source, "SendTransientKeyEdge"), r'''
SendTransientKeyEdge(mode, target, key, isDown) {
    FileAppend((isDown ? "down:" : "up:") key "`n", A_ScriptDir "\edges.txt")
    if (isDown) {
        ; WM_COPYDATA interrupts this timer despite Critical.
        Sleep 180
    }
    return true
}'''.strip())
    # Only this independent burst's physical sink is replaced; its gate/start/
    # scheduling and fixed-count implementation remain the production source.
    source = source.replace("SendInput burst",
                            'FileAppend(burst, A_ScriptDir "\\zero.txt")')
    source = source.replace("OnMessage(0x4A, WM_COPYDATA)", r'''
OnMessage(0x4A, WM_COPYDATA)
OnMessage(0x8001, ProbeState)
ProbeState(wParam, lParam, msg, hwnd) {
    global PersistentPressRoutes, MacroHeldKeys, SkillHeldKeys
    global RuntimeAcceptingActions
    if (wParam = 1) {
        return PersistentPressRoutes.Count + MacroHeldKeys.Count * 100
            + SkillHeldKeys.Count * 10000 + (RuntimeAcceptingActions ? 1000000 : 0)
    }
    if (wParam = 2) {
        StartIndependentZeroBurst()
        return 1
    }
    if (wParam = 3) {
        SetTimer(() => SetSkillHoldKeys("RButton"), -1)
        return 1
    }
    if (wParam = 4) {
        SetTimer(() => SetSkillHoldKeys("RButton`nq"), -1)
        return 1
    }
    return 0
}''')
    script = tmp_path / "isolated.ahk"
    script.write_text(source, encoding="utf-8", newline="\n")
    proc = subprocess.Popen([str(AHK), "/ErrorStdOut", str(script)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        from hold_client import FindWindowW

        _wait(lambda: FindWindowW(None, title), proc)
        assert send_ahk_cmd_ex(title, CMD_PING, "")[0]
        hwnd = FindWindowW(None, title)
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                 ctypes.c_size_t, ctypes.c_ssize_t]
        send_message.restype = ctypes.c_ssize_t
        yield title, tmp_path, lambda cmd: send_message(hwnd, 0x8001, cmd, 0)
    finally:
        proc.terminate()
        proc.communicate(timeout=5)


def _wait(predicate, proc=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        if proc is not None and proc.poll() is not None:
            raise AssertionError(proc.communicate()[1].decode("utf-8", "replace"))
        time.sleep(0.005)
    raise AssertionError("isolated AHK probe timed out")


def test_timer_down_interrupted_by_stop_has_no_late_route_or_owner(isolated_server):
    title, directory, probe = isolated_server
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert send_ahk_cmd_ex(title, CMD_SET_MACRO_STEPS,
                           "down:RButton\ndelay:5000")[0]
    assert send_ahk_cmd_ex(title, CMD_START_MACRO, "")[0]
    edges = directory / "edges.txt"
    _wait(lambda: edges.exists() and "down:RButton" in edges.read_text())
    # This real message arrives while MacroTick's SendDown is suspended.
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    time.sleep(0.25)
    assert probe(1) == 0, "closed gate must have no late route or macro ownership"
    assert edges.read_text().splitlines() == ["down:RButton", "up:RButton"]


def test_independent_zero_burst_still_runs_with_gate_closed(isolated_server):
    _, directory, probe = isolated_server
    assert probe(1) == 0
    assert probe(2) == 1
    output = directory / "zero.txt"
    _wait(output.exists)
    assert output.read_text() == "{0 down}{0 up}" * 60
    assert not (directory / "edges.txt").exists(), "0 bypasses SendDown entirely"
    assert probe(1) == 0


def test_skill_timer_down_interrupted_by_stop_has_no_late_owner(isolated_server):
    title, directory, probe = isolated_server
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert probe(3) == 1
    edges = directory / "edges.txt"
    _wait(edges.exists)
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    time.sleep(0.25)
    assert probe(1) == 0
    assert edges.read_text().splitlines() == ["down:RButton", "up:RButton"]


def test_old_down_does_not_release_new_generation_same_route(isolated_server):
    title, directory, probe = isolated_server
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert send_ahk_cmd_ex(title, CMD_SET_MACRO_STEPS,
                           "down:RButton\ndelay:5000")[0]
    assert send_ahk_cmd_ex(title, CMD_START_MACRO, "")[0]
    edges = directory / "edges.txt"
    _wait(edges.exists)
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert send_ahk_cmd_ex(title, CMD_SET_SKILL_HOLD_KEYS, "RButton")[0]
    time.sleep(0.25)
    assert probe(1) == 1010001, "new skill owner remains; old macro owner must not return"
    assert edges.read_text().splitlines() == ["down:RButton", "down:RButton"]
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    assert probe(1) == 0
    assert edges.read_text().splitlines() == ["down:RButton", "down:RButton", "up:RButton"]


def test_old_skill_reconcile_does_not_continue_after_reopen(isolated_server):
    title, directory, probe = isolated_server
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert probe(4) == 1
    edges = directory / "edges.txt"
    _wait(edges.exists)
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    time.sleep(0.45)
    assert probe(1) == 1000000, "old desired collection cannot repopulate a new generation"
    assert edges.read_text().splitlines() == ["down:RButton", "up:RButton"]


def test_same_generation_desired_replacement_cancels_inflight_old_hold(isolated_server):
    title, directory, probe = isolated_server
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "true")[0]
    assert probe(4) == 1  # old desired collection: RButton, q
    edges = directory / "edges.txt"
    _wait(edges.exists)
    # No gate reset: the desired collection is independently versioned.
    assert send_ahk_cmd_ex(title, CMD_SET_SKILL_HOLD_KEYS, "v")[0]
    time.sleep(0.25)
    assert probe(1) == 1010001, "only the replacement collection's V may remain held"
    assert edges.read_text().splitlines() == ["down:RButton", "down:v", "up:RButton"]
    assert send_ahk_cmd_ex(title, CMD_SET_ACCEPTING_ACTIONS, "false")[0]
    assert edges.read_text().splitlines() == ["down:RButton", "down:v", "up:RButton", "up:v"]


def test_serialized_sender_never_overlaps_ahk_message_handlers(isolated_server):
    from torchlight_assistant.core.ahk_command_sender import AHKCommandSender

    title, directory, probe = isolated_server
    sender = AHKCommandSender(title)
    assert sender._send(CMD_SET_ACCEPTING_ACTIONS, "true")
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(
        sender._send(CMD_SET_SKILL_HOLD_KEYS, "RButton")))
    worker.start()
    _wait((directory / "edges.txt").exists)
    stopped = sender._send(CMD_SET_ACCEPTING_ACTIONS, "false", force=True)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert outcomes == [True]
    assert stopped, "stop must wait for the active WM_COPYDATA handler"
    assert probe(1) == 0
    assert not sender.transport_unavailable
