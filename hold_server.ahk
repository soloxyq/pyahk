; =============== hold_server_minimal.ahk ===============
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
; 可选：若遇到部分游戏不响应，可尝试启用更底层的输入模式
; SendMode "Input"

; 设置一个隐藏 GUI 窗口用于接收 WM_COPYDATA
WinTitle := "HoldServer_Window_UniqueName_12345"
gui1 := Gui()
gui1.Hide()
hWnd := gui1.Hwnd

; 注册 WM_COPYDATA 消息
OnMessage(0x4A, WM_COPYDATA)

; 处理 WM_COPYDATA
WM_COPYDATA(wParam, lParam, msg, hwnd) {
    ; COPYDATASTRUCT 结构: {dwData:UPtr, cbData:UInt, lpData:UPtr}
    str := StrGet(NumGet(lParam + A_PtrSize*2, "UPtr"), "UTF-8")
    if !str {
        return 0
    }

    parts := StrSplit(str, ":")
    if parts.Length < 2 {
        return 0
    }
    cmd := parts[1]
    arg := parts[2]

    if (cmd = "hold") {
        SendDown(arg)
        return 1
    } else if (cmd = "release") {
        SendUp(arg)
        return 1
    }
    ; 未识别的指令
    return 0
}

; =============== 辅助函数 =================
SendDown(keys) {
    for k in StrSplit(keys, "+")
        Send("{" k " down}")
}
SendUp(keys) {
    for k in StrSplit(keys, "+")
        Send("{" k " up}")
}
SendPress(keys) {
    for k in StrSplit(keys, "+") {
        Send("{" k " down}")
    }
    for k in StrSplit(keys, "+") {
        Send("{" k " up}")
    }
}
; ==========================================
