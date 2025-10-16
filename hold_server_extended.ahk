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

; 原地模式状态
global StationaryModeActive := false
global StationaryModeType := "shift_modifier"

; 强制移动键
global ForceMoveKey := "a"  ; 默认为a键
global ForceMoveActive := false  ; 强制移动键是否处于按下状态

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

    ; 调试日志（可选）
    ; FileAppend("收到命令ID: " . cmdId . ", 参数: '" . param . "'`n", "ahk_debug.txt")

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
                ; FileAppend("目标窗口已设置: '" . TargetWin . "'`n", "ahk_debug.txt")
            }
            return 1

        case CMD_ACTIVATE:
            ; ACTIVATE - 激活目标窗口
            global TargetWin

            ; FileAppend("激活命令收到，TargetWin='" . TargetWin . "'`n", "ahk_debug.txt")

            if (TargetWin != "") {
                if WinExist(TargetWin) {
                    WinActivate(TargetWin)
                    ; FileAppend("窗口已激活`n", "ahk_debug.txt")
                    return 1
                } else {
                    ; FileAppend("窗口不存在`n", "ahk_debug.txt")
                    return 0
                }
            } else {
                ; FileAppend("TargetWin为空`n", "ahk_debug.txt")
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
                ; FileAppend("原地模式已设置: active=" . StationaryModeActive . ", type=" . StationaryModeType . "`n",
                ;     "ahk_debug.txt")
                return 1
            }
            return 0

        case CMD_SET_FORCE_MOVE_KEY:
            ; SET_FORCE_MOVE_KEY - 设置强制移动键
            ; 参数格式: "key" 例如: "a"
            if (param != "") {
                global ForceMoveKey
                ForceMoveKey := param
                ; FileAppend("强制移动键已设置: " . ForceMoveKey . "`n", "ahk_debug.txt")
                return 1
            }
            return 0

        case CMD_SET_FORCE_MOVE_STATE:
            ; SET_FORCE_MOVE_STATE - 设置强制移动状态
            ; 参数格式: "true" 或 "false"
            global ForceMoveActive
            ForceMoveActive := (param = "true")
            ; FileAppend("强制移动状态已设置: " . ForceMoveActive . "`n", "ahk_debug.txt")
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
                ; FileAppend("管理按键配置已设置: " . key . " -> " . target . " (延迟: " . delay . "ms)`n", "ahk_debug.txt")
                return 1
            }
            return 0

        case CMD_CLEAR_HOOKS:
            ; CLEAR_HOOKS - 清空所有可配置的Hook（保留F8根热键）
            ClearAllConfigurableHooks()
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
    global ForceMoveActive

    ; 如果强制移动键按下，所有队列中的按键都替换为f键
    if (ForceMoveActive) {
        ; 所有按键在强制移动时都替换为f键
        Send "{f}"
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
    global StationaryModeActive, StationaryModeType

    ; 如果原地模式未激活，不添加shift修饰符
    if (!StationaryModeActive) {
        return false
    }

    ; 如果不是shift_modifier模式，不添加shift修饰符
    if (StationaryModeType != "shift_modifier") {
        return false
    }

    ; 定义需要添加shift修饰符的技能键
    skillKeys := ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        "q", "w", "e", "r", "t", "y", "u", "i", "o", "p",
        "a", "s", "d", "f", "g", "h", "j", "k", "l", "z", "x", "c", "v", "b", "n", "m"]

    keyLower := StrLower(key)

    ; 检查是否是技能键
    for _, skillKey in skillKeys {
        if (keyLower == skillKey) {
            return true
        }
    }

    return false
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
    FileAppend("=== RegisterHook被调用 ===" . "`n", "ahk_debug.txt")
    FileAppend("按键: " . key . " 模式: " . mode . "`n", "ahk_debug.txt")
    
    ; 检查是否已注册
    if (RegisteredHooks.Has(key)) {
        existing_mode := RegisteredHooks[key]
        FileAppend("Hook已存在，旧模式: " . existing_mode . "`n", "ahk_debug.txt")
        if (existing_mode = mode) {
            FileAppend("Hook已存在且模式相同，跳过注册: " . key . " (" . mode . ")`n", "ahk_debug.txt")
            return
        } else {
            FileAppend("Hook已存在但模式不同，先取消旧Hook: " . key . " 旧模式:" . existing_mode . " 新模式:" . mode . "`n", "ahk_debug.txt")
            UnregisterHook(key)
        }
    } else {
        FileAppend("Hook不存在，准备新注册: " . key . " (" . mode . ")`n", "ahk_debug.txt")
    }

    ; 记录Hook
    RegisteredHooks[key] := mode

    ; 调试信息（可选）
    ; FileAppend("开始注册Hook: " . key . " 模式: " . mode . "`n", "ahk_debug.txt")

    ; 根据模式注册Hotkey（直接使用AHK按键名称）
    try {
        switch mode {
            case "intercept":
                ; 拦截模式 - 使用$前缀避免自拦截，只监听按下事件
                Hotkey("$" key, (*) => HandleInterceptKey(key))
                ; FileAppend("成功注册拦截Hook: $" . key . " (仅按下)`n", "ahk_debug.txt")

            case "priority":
                ; 优先级模式 - 发送priority_key事件，只监听按下事件（管理按键：拦截+延迟+映射）
                FileAppend("准备注册priority热键: $" . key . "`n", "ahk_debug.txt")
                try {
                    Hotkey("$" key, (*) => HandleManagedKey(key))
                    FileAppend("成功注册管理按键Hook: $" . key . " (拦截+延迟+映射)`n", "ahk_debug.txt")
                } catch as err {
                    FileAppend("注册管理按键Hook失败: $" . key . " 错误: " . err.message . "`n", "ahk_debug.txt")
                }

            case "special":
                ; 特殊按键模式 - 不拦截，持续状态检测（如space）
                Hotkey("~" key, (*) => HandleSpecialKeyDown(key))
                Hotkey("~" key " up", (*) => HandleSpecialKeyUp(key))
                ; FileAppend("成功注册特殊按键Hook: ~" . key . " (持续状态检测)`n", "ahk_debug.txt")

            case "monitor":
                ; 监控模式 - 使用~前缀不拦截，监听按下和释放事件
                Hotkey("~" key, (*) => HandleMonitorKey(key))
                Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key))
                ; FileAppend("成功注册监控Hook: ~" . key . " (按下+释放)`n", "ahk_debug.txt")

            case "block":
                ; 阻止模式 - 完全阻止按键
                Hotkey("$" key, (*) => {})
                ; FileAppend("成功注册阻止Hook: $" . key . " (完全阻止)`n", "ahk_debug.txt")
        }
    } catch as err {
        ; FileAppend("Hook注册失败: " . key . " 错误: " . err.message . "`n", "ahk_debug.txt")
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
        case "priority":
            Hotkey("$" key, "Off")
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
    ; 拦截模式 - 按键按下（简化版本，只处理按下事件）
    key_lower := StrLower(key)

    ; 调试信息（可选）
    ; FileAppend("HandleInterceptKey被调用: " . key . " (小写: " . key_lower . ")`n", "ahk_debug.txt")

    ; 所有拦截按键都完全拦截，只通知Python
    SendEventToPython("intercept_key_down:" key)
    ; FileAppend("按键已拦截并通知Python: " . key . "`n", "ahk_debug.txt")

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

    ; 调试信息
    FileAppend("=== HandleManagedKey被调用 ===" . "`n", "ahk_debug.txt")
    FileAppend("按键: " . key . "`n", "ahk_debug.txt")
    FileAppend("时间戳: " . A_TickCount . "`n", "ahk_debug.txt")

    ; 通知Python暂停调度（瞬时暂停）
    FileAppend("发送managed_key_down事件到Python`n", "ahk_debug.txt")
    SendEventToPython("managed_key_down:" key)

    ; 设置暂停标志
    IsPaused := true

    ; 🎯 根据配置进行延迟+映射
    if (ManagedKeysConfig.Has(key)) {
        config := ManagedKeysConfig[key]
        target := config.target
        delay := config.delay

        ; 先延迟
        if (delay > 0) {
            Sleep(delay)
        }

        ; 再发送映射后的按键
        Send "{" target "}"

        ; FileAppend("管理按键处理: " . key . " -> " . target . " (延迟: " . delay . "ms)`n", "ahk_debug.txt")
    } else {
        ; 如果没有配置，使用原按键
        Send "{" key "}"
    }

    ; 设置定时器，500ms后自动恢复
    SetTimer(RestoreManagedKey.Bind(key), -500)
}

RestoreManagedKey(key) {
    ; 恢复管理按键后的队列处理
    global IsPaused
    IsPaused := false
    SendEventToPython("managed_key_up:" key)
}

HandleMonitorKey(key) {
    ; 监控模式 - 按键按下 (不拦截)
    ; FileAppend("HandleMonitorKey被调用: " . key . "`n", "ahk_debug.txt")
    SendEventToPython("monitor_key_down:" key)
}

HandleMonitorKeyUp(key) {
    ; 监控模式 - 按键释放 (不拦截)
    ; FileAppend("HandleMonitorKeyUp被调用: " . key . "`n", "ahk_debug.txt")
    SendEventToPython("monitor_key_up:" key)
}

; 已移除RestorePriorityKey，替换为RestoreManagedKey

; ===============================================================================
; 事件发送到Python
; ===============================================================================
SendEventToPython(event) {
    ; 优先查找OSD窗口（运行时可见）
    pythonHwnd := WinExist("TorchLightAssistant_OSD_12345")

    if (!pythonHwnd) {
        ; 如果OSD窗口不存在，查找主窗口（停止时可见）
        pythonHwnd := WinExist("TorchLightAssistant_MainWindow_12345")
    }

    if (pythonHwnd) {
        SendWMCopyDataToPython(pythonHwnd, event)
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
    ; 清空所有可配置的Hook（只保留F8这个根热键）
    ; 先收集所有要删除的键，然后再删除（避免遍历时修改Map的问题）
    FileAppend("=== ClearAllConfigurableHooks 被调用 ===" . "`n", "ahk_debug.txt")
    FileAppend("当前注册的Hook数量: " . RegisteredHooks.Count . "`n", "ahk_debug.txt")
    
    ; 第一步：收集所有要删除的键
    keysToRemove := []
    for key, mode in RegisteredHooks {
        FileAppend("检查Hook: " . key . " (模式: " . mode . ")`n", "ahk_debug.txt")
        
        ; 只保留F8根热键，清除所有其他动态热键
        if (key = "F8") {
            FileAppend("跳过F8根热键`n", "ahk_debug.txt")
            continue
        }
        
        FileAppend("标记为待删除: " . key . "`n", "ahk_debug.txt")
        keysToRemove.Push(key)
    }
    
    ; 第二步：删除所有标记的键
    for index, key in keysToRemove {
        FileAppend("准备取消Hook: " . key . "`n", "ahk_debug.txt")
        UnregisterHook(key)
        FileAppend("已取消Hook: " . key . "`n", "ahk_debug.txt")
    }
    
    FileAppend("清理完成后，剩余Hook数量: " . RegisteredHooks.Count . "`n", "ahk_debug.txt")
    for key, mode in RegisteredHooks {
        FileAppend("剩余Hook: " . key . " (模式: " . mode . ")`n", "ahk_debug.txt")
    }
    FileAppend("=== ClearAllConfigurableHooks 完成 ===" . "`n", "ahk_debug.txt")
}

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
