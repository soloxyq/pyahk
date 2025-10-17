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

; 包含命令定义
#Include ahk_commands.ahk

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

; 🎯 新增：特殊按键状态跟踪
global SpecialKeysPressed := Map()  ; 跟踪特殊按键的按住状态
global SpecialKeysPaused := false   ; 特殊按键是否导致系统暂停

; 🎯 新增：管理按键配置存储
global ManagedKeysConfig := Map()   ; 存储管理按键的延迟和映射配置
global TargetWin := "" ; 目标窗口标识符

; 🎯 监控按键状态跟踪（避免重复发送事件）
global MonitorKeysState := Map()   ; 跟踪monitor按键的按下状态

; 原地模式状态
global StationaryModeActive := false
global StationaryModeType := "shift_modifier"

; 强制移动键
global ForceMoveKey := ""  ; 由Python设置，默认为空（未启用）
global ForceMoveActive := false  ; 强制移动键是否处于按下状态
global ForceMoveReplacementKey := "f"  ; 强制移动时的替换键，由Python设置

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
    ; dwData = 命令ID
    ; lpData = 参数字符串（可选）
    cmdId := NumGet(lParam, "UPtr")
    dataSize := NumGet(lParam + A_PtrSize, "UPtr")

    ; 读取参数（如果有）
    param := ""
    if (dataSize > 0) {
        dataPtr := NumGet(lParam + A_PtrSize * 2, "UPtr")
        param := StrGet(dataPtr, dataSize, "UTF-8")
    }

    ; 处理命令
    switch cmdId {
        case CMD_PING:
            ; PING - 测试连接
            return 1

        case CMD_SET_TARGET:
            ; SET_TARGET - 设置目标窗口
            global TargetWin
            if (param != "") {
                TargetWin := param
            }
            return 1

        case CMD_ACTIVATE:
            ; ACTIVATE - 激活目标窗口
            global TargetWin

            if (TargetWin != "") {
                if WinExist(TargetWin) {
                    WinActivate(TargetWin)
                    return 1
                } else {
                    return 0
                }
            } else {
                return 0
            }

        case CMD_ENQUEUE:
            ; ENQUEUE - 添加到队列
            ; 参数格式: "priority:action"
            parts := StrSplit(param, ":", , 2)
            if (parts.Length >= 2) {
                priority := Integer(parts[1])
                action := parts[2]
                EnqueueAction(priority, action)
                return 1
            }
            return 0

        case CMD_PAUSE:
            ; PAUSE - 暂停队列处理
            IsPaused := true
            return 1

        case CMD_RESUME:
            ; RESUME - 恢复队列处理
            IsPaused := false
            return 1

        case CMD_HOOK_REGISTER:
            ; HOOK_REGISTER - 注册Hook
            ; 参数格式: "key:mode"
            parts := StrSplit(param, ":")
            if (parts.Length >= 2) {
                RegisterHook(parts[1], parts[2])
                return 1
            }
            return 0

        case CMD_HOOK_UNREGISTER:
            ; HOOK_UNREGISTER - 取消Hook
            UnregisterHook(param)
            return 1

        case CMD_CLEAR_QUEUE:
            ; CLEAR_QUEUE - 清空队列
            priority := Integer(param)
            ClearQueue(priority)
            return 1

        case CMD_SET_STATIONARY:
            ; SET_STATIONARY - 设置原地模式
            ; 参数格式: "active:mode_type" 例如: "true:shift_modifier"
            parts := StrSplit(param, ":")
            if (parts.Length >= 2) {
                global StationaryModeActive, StationaryModeType
                StationaryModeActive := (parts[1] = "true")
                StationaryModeType := parts[2]
                return 1
            }
            return 0

        case CMD_SET_FORCE_MOVE_KEY:
            ; SET_FORCE_MOVE_KEY - 设置强制移动键
            ; 参数格式: "key" 例如: "a"
            if (param != "") {
                global ForceMoveKey
                ForceMoveKey := param
                return 1
            }
            return 0

        case CMD_SET_FORCE_MOVE_STATE:
            ; SET_FORCE_MOVE_STATE - 设置强制移动状态
            ; 参数格式: "true" 或 "false"
            global ForceMoveActive
            ForceMoveActive := (param = "true")
            return 1

        case CMD_SET_MANAGED_KEY_CONFIG:
            ; SET_MANAGED_KEY_CONFIG - 设置管理按键配置
            ; 参数格式: "key:target:delay" 例如: "e:+:500"
            global ManagedKeysConfig
            parts := StrSplit(param, ":")
            if (parts.Length >= 3) {
                key := parts[1]
                target := parts[2]
                delay := Integer(parts[3])
                ManagedKeysConfig[key] := { target: target, delay: delay }
                return 1
            }
            return 0

        case CMD_CLEAR_HOOKS:
            ; CLEAR_HOOKS - 清空所有可配置的Hook（保留F8根热键）
            ClearAllConfigurableHooks()
            return 1

        case CMD_SET_FORCE_MOVE_REPLACEMENT_KEY:
            ; SET_FORCE_MOVE_REPLACEMENT_KEY - 设置强制移动替换键
            ; 参数格式: "key" 例如: "f"
            if (param != "") {
                global ForceMoveReplacementKey
                ForceMoveReplacementKey := param
                return 1
            }
            return 0
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
    global ForceMoveActive, ForceMoveReplacementKey

    ; 如果强制移动键按下，所有队列中的按键都替换为配置的替换键
    if (ForceMoveActive) {
        ; 所有按键在强制移动时都替换为配置的替换键
        Send "{" ForceMoveReplacementKey "}"
        return  ; 已经处理完毕，直接返回
    }

    ; 正常按键处理（没有强制移动或不是技能键）
    if (ShouldAddShiftModifier(key)) {
        Send "+{" key "}"  ; 带shift修饰符
    } else {
        Send "{" key "}"   ; 普通按键
    }
}

SendDown(key) {
    ; 按住按键
    Send "{" key " down}"
}

SendUp(key) {
    ; 释放按键
    Send "{" key " up}"
}

ShouldAddShiftModifier(key) {
    ; 检查是否应该添加shift修饰符
    ; 🎯 简化逻辑：原地模式激活时，所有按键都加Shift
    ; 不需要判断是否是技能键，由Python层决定发送什么按键
    global StationaryModeActive, StationaryModeType

    ; 如果原地模式未激活，不添加shift修饰符
    if (!StationaryModeActive) {
        return false
    }

    ; 如果不是shift_modifier模式，不添加shift修饰符
    if (StationaryModeType != "shift_modifier") {
        return false
    }

    ; 原地模式激活且是shift_modifier模式，所有按键都加Shift
    return true
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
    ; 简化版本：直接注册，不检查是否已存在
    ; F8不加入记录（由Python端单独管理）
    ; 🔧 关键修复：使用"On"选项确保热键被启用（即使之前被禁用过）

    key_upper := StrUpper(key)

    ; 记录Hook（F8除外）
    if (key_upper != "F8") {
        RegisteredHooks[key] := mode
    }

    ; 根据模式注册Hotkey（使用"On"选项）
    try {
        switch mode {
            case "intercept":
                Hotkey("$" key, (*) => HandleInterceptKey(key), "On")

            case "priority":
                Hotkey("$" key, (*) => HandleManagedKey(key), "On")

            case "special":
                Hotkey("~" key, (*) => HandleSpecialKeyDown(key), "On")
                Hotkey("~" key " up", (*) => HandleSpecialKeyUp(key), "On")

            case "monitor":
                Hotkey("~" key, (*) => HandleMonitorKey(key), "On")
                Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key), "On")

            case "block":
                Hotkey("$" key, (*) => {}, "On")
        }
    } catch as err {
        ; 注册失败，静默处理
    }
}

UnregisterHook(key) {
    ; 简化版本：直接取消，不需要重复注销
    
    ; 检查是否在记录中
    if (!RegisteredHooks.Has(key)) {
        return
    }

    ; 获取模式
    mode := RegisteredHooks[key]

    ; 取消Hotkey
    try {
        switch mode {
            case "intercept", "priority", "block":
                Hotkey("$" key, "Off")

            case "monitor", "special":
                Hotkey("~" key, "Off")
                Hotkey("~" key " up", "Off")
        }
    } catch {
        ; 取消失败，静默处理
    }

    ; 删除记录
    RegisteredHooks.Delete(key)
}

; ===============================================================================
; Hook处理器
; ===============================================================================
HandleInterceptKey(key) {
    ; 拦截模式 - 按键按下（简化版本，只处理按下事件）
    key_upper := StrUpper(key)

    ; 🔍 F8按键特别标记（低频，保留）
    if (key_upper = "F8") {
        FileAppend("`n🔴 === F8按键被拦截 ===" . "`n", "ahk_debug.txt")
        FileAppend("时间: " . A_Now . "`n", "ahk_debug.txt")
    }

    ; 🔍 Z键特别标记（低频，保留）
    if (key_upper = "Z") {
        FileAppend("`n🟡 === Z按键被拦截 ===" . "`n", "ahk_debug.txt")
        FileAppend("时间: " . A_Now . "`n", "ahk_debug.txt")
        activeWin := WinGetTitle("A")
        FileAppend("当前活动窗口: " . activeWin . "`n", "ahk_debug.txt")
    }

    ; 所有拦截按键都完全拦截，只通知Python
    SendEventToPython("intercept_key_down:" key)

    ; 不发送到目标应用程序（完全拦截）
}

; 🎯 特殊按键处理（如space）- 不拦截，持续状态检测
HandleSpecialKeyDown(key) {
    global SpecialKeysPressed, SpecialKeysPaused

    ; 记录按键按下状态
    SpecialKeysPressed[key] := true

    ; 如果这是第一个特殊按键，暂停系统
    if (SpecialKeysPressed.Count = 1 && !SpecialKeysPaused) {
        SpecialKeysPaused := true
        SendEventToPython("special_key_pause:start")
    }

    ; 通知Python特殊按键状态
    SendEventToPython("special_key_down:" key)
}

HandleSpecialKeyUp(key) {
    global SpecialKeysPressed, SpecialKeysPaused

    ; 移除按键状态
    if (SpecialKeysPressed.Has(key)) {
        SpecialKeysPressed.Delete(key)
    }

    ; 如果所有特殊按键都释放了，恢复系统
    if (SpecialKeysPressed.Count = 0 && SpecialKeysPaused) {
        SpecialKeysPaused := false
        SendEventToPython("special_key_pause:end")
    }

    ; 通知Python特殊按键状态
    SendEventToPython("special_key_up:" key)
}

; 🎯 管理按键处理（如RButton/e）- 拦截+延迟+映射
HandleManagedKey(key) {
    global ManagedKeysConfig, IsPaused

    ; 🎯 优先响应：立即暂停队列 → 延迟 → 发送按键 → 立即恢复队列
    
    ; 1. 立即暂停队列（确保优先响应）
    IsPaused := true
    SendEventToPython("managed_key_down:" key)

    ; 2. 根据配置进行延迟+映射
    if (ManagedKeysConfig.Has(key)) {
        config := ManagedKeysConfig[key]
        target := config.target
        delay := config.delay

        ; 延迟
        if (delay > 0) {
            Sleep(delay)
        }

        ; 发送映射后的按键
        Send "{" target "}"
    } else {
        ; 如果没有配置，使用原按键
        Send "{" key "}"
    }

    ; 3. 立即恢复队列
    IsPaused := false
    SendEventToPython("managed_key_up:" key)
}

RestoreManagedKey(key) {
    ; 恢复管理按键后的队列处理
    global IsPaused
    IsPaused := false
    SendEventToPython("managed_key_up:" key)
}

HandleMonitorKey(key) {
    ; 监控模式 - 按键按下 (不拦截)
    ; 🎯 性能优化：只在状态变化时发送事件
    global MonitorKeysState
    
    key_upper := StrUpper(key)
    
    ; 如果按键已经是按下状态，不重复发送
    if (MonitorKeysState.Has(key_upper) && MonitorKeysState[key_upper] = true) {
        return
    }
    
    ; 标记为按下状态
    MonitorKeysState[key_upper] := true
    
    ; 发送按下事件
    SendEventToPython("monitor_key_down:" key)
}

HandleMonitorKeyUp(key) {
    ; 监控模式 - 按键释放 (不拦截)
    ; 🎯 性能优化：只在状态变化时发送事件
    global MonitorKeysState
    
    key_upper := StrUpper(key)
    
    ; 如果按键已经是释放状态，不重复发送
    if (!MonitorKeysState.Has(key_upper) || MonitorKeysState[key_upper] = false) {
        return
    }
    
    ; 标记为释放状态
    MonitorKeysState[key_upper] := false
    
    ; 发送释放事件
    SendEventToPython("monitor_key_up:" key)
}

; 已移除RestorePriorityKey，替换为RestoreManagedKey

; ===============================================================================
; 事件发送到Python
; ===============================================================================
SendEventToPython(event) {
    ; 🔍 F8事件特别标记
    if (InStr(event, "F8") || InStr(event, "f8")) {
        FileAppend("🔵 SendEventToPython: " . event . "`n", "ahk_debug.txt")
    }

    ; 优先查找OSD窗口（运行时可见）
    pythonHwnd := WinExist("TorchLightAssistant_OSD_12345")

    if (pythonHwnd) {
        if (InStr(event, "F8") || InStr(event, "f8")) {
            FileAppend("找到OSD窗口，句柄: " . pythonHwnd . "`n", "ahk_debug.txt")
        }
    } else {
        ; 如果OSD窗口不存在，查找主窗口（停止时可见）
        pythonHwnd := WinExist("TorchLightAssistant_MainWindow_12345")
        if (pythonHwnd && (InStr(event, "F8") || InStr(event, "f8"))) {
            FileAppend("找到主窗口，句柄: " . pythonHwnd . "`n", "ahk_debug.txt")
        }
    }

    if (pythonHwnd) {
        SendWMCopyDataToPython(pythonHwnd, event)
        if (InStr(event, "F8") || InStr(event, "f8")) {
            FileAppend("✅ 事件已发送到Python`n", "ahk_debug.txt")
        }
    } else {
        if (InStr(event, "F8") || InStr(event, "f8")) {
            FileAppend("❌ 错误：找不到Python窗口！`n", "ahk_debug.txt")
        }
    }
}

; 发送WM_COPYDATA消息到Python的辅助函数
SendWMCopyDataToPython(hwnd, eventData) {
    try {
        ; 准备UTF-8编码的数据
        eventBytes := Buffer(StrLen(eventData) * 3 + 1)  ; UTF-8最多3字节/字符
        dataSize := StrPut(eventData, eventBytes, "UTF-8") - 1  ; 不包含null终止符

        ; 创建COPYDATASTRUCT
        cds := Buffer(A_PtrSize * 3)
        NumPut("Ptr", 9999, cds, 0)                        ; dwData = 9999 (事件标识)
        NumPut("UInt", dataSize, cds, A_PtrSize)           ; cbData = 数据长度
        NumPut("Ptr", eventBytes.Ptr, cds, A_PtrSize * 2)  ; lpData = 数据指针

        ; 发送WM_COPYDATA消息
        DllCall("user32.dll\SendMessageW",
            "Ptr", hwnd,      ; 目标窗口句柄
            "UInt", 0x004A,   ; WM_COPYDATA
            "Ptr", 0,         ; wParam
            "Ptr", cds.Ptr)   ; lParam

    } catch as err {
        ; 发送失败，静默忽略
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
; TrayTip("AHK输入系统已启动", "hold_server_extended.ahk", 1)  ; 已禁用系统通知

; ===============================================================================
; Hook清理函数
; ===============================================================================
ClearAllConfigurableHooks() {
    ; 简化版本：清空所有记录的Hook（F8不在记录中，所以自动被保留）

    ; 收集所有要删除的键
    keysToRemove := []
    for key, mode in RegisteredHooks {
        keysToRemove.Push(key)
    }

    ; 删除所有键
    for index, key in keysToRemove {
        UnregisterHook(key)
    }
}

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
