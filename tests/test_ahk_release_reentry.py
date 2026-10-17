"""Real AHK release reentry; physical input sinks are replaced by logs."""

import ctypes
import re
import subprocess
import uuid

import pytest

from test_ahk_persistent_transactions import AHK, ROOT, _function, _wait
from hold_client import FindWindowW
from torchlight_assistant.config.ahk_commands import (
    CMD_SET_ACCEPTING_ACTIONS, CMD_SET_MACRO_STEPS, CMD_START_MACRO,
)
from torchlight_assistant.core.ahk_command_sender import AHKCommandSender


pytestmark = pytest.mark.skipif(AHK is None, reason="AutoHotkey v2 required")


def test_special_pause_release_survives_serialized_stop_reentry(tmp_path):
    source = (ROOT / "hold_server_extended.ahk").read_text(encoding="utf-8")
    title = "pyahk_release_probe_" + uuid.uuid4().hex
    source = source.replace("HoldServer_Window_UniqueName_12345", title)
    source = source.replace("#SingleInstance Force", "#SingleInstance Off")
    source = source.replace("#Include ahk_commands.ahk", f'#Include {ROOT / "ahk_commands.ahk"}')
    source = re.sub(r'^Hotkey\("\$0.*$', '', source, flags=re.MULTILINE)
    source = source.replace(_function(source, "SendTransientKeyEdge"), r'''
SendTransientKeyEdge(mode, target, key, isDown) {
    static delayedFirstUp := false
    FileAppend((isDown ? "down:" : "up:") key "`n", A_ScriptDir "\edges.txt")
    if (!isDown && !delayedFirstUp) {
        delayedFirstUp := true
        Sleep 180
    }
    return true
}'''.strip())
    source = source.replace("OnMessage(0x4A, WM_COPYDATA)", r'''
OnMessage(0x4A, WM_COPYDATA)
OnMessage(0x8001, ProbeRelease)
ProbeRelease(wParam, lParam, msg, hwnd) {
    SetTimer(ProbeSpecialPause, -1)
    return 1
}
ProbeSpecialPause() {
    try {
        HandleSpecialKeyDown("space")
    } catch Error as err {
        FileAppend(err.Message, A_ScriptDir "\error.txt")
    } finally {
        FileAppend("done", A_ScriptDir "\done.txt")
    }
}''')
    script = tmp_path / "isolated_release.ahk"
    script.write_text(source, encoding="utf-8", newline="\n")
    proc = subprocess.Popen(
        [str(AHK), "/ErrorStdOut", str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait(lambda: FindWindowW(None, title), proc)
        sender = AHKCommandSender(title)
        assert sender._send(CMD_SET_ACCEPTING_ACTIONS, "true")
        assert sender._send(CMD_SET_MACRO_STEPS, "down:LButton\ndown:RButton\ndelay:5000")
        assert sender._send(CMD_START_MACRO, "")
        edges = tmp_path / "edges.txt"
        _wait(lambda: edges.exists() and edges.read_text().count("down:") == 2, proc)

        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        send_message.restype = ctypes.c_ssize_t
        assert send_message(FindWindowW(None, title), 0x8001, 0, 0) == 1
        _wait(lambda: "up:RButton" in edges.read_text(), proc)
        # Use the production mailbox: there are no overlapping Python commands.
        assert sender._send(CMD_SET_ACCEPTING_ACTIONS, "false", force=True)
        _wait(lambda: (tmp_path / "done.txt").exists(), proc)
        error = tmp_path / "error.txt"
        assert not error.exists(), error.read_text() if error.exists() else ""
        assert edges.read_text().splitlines() == [
            "down:LButton", "down:RButton", "up:RButton", "up:LButton",
        ]
    finally:
        proc.terminate()
        proc.communicate(timeout=5)
