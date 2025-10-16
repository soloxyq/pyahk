; ===============================================================================
; hold_server_extended.ahk - AHK完整输入系统服务器
; ===============================================================================
; 功能:
;   - 四级优先队列 (emergency/high/normal/low)
;   - 动态Hook管理 (避免自拦截)
;   - 按键序列支持
;   - 暂停/恢复机制
;   - 事件发送到Python
; ===============================================================================

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
#WinActivateForce ; 强制激活窗口

; ===============================================================================
; 全局状态
; ===============================================================================
global EmergencyQueue := []
global HighQueue := []
global NormalQueue := []
global LowQueue := []

global IsPaused := false
global PriorityKeysActive := Map()
global RegisteredHooks := Map()
global TargetWin := "" ; 目标窗口标识符

; 统计信息
global QueueStats := Map(
    "emergency", 0,
    "high", 0,
    "normal", 0,
    "low", 0,
    "processed", 0
)

; ===============================================================================
; GUI窗口 (接收WM_COPYDATA)
; ===============================================================================
WinTitle := "HoldServer_Window_UniqueName_12345"
gui1 := Gui()
gui1.Title := WinTitle
gui1.Hide()
hWnd := gui1.Hwnd

; 注册WM_COPYDATA消息
OnMessage(0x4A, WM_COPYDATA)

; ===============================================================================
; 队列处理器 (10ms定时器)
; ===============================================================================
ProcessQueue() {
    ; 紧急队列永远执行
    if (EmergencyQueue.Length > 0) {
        action := EmergencyQueue.RemoveAt(1)
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
        return
    }
    
    ; 暂停时不处理其他队列
    if (IsPaused) {
        return
    }
    
    ; 按优先级处理
    if (HighQueue.Length > 0) {
        action := HighQueue.RemoveAt(1)
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    } else if (NormalQueue.Length > 0) {
        action := NormalQueue.RemoveAt(1)
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    } else if (LowQueue.Length > 0) {
        action := LowQueue.RemoveAt(1)
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    }
}

; 启动定时器
SetTimer(ProcessQueue, 10)

; ===============================================================================
; 命令接收 (WM_COPYDATA)
; ===============================================================================
WM_COPYDATA(wParam, lParam, msg, hwnd) {
    ; 解析COPYDATASTRUCT
    str := StrGet(NumGet(lParam + A_PtrSize*2, "UPtr"), "UTF-8")
    if !str {
        return 0
    }
    
    ; 记录所有收到的命令
    FileAppend("收到命令: " . str . "`n", "ahk_debug.txt")

    ; 解析命令
    parts := StrSplit(str, ":")
    if parts.Length < 1 {
        return 0
    }
    
    cmd := parts[1]
    FileAppend("解析后的cmd: " . cmd . "`n", "ahk_debug.txt")
    
    ; 处理命令
    switch cmd {
        case "set_target":
            ; set_target:ahk_exe notepad++.exe
            global TargetWin
            if parts.Length > 1 {
                TargetWin := parts[2]
                OutputDebug("[AHK] 目标窗口已设置: '" . TargetWin . "'")
                
                ; 写入调试文件
                try {
                    FileDelete("ahk_debug.txt")
                } catch {
                }
                FileAppend("set_target收到: " . TargetWin . "`n", "ahk_debug.txt")
            }
            return 1

        case "activate":
            ; activate - 激活已设置的目标窗口
            global TargetWin
            
            ; 写入调试文件
            FileAppend("activate命令收到`n", "ahk_debug.txt")
            FileAppend("TargetWin值: '" . TargetWin . "'`n", "ahk_debug.txt")
            FileAppend("TargetWin长度: " . StrLen(TargetWin) . "`n", "ahk_debug.txt")
            
            if (TargetWin != "") {
                FileAppend("尝试查找窗口...`n", "ahk_debug.txt")
                if WinExist(TargetWin) {
                    FileAppend("窗口存在，开始激活`n", "ahk_debug.txt")
                    WinActivate(TargetWin)
                    FileAppend("WinActivate执行完成`n", "ahk_debug.txt")
                    return 1
                } else {
                    FileAppend("窗口不存在`n", "ahk_debug.txt")
                    return 0
                }
            } else {
                FileAppend("TargetWin为空`n", "ahk_debug.txt")
                return 0
            }

        case "enqueue":
            ; enqueue:priority:action
            if parts.Length < 3 {
                return 0
            }
            priority := Integer(parts[2])
            action := parts[3]
            EnqueueAction(priority, action)
            return 1
            
        case "press":
            ; press:key - 立即发送
            SendPress(parts[2])
            return 1
            
        case "sequence":
            ; sequence:key1,delay50,key2
            ExecuteSequence(parts[2])
            return 1
            
        case "pause":
            ; pause - 暂停队列处理
            IsPaused := true
            return 1
            
        case "resume":
            ; resume - 恢复队列处理
            IsPaused := false
            return 1
            
        case "hook_register":
            ; hook_register:key:mode
            if parts.Length < 3 {
                return 0
            }
            RegisterHook(parts[2], parts[3])
            return 1
            
        case "hook_unregister":
            ; hook_unregister:key
            UnregisterHook(parts[2])
            return 1
            
        case "clear_queue":
            ; clear_queue:priority
            priority := Integer(parts[2])
            ClearQueue(priority)
            return 1
            
        case "get_stats":
            ; get_stats - 返回统计信息
            SendStatsToPython()
            return 1
            
        case "ping":
            ; ping - 连接测试
            return 1
    }
    
    ; 未识别的命令
    return 0
}

; ===============================================================================
; 队列操作
; ===============================================================================
EnqueueAction(priority, action) {
    switch priority {
        case 0:
            EmergencyQueue.Push(action)
            QueueStats["emergency"] := QueueStats["emergency"] + 1
        case 1:
            HighQueue.Push(action)
            QueueStats["high"] := QueueStats["high"] + 1
        case 2:
            NormalQueue.Push(action)
            QueueStats["normal"] := QueueStats["normal"] + 1
        case 3:
            LowQueue.Push(action)
            QueueStats["low"] := QueueStats["low"] + 1
    }
}

ClearQueue(priority) {
    switch priority {
        case 0:
            EmergencyQueue := []
        case 1:
            HighQueue := []
        case 2:
            NormalQueue := []
        case 3:
            LowQueue := []
        case -1:
            ; 清空所有队列
            EmergencyQueue := []
            HighQueue := []
            NormalQueue := []
            LowQueue := []
    }
}

; ===============================================================================
; 动作执行
; ===============================================================================
ExecuteAction(action) {
    ; 解析动作类型
    parts := StrSplit(action, ":", 2)
    if parts.Length < 2 {
        SendPress(action) ; 兼容旧的直接发送key的模式
        return
    }
    
    actionType := parts[1]
    actionData := parts[2]
    
    ; 执行动作
    switch actionType {
        case "press":
            SendPress(actionData)
        case "sequence":
            ExecuteSequence(actionData)
        case "hold":
            SendDown(actionData)
        case "release":
            SendUp(actionData)
        case "mouse_click":
            ExecuteMouseClick(actionData)
        case "delay":
            Sleep Integer(actionData)
    }
}

SendPress(key) {
    ; 发送按键 (按下并释放)
    Send "{" key "}"
}

SendDown(key) {
    ; 按住按键
    Send "{" key " down}"
}

SendUp(key) {
    ; 释放按键
    Send "{" key " up}"
}

ExecuteSequence(sequence) {
    ; 执行按键序列: "delay50,q,delay100,w"
    parts := StrSplit(sequence, ",")
    for index, part in parts {
        part := Trim(part)
        if (InStr(part, "delay")) {
            ; 延迟指令
            ms := Integer(SubStr(part, 6))
            Sleep ms
        } else if (InStr(part, "+")) {
            ; 组合键: shift+q
            Send "{" part "}"
        } else {
            ; 普通按键
            Send "{" part "}"
        }
    }
}

ExecuteMouseClick(data) {
    ; 鼠标点击: "left" 或 "right" 或 "middle"
    Click data
}

; ===============================================================================
; Hook管理
; ===============================================================================
RegisterHook(key, mode) {
    ; 检查是否已注册
    if (RegisteredHooks.Has(key)) {
        return
    }
    
    ; 记录Hook
    RegisteredHooks[key] := mode
    
    ; 根据模式注册Hotkey（直接使用AHK按键名称）
    switch mode {
        case "intercept":
            ; 拦截模式 - 使用$前缀避免自拦截
            Hotkey("$" key, (*) => HandleInterceptKey(key))
            Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key))
            
        case "monitor":
            ; 监控模式 - 使用~前缀不拦截
            Hotkey("~" key, (*) => HandleMonitorKey(key))
            Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key))
            
        case "block":
            ; 阻止模式 - 完全阻止按键
            Hotkey("$" key, (*) => {})
    }
}

UnregisterHook(key) {
    ; 检查是否已注册
    if (!RegisteredHooks.Has(key)) {
        return
    }
    
    ; 获取模式
    mode := RegisteredHooks[key]
    
    ; 取消Hotkey（直接使用AHK按键名称）
    switch mode {
        case "intercept":
            Hotkey("$" key, "Off")
            Hotkey("$" key " up", "Off")
        case "monitor":
            Hotkey("~" key, "Off")
            Hotkey("~" key " up", "Off")
        case "block":
            Hotkey("$" key, "Off")
    }
    
    ; 删除记录
    RegisteredHooks.Delete(key)
}

; ===============================================================================
; Hook处理器
; ===============================================================================
HandleInterceptKey(key) {
    ; 拦截模式 - 按键按下
    key_lower := StrLower(key)
    ; 判断是否是优先级按键（使用AHK按键名称）
    ; space, RButton, e 等
    is_priority_key := (key_lower = "space" or key_lower = "rbutton" or key_lower = "e")

    if (is_priority_key) {
        ; 优先级按键 - 需要暂停队列
        ; 1. 通知Python暂停调度
        SendEventToPython("priority_key_down:" key)

        ; 2. 设置暂停标志
        IsPaused := true
        PriorityKeysActive[key] := true

        ; 3. 前置延迟 (确保游戏响应)
        Sleep 50

        ; 4. 发送按键到游戏
        Send "{" key "}"
    } else {
        ; 系统热键（F8/F7/F9/Z等）- 不暂停队列，只通知Python
        ; 1. 通知Python
        SendEventToPython("intercept_key_down:" key)

        ; 2. 不发送到游戏（由Python处理逻辑）
    }
}

HandleInterceptKeyUp(key) {
    ; 拦截模式 - 按键释放
    key_lower := StrLower(key)
    
    ; 判断是否是优先级按键（使用AHK按键名称）
    is_priority_key := (key_lower = "space" or key_lower = "rbutton" or key_lower = "e")
    
    if (is_priority_key) {
        ; 优先级按键释放
        ; 1. 通知Python
        SendEventToPython("priority_key_up:" key)
        
        ; 2. 清除标志
        PriorityKeysActive.Delete(key)
        
        ; 3. 如果没有其他优先级按键，恢复队列处理
        if (PriorityKeysActive.Count = 0) {
            IsPaused := false
        }
        
        ; 4. 发送释放到游戏
        Send "{" key " up}"
    } else {
        ; 系统热键释放
        SendEventToPython("intercept_key_up:" key)
    }
}

HandleMonitorKey(key) {
    ; 监控模式 - 按键按下 (不拦截)
    SendEventToPython("monitor_key_down:" key)
}

HandleMonitorKeyUp(key) {
    ; 监控模式 - 按键释放 (不拦截)
    SendEventToPython("monitor_key_up:" key)
}

; ===============================================================================
; 事件发送到Python
; ===============================================================================
SendEventToPython(event) {
    ; 使用文件通信 (简单可靠)
    try {
        ; 追加事件到文件
        FileAppend(event "`n", "ahk_events.txt")
    } catch as err {
        ; 忽略错误
    }
}

SendStatsToPython() {
    ; 发送统计信息
    stats := Format("stats:e={},h={},n={},l={},p={}",
        QueueStats["emergency"],
        QueueStats["high"],
        QueueStats["normal"],
        QueueStats["low"],
        QueueStats["processed"]
    )
    SendEventToPython(stats)
}

; ===============================================================================
; 辅助函数
; ===============================================================================
Trim(str) {
    ; 去除首尾空格
    return RegExReplace(str, "^\s+|\s+$", "")
}

; ===============================================================================
; 启动信息
; ===============================================================================
TrayTip("AHK输入系统已启动", "hold_server_extended.ahk", 1)

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
