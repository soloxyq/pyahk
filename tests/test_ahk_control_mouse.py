#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通过隐藏窗口收到的真实 Windows 消息验证 control 鼠标边沿。"""

import runpy
import subprocess
from pathlib import Path

import pytest


_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_ahk_transient_press.py")))

_PROBE = r'''
#Requires AutoHotkey v2.0
#SingleInstance Off

global TargetGui := Gui(, "pyahk_control_mouse_hidden_target")
global Messages := []
global Failures := []
global Checks := 0

for msg in [0x100, 0x101, 0x201, 0x202, 0x204, 0x205, 0x207, 0x208, 0x20B, 0x20C] {
    OnMessage(msg, CaptureMessage)
}

CaptureMessage(wParam, lParam, msg, hwnd) {
    global TargetGui, Messages
    if (hwnd = TargetGui.Hwnd) {
        Messages.Push({msg: msg, value: wParam})
    }
}

Expect(label, actual, expected) {
    global Checks, Failures
    Checks += 1
    if (actual != expected) {
        Failures.Push(label ": expected [" expected "] actual [" actual "]")
    }
}

CheckEdges(key, downMessage, upMessage, xButton := 0) {
    global TargetGui, Messages
    Messages := []
    Expect(key "-down-accepted",
        SendTransientKeyEdge("control", TargetGui.Hwnd, key, true), true)
    Expect(key "-up-accepted",
        SendTransientKeyEdge("control", TargetGui.Hwnd, key, false), true)
    ; ControlSend/ControlClick 投递消息后，让当前线程接收它们。
    Sleep 50
    Expect(key "-message-count", Messages.Length, 2)
    if (Messages.Length = 2) {
        Expect(key "-down-message", Messages[1].msg, downMessage)
        Expect(key "-up-message", Messages[2].msg, upMessage)
        if (xButton) {
            Expect(key "-down-button", Messages[1].value >> 16, xButton)
            Expect(key "-up-button", Messages[2].value >> 16, xButton)
        }
    }
}

; 隐藏窗口从不 Show，所有输入都显式指定其 HWND，不触及桌面输入流。
CheckEdges("LButton", 0x201, 0x202)
CheckEdges("rbutton", 0x204, 0x205)
CheckEdges("MButton", 0x207, 0x208)
CheckEdges("XButton1", 0x20B, 0x20C, 1)
CheckEdges("XButton2", 0x20B, 0x20C, 2)
CheckEdges("q", 0x100, 0x101)

; 鼠标边沿不能继承 ControlClick 的隐式 Sleep，否则会阻塞紧急队列。
previousDelay := SetControlDelay(500)
started := DllCall("Kernel32\GetTickCount64", "UInt64")
Expect("no-control-delay-down", SendTransientKeyEdge("control", TargetGui.Hwnd, "LButton", true), true)
elapsed := DllCall("Kernel32\GetTickCount64", "UInt64") - started
Expect("mouse-edge-does-not-block", elapsed < 250, true)
Expect("control-delay-restored", A_ControlDelay, 500)
SetControlDelay previousDelay
SendTransientKeyEdge("control", TargetGui.Hwnd, "LButton", false)
Expect("target-remains-hidden", DllCall("IsWindowVisible", "Ptr", TargetGui.Hwnd), 0)
Expect("target-not-activated", DllCall("GetForegroundWindow", "Ptr") = TargetGui.Hwnd, false)

report := "CHECKS=" Checks "`nRESULT=" (Failures.Length ? "FAIL" : "OK") "`n"
for failure in Failures {
    report .= "FAIL " failure "`n"
}
FileAppend(report, A_Args[1], "UTF-8")
ExitApp Failures.Length ? 1 : 0
'''


def test_control_mouse_delivers_mouse_messages_to_hidden_target(tmp_path):
    ahk = _HELPERS["_find_ahk"]()
    if ahk is None:
        pytest.skip("未找到 AutoHotkey v2")

    source = Path(_HELPERS["AHK_SCRIPT"]).read_text(encoding="utf-8")
    edge_sender = _HELPERS["_extract_function"](
        source.splitlines(), "SendTransientKeyEdge"
    )
    script = tmp_path / "control_mouse.ahk"
    result = tmp_path / "result.txt"
    script.write_text(_PROBE + "\n" + edge_sender, encoding="utf-8", newline="\n")
    proc = subprocess.run(
        [ahk, "/ErrorStdOut", str(script), str(result)],
        capture_output=True,
        timeout=10,
    )
    stderr = proc.stderr.decode("utf-8", "replace")
    assert result.is_file(), f"AHK 未产生结果(exit={proc.returncode}): {stderr}"
    report = result.read_text(encoding="utf-8-sig")
    assert "RESULT=OK" in report, f"{report}\n{stderr}"
    assert proc.returncode == 0, f"exit={proc.returncode}\n{report}\n{stderr}"
