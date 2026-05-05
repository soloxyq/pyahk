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
SendMode "Input"  ; 使用SendInput模式，提高在游戏中的识别率

; 包含命令定义
#Include ahk_commands.ahk

; ===============================================================================
; 全局状态
; ===============================================================================
global EmergencyQueue := []
global HighQueue := []
global NormalQueue := []
global LowQueue := []

; 🚀 性能优化：队列计数器，避免频繁的Length属性访问
global QueueCounts := Map(
    "emergency", 0,
    "high", 0,
    "normal", 0,
    "low", 0
)
global TotalQueueCount := 0  ; 总任务计数，用于快速检查是否有工作

global IsPaused := false
global PriorityKeysActive := Map()
global RegisteredHooks := Map()

; 🎯 新增：特殊按键状态跟踪
global SpecialKeysPressed := Map()  ; 跟踪特殊按键的按住状态
global SpecialKeysPaused := false   ; 特殊按键是否导致系统暂停

; 🎯 新增：管理按键配置存储
global ManagedKeysConfig := Map()   ; 存储管理按键的延迟和映射配置
global TargetWin := "" ; 目标窗口标识符

; 🎯 新增：紧急按键缓存（Master方案学习）
global CachedHpKey := ""     ; 缓存的HP按键
global CachedMpKey := ""     ; 缓存的MP按键
global ActiveManagedKeys := Map() ; 去重机制：正在处理的管理按键
global MANAGED_KEY_TIMEOUT_MS := 2000  ; ActiveManagedKeys 标记自动过期时长 (single-flight 安全网)

; 🎯 监控按键状态跟踪（避免重复发送事件）
global MonitorKeysState := Map()   ; 跟踪monitor按键的按下状态

; 原地模式状态
global StationaryModeActive := false
global StationaryModeType := ""  ; 由Python设置，默认为空（未启用）

; 强制移动键
global ForceMoveKey := ""  ; 由Python设置，默认为空（未启用）
global ForceMoveActive := false  ; 强制移动键是否处于按下状态
global ForceMoveReplacementKey := ""  ; 强制移动时的替换键，由Python设置，默认为空
global ForceMovePassthroughKeys := Map()  ; 强制移动期间不被替换的白名单(位移技能,如 RButton 闪现)

; 发送模式
global SendKeyMode := "direct"  ; "direct"=直接发送(SendInput) "control"=控件发送(ControlSend)

; 🎯 异步延迟机制
global DelayUntil := 0  ; 延迟到什么时间（毫秒），0表示没有延迟
global DelayClearOthers := false  ; 当前 delay 期间是否需要清空非紧急队列(管理按键专用)

; 🎯 基于F8状态的智能窗口句柄缓存
global CurrentPythonWindow := "TorchLightAssistant_MainWindow_12345"  ; 启动时默认主窗口
global CachedPythonHwnd := 0  ; 缓存的Python窗口句柄

; 统计信息
global QueueStats := Map(
    "emergency", 0,
    "high", 0,
    "normal", 0,
    "low", 0,
    "processed", 0
)


; 🚀 性能优化：字符串缓存池，减少频繁的字符串操作
global StringSplitCache := Map()
global StringLowerCache := Map()
global MaxCacheSize := 100

; 预定义常用字符串常量，避免重复创建
global ACTION_PRESS := "press"
global ACTION_DELAY := "delay"
global ACTION_SEQUENCE := "sequence"
global ACTION_CLEANUP := "cleanup"
global ACTION_HOLD := "hold"
global ACTION_RELEASE := "release"
global ACTION_MOUSE_CLICK := "mouse_click"
global ACTION_NOTIFY := "notify"

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
; 队列处理器 (20ms定时器)
; ===============================================================================
ProcessQueue() {
    ; 🔧 BUG修复(AHK v2 作用域): 必须显式声明所有用到的全局变量
    ; 否则函数内对其赋值会创建局部变量,读取也会读到未初始化的局部变量
    global DelayUntil, DelayClearOthers, TotalQueueCount, QueueCounts
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue
    global QueueStats, IsPaused, SpecialKeysPaused

    ; 🚀 性能优化：快速检查 - 如果没有任何任务且不在延迟中，直接返回
    if (TotalQueueCount = 0 && DelayUntil = 0) {
        return
    }

    ; 🎯 检查是否在异步延迟中
    if (DelayUntil > 0) {
        if (A_TickCount < DelayUntil) {
            ; 🔧 BUG修复(#2): 只在管理按键引发的 delay 期间清空非紧急队列(保证管理键独占)
            ; 普通 sequence 内的 delay 不应清掉自己后续的项,否则 "q,delay100,w" 中 w 会丢失
            if (DelayClearOthers && (QueueCounts["high"] > 0 || QueueCounts["normal"] > 0 || QueueCounts["low"] > 0)) {
                ClearNonEmergencyQueues()
            }
            return  ; 还在延迟中，不处理任何队列
        } else {
            ; 延迟结束，重置
            DelayUntil := 0
            DelayClearOthers := false
        }
    }

    ; 🚀 索急队列永远执行（使用计数器检查）
    if (QueueCounts["emergency"] > 0) {
        action := EmergencyQueue.RemoveAt(1)
        DecrementQueueCount("emergency")
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
        return
    }

    ; 🎯 修复：优先级模式下的絒急按键处理（Master方案学习）
    if (IsPaused) {
        return  ; 手动暂停时完全停止
    }

    if (SpecialKeysPaused) {
        ; 特殊按键激活时：只允许"安全动作"通过
        ; - HP/MP 紧急药剂(用户保命)
        ; - release:* 释放动作(防止 TriggerMode=2 按住模式 stuck key)
        if (QueueCounts["high"] > 0) {
            action := HighQueue[1]
            if (IsAllowedDuringPause(action)) {
                action := HighQueue.RemoveAt(1)
                DecrementQueueCount("high")
                ExecuteAction(action)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        if (QueueCounts["normal"] > 0) {
            action := NormalQueue[1]
            if (IsAllowedDuringPause(action)) {
                action := NormalQueue.RemoveAt(1)
                DecrementQueueCount("normal")
                ExecuteAction(action)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        if (QueueCounts["low"] > 0) {
            action := LowQueue[1]
            if (IsAllowedDuringPause(action)) {
                action := LowQueue.RemoveAt(1)
                DecrementQueueCount("low")
                ExecuteAction(action)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        return  ; 非安全动作在 SpecialKeysPaused 期间被过滤
    }

    ; 🚀 正常模式：按优先级处理（使用计数器）
    if (QueueCounts["high"] > 0) {
        action := HighQueue.RemoveAt(1)
        DecrementQueueCount("high")
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    } else if (QueueCounts["normal"] > 0) {
        action := NormalQueue.RemoveAt(1)
        DecrementQueueCount("normal")
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    } else if (QueueCounts["low"] > 0) {
        action := LowQueue.RemoveAt(1)
        DecrementQueueCount("low")
        ExecuteAction(action)
        QueueStats["processed"] := QueueStats["processed"] + 1
    }
}

; 启动定时器（固定20ms，简单高效）
SetTimer(ProcessQueue, 20)

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
            parts := CachedStrSplit(param, ":", , 2)
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
            parts := CachedStrSplit(param, ":")
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
            parts := CachedStrSplit(param, ":")
            if (parts.Length >= 2) {
                global StationaryModeActive, StationaryModeType
                StationaryModeActive := (parts[1] = "true")
                StationaryModeType := parts[2]
                return 1
            }
            return 0

        case CMD_SET_FORCE_MOVE_KEY:
            ; SET_FORCE_MOVE_KEY - 设置强制移动键
            ; 参数格式: "key" 例如: "a"，空字符串表示清空配置
            global ForceMoveKey
            ForceMoveKey := param  ; 接受任何值，包括空字符串
            return 1

        case CMD_SET_FORCE_MOVE_STATE:
            ; SET_FORCE_MOVE_STATE - 设置强制移动状态
            ; 参数格式: "true" 或 "false"
            global ForceMoveActive
            ForceMoveActive := (param = "true")
            return 1

        case CMD_SET_MANAGED_KEY_CONFIG:
            ; SET_MANAGED_KEY_CONFIG - 设置管理按键配置
            ; 参数格式: "key:target:delay[:hold_ms]" 例如: "e:+:500" 或 "c:c:75:50"
            global ManagedKeysConfig
            parts := CachedStrSplit(param, ":")
            if (parts.Length >= 3) {
                key := parts[1]
                target := parts[2]
                delay := Integer(parts[3])
                hold_ms := (parts.Length >= 4) ? Integer(parts[4]) : 0
                ManagedKeysConfig[key] := { target: target, delay: delay, hold_ms: hold_ms }
                return 1
            }
            return 0

        case CMD_CLEAR_HOOKS:
            ; CLEAR_HOOKS - 清空所有可配置的Hook（保留 F8/F7/F9 永久根热键）
            ClearAllConfigurableHooks()
            return 1

        case CMD_SET_FORCE_MOVE_REPLACEMENT_KEY:
            ; SET_FORCE_MOVE_REPLACEMENT_KEY - 设置强制移动替换键
            ; 参数格式: "key" 例如: "f"，空字符串使用默认值"f"
            global ForceMoveReplacementKey
            if (param != "") {
                ForceMoveReplacementKey := param
            } else {
                ForceMoveReplacementKey := "f"  ; 默认值
            }
            return 1

        case CMD_SET_FORCE_MOVE_PASSTHROUGH_KEYS:
            ; SET_FORCE_MOVE_PASSTHROUGH_KEYS - 设置强制移动白名单
            ; 参数格式: 逗号分隔的键名小写,如 "rbutton,space"
            ; 空字符串清空白名单(强制移动期间非紧急键都替换)
            global ForceMovePassthroughKeys
            ForceMovePassthroughKeys := Map()
            if (param != "") {
                keys := CachedStrSplit(param, ",")
                for index, k in keys {
                    k := Trim(k)
                    if (k != "") {
                        ForceMovePassthroughKeys[CachedStrLower(k)] := true
                    }
                }
            }
            return 1

        case CMD_SET_PYTHON_WINDOW_STATE:
            ; SET_PYTHON_WINDOW_STATE - 设置Python窗口状态
            ; 参数格式: "main" 或 "osd"
            global CurrentPythonWindow, CachedPythonHwnd
            if (param = "main") {
                CurrentPythonWindow := "TorchLightAssistant_MainWindow_12345"
            } else if (param = "osd") {
                CurrentPythonWindow := "TorchLightAssistant_OSD_12345"
            }
            ; 清除缓存，强制重新获取新窗口句柄
            CachedPythonHwnd := 0
            return 1

        case CMD_BATCH_UPDATE_CONFIG:
            ; BATCH_UPDATE_CONFIG - 批量配置更新（Master方案学习）
            ; 参数格式: "hp_key:1,mp_key:2,stationary_type:shift_modifier"
            UpdateBatchConfig(param)
            return 1

        case CMD_SET_SEND_MODE:
            ; SET_SEND_MODE - 设置发送模式
            ; 参数格式: "direct" 或 "control"
            global SendKeyMode
            if (param = "direct" || param = "control") {
                SendKeyMode := param
                return 1
            }
            return 0
    }

    ; 未识别的命令
    return 0
}

; ===============================================================================
; 紧急按键和去重机制（Master方案学习）
; ===============================================================================
; SpecialKeysPaused 期间应当放行的"安全动作":
; - 紧急动作 (HP/MP 药剂):用户保命,必须执行
; - release:* 动作:防止 TriggerMode=2 按住模式在 Space 闪避期间被卡键
;   尤其 STOPPED 路径里 ~space hook 被注销后再也收不到 up 事件,
;   release:2 会永远卡在 normal 队列 → 游戏里 2 一直被按住 stuck
IsAllowedDuringPause(action) {
    if (IsEmergencyAction(action))
        return true
    if (InStr(action, "release:") = 1)
        return true
    return false
}

; 判断是否为紧急动作（HP/MP等生存技能）
IsEmergencyAction(action) {
    global CachedHpKey, CachedMpKey, ACTION_PRESS

    ; 🚀 解析动作类型（使用缓存）
    if (InStr(action, ":")) {
        parts := CachedStrSplit(action, ":", , 2)
        if (parts.Length >= 2 && parts[1] = ACTION_PRESS) {
            key := CachedStrLower(parts[2])
            return (key = CachedHpKey || key = CachedMpKey)
        }
    } else {
        ; 兼容旧格式：直接按键
        key := CachedStrLower(action)
        return (key = CachedHpKey || key = CachedMpKey)
    }

    return false
}

; 检查按键序列是否正在处理中（去重机制）
; 安全网:标记超过 MANAGED_KEY_TIMEOUT_MS 自动过期,防止 ClearQueue/PAUSED 等场景
; 把 cleanup 动作清掉后 ActiveManagedKeys 永远卡死,导致该 managed_key 永远点不出来
IsManagedKeyActive(key) {
    global ActiveManagedKeys, MANAGED_KEY_TIMEOUT_MS
    if (!ActiveManagedKeys.Has(key))
        return false
    if (A_TickCount - ActiveManagedKeys[key] > MANAGED_KEY_TIMEOUT_MS) {
        ActiveManagedKeys.Delete(key)
        return false
    }
    return true
}

; 标记管理按键为活跃状态
MarkManagedKeyActive(key) {
    global ActiveManagedKeys
    ActiveManagedKeys[key] := A_TickCount
}

; 清理管理按键活跃标记
ClearManagedKeyMark(key) {
    global ActiveManagedKeys
    if (ActiveManagedKeys.Has(key)) {
        ActiveManagedKeys.Delete(key)
    }
}

; 批量配置更新函数（Master方案学习）
UpdateBatchConfig(configString) {
    global CachedHpKey, CachedMpKey, StationaryModeType

    if (configString = "") {
        return
    }

    ; 🚀 使用缓存分割
    configs := CachedStrSplit(configString, ",")
    for index, config in configs {
        parts := CachedStrSplit(config, ":")
        if (parts.Length >= 2) {
            key := Trim(parts[1])
            value := Trim(parts[2])

            switch key {
                case "hp_key":
                    CachedHpKey := CachedStrLower(value)
                case "mp_key":
                    CachedMpKey := CachedStrLower(value)
                case "stationary_type":
                    StationaryModeType := value
                    ; 可扩展更多配置项...
            }
        }
    }
}

; ===============================================================================
; 🚀 队列计数器管理函数（性能优化）
; ===============================================================================
IncrementQueueCount(queueName) {
    global QueueCounts, TotalQueueCount
    QueueCounts[queueName] := QueueCounts[queueName] + 1
    TotalQueueCount := TotalQueueCount + 1
}

DecrementQueueCount(queueName) {
    global QueueCounts, TotalQueueCount
    if (QueueCounts[queueName] > 0) {
        QueueCounts[queueName] := QueueCounts[queueName] - 1
        TotalQueueCount := TotalQueueCount - 1
    }
}

; 🚀 快速清理非紂急队列（性能优化）
ClearNonEmergencyQueues() {
    global HighQueue, NormalQueue, LowQueue, QueueCounts, TotalQueueCount
    
    ; 更新计数器
    TotalQueueCount := TotalQueueCount - QueueCounts["high"] - QueueCounts["normal"] - QueueCounts["low"]
    QueueCounts["high"] := 0
    QueueCounts["normal"] := 0
    QueueCounts["low"] := 0
    
    ; 清空队列
    HighQueue := []
    NormalQueue := []
    LowQueue := []
}

; ===============================================================================
; 队列操作
; ===============================================================================
EnqueueAction(priority, action) {
    ; 🔧 防御性 global 声明:虽然函数内当前只有 .Push() 方法调用(不会触发 local 化),
    ; 但显式声明可防止未来误改导致的隐式作用域问题
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue, QueueStats

    ; 🔧 BUG修复(B5+B16): 拦截 sequence 类型,展开为多个原子动作进入同一优先级队列
    ; 这样可复用 DelayUntil 异步机制,避免 ExecuteSequence 中的同步 Sleep 阻塞所有队列
    if (InStr(action, "sequence:") = 1) {
        sequenceData := SubStr(action, 10)
        parts := CachedStrSplit(sequenceData, ",")
        for index, part in parts {
            part := Trim(part)
            if (part = "")
                continue
            if (InStr(part, "delay") = 1) {
                ms := Integer(SubStr(part, 6))
                EnqueueAction(priority, "delay:" ms)
            } else {
                EnqueueAction(priority, "press:" part)
            }
        }
        return
    }

    switch priority {
        case 0:
            EmergencyQueue.Push(action)
            IncrementQueueCount("emergency")
            QueueStats["emergency"] := QueueStats["emergency"] + 1
        case 1:
            HighQueue.Push(action)
            IncrementQueueCount("high")
            QueueStats["high"] := QueueStats["high"] + 1
        case 2:
            NormalQueue.Push(action)
            IncrementQueueCount("normal")
            QueueStats["normal"] := QueueStats["normal"] + 1
        case 3:
            LowQueue.Push(action)
            IncrementQueueCount("low")
            QueueStats["low"] := QueueStats["low"] + 1
    }
}

ClearQueue(priority) {
    ; 🔧 BUG修复(AHK v2 作用域): 必须显式声明四个队列变量为 global,
    ; 否则函数内 EmergencyQueue := [] 等赋值实际上创建的是局部变量,
    ; 全局队列内容不会被清空,只有计数器清零,会导致 PAUSED 状态和管理按键
    ; 期间残留旧动作下次入队时被混合执行。
    global QueueCounts, TotalQueueCount
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue
    global ActiveManagedKeys
    global DelayUntil, DelayClearOthers

    switch priority {
        case 0:
            TotalQueueCount := TotalQueueCount - QueueCounts["emergency"]
            QueueCounts["emergency"] := 0
            EmergencyQueue := []
            ; emergency 里 cleanup:key 动作丢失,同步清 single-flight 锁
            ActiveManagedKeys := Map()
        case 1:
            TotalQueueCount := TotalQueueCount - QueueCounts["high"]
            QueueCounts["high"] := 0
            HighQueue := []
        case 2:
            TotalQueueCount := TotalQueueCount - QueueCounts["normal"]
            QueueCounts["normal"] := 0
            NormalQueue := []
        case 3:
            TotalQueueCount := TotalQueueCount - QueueCounts["low"]
            QueueCounts["low"] := 0
            LowQueue := []
        case -1:
            ; 🚀 清空所有队列（使用计数器）
            TotalQueueCount := 0
            QueueCounts["emergency"] := 0
            QueueCounts["high"] := 0
            QueueCounts["normal"] := 0
            QueueCounts["low"] := 0
            EmergencyQueue := []
            HighQueue := []
            NormalQueue := []
            LowQueue := []
            ; cleanup:key 动作随 emergency 一起被清,managed_key 锁需同步重置,
            ; 否则 PAUSED → RESUME 后该 key 会因为锁残留而永远点不出来
            ActiveManagedKeys := Map()
            ; 队列已全空,继续保留 delay 状态语义不干净,且 DelayClearOthers
            ; 在下次入队前还会清掉非紧急(包括 hold 模式 resume 时的 hold:N)
            DelayUntil := 0
            DelayClearOthers := false
        case -2:
            ; 🔧 清空所有非紧急队列(保留 emergency,用于管理按键期间保护 HP/MP 救命动作)
            ; emergency 不动 → cleanup:key 仍会执行 → ActiveManagedKeys 不需手动清
            ClearNonEmergencyQueues()
    }
}

; ===============================================================================
; 🚀 字符串缓存函数（性能优化）
; ===============================================================================
CachedStrSplit(str, delimiter, omitChars := "", maxParts := -1) {
    global StringSplitCache, MaxCacheSize
    
    ; 生成缓存键
    cacheKey := str . "|" . delimiter . "|" . omitChars . "|" . maxParts
    
    ; 检查缓存
    if (StringSplitCache.Has(cacheKey)) {
        return StringSplitCache[cacheKey]
    }
    
    ; 执行分割
    result := StrSplit(str, delimiter, omitChars, maxParts)
    
    ; 缓存管理：防止内存泄露
    ; 🔧 BUG修复(B6): 不能在 Map 迭代时同时 Delete,先收集 keys 再删除
    if (StringSplitCache.Count >= MaxCacheSize) {
        keysToRemove := []
        halfSize := MaxCacheSize // 2
        for key in StringSplitCache {
            keysToRemove.Push(key)
            if (keysToRemove.Length >= halfSize)
                break
        }
        for index, k in keysToRemove
            StringSplitCache.Delete(k)
    }

    ; 添加到缓存
    StringSplitCache[cacheKey] := result
    return result
}

CachedStrLower(str) {
    global StringLowerCache, MaxCacheSize
    
    ; 检查缓存
    if (StringLowerCache.Has(str)) {
        return StringLowerCache[str]
    }
    
    ; 执行转换
    result := StrLower(str)
    
    ; 缓存管理
    ; 🔧 BUG修复(B6): 不能在 Map 迭代时同时 Delete,先收集 keys 再删除
    if (StringLowerCache.Count >= MaxCacheSize) {
        keysToRemove := []
        halfSize := MaxCacheSize // 2
        for key in StringLowerCache {
            keysToRemove.Push(key)
            if (keysToRemove.Length >= halfSize)
                break
        }
        for index, k in keysToRemove
            StringLowerCache.Delete(k)
    }

    StringLowerCache[str] := result
    return result
}

; 📝 注意：直接使用常量比较，不要函数包装（函数调用开销 > 直接比较）


; ===============================================================================
; 动作执行
; ===============================================================================
ExecuteAction(action) {
    ; 🔧 BUG修复(AHK v2 作用域): 把分散的 global 声明统一到函数顶部,
    ; 避免在 if 分支内零散声明导致维护困难
    global ACTION_CLEANUP, ACTION_PRESS, ACTION_SEQUENCE, ACTION_HOLD, ACTION_RELEASE, ACTION_MOUSE_CLICK, ACTION_DELAY, ACTION_NOTIFY
    global DelayUntil, DelayClearOthers

    ; 🚀 处理清理标记（使用常量比较）
    if (InStr(action, ACTION_CLEANUP . ":")) {
        key := StrReplace(action, ACTION_CLEANUP . ":", "")
        ClearManagedKeyMark(key)
        return
    }

    ; 🚀 解析动作类型（使用缓存分割）
    parts := CachedStrSplit(action, ":", , 2)
    if parts.Length < 2 {
        SendPress(action, IsEmergencyAction(action)) ; 兼容旧的直接发送key的模式
        return
    }

    actionType := parts[1]
    actionData := parts[2]

    ; 🚀 执行动作（直接常量比较，无函数调用开销）
    ; 注意: sequence 已在 EnqueueAction 入口展开为多个原子动作,此处不再处理
    if (actionType = ACTION_PRESS) {
        SendPress(actionData, IsEmergencyAction(action))
    } else if (actionType = ACTION_HOLD) {
        SendDown(actionData)
    } else if (actionType = ACTION_RELEASE) {
        SendUp(actionData)
    } else if (actionType = ACTION_MOUSE_CLICK) {
        ExecuteMouseClick(actionData)
    } else if (actionType = ACTION_DELAY) {
        ; 🎯 异步延迟：设置延迟结束时间，不阻塞
        ; 普通 delay 不清队列,允许同优先级的后续动作继续排队
        DelayUntil := A_TickCount + Integer(actionData)
        DelayClearOthers := false
    } else if (actionType = "delay_clear") {
        ; 🔧 管理按键专用延迟:延迟期间清空非紧急队列,保证管理键独占执行
        DelayUntil := A_TickCount + Integer(actionData)
        DelayClearOthers := true
    } else if (actionType = ACTION_NOTIFY) {
        ; 🎯 发送通知到Python
        SendEventToPython(actionData)
    }
}

SendPress(key, forceMoveBypass := false) {
    ; 发送按键 (按下并释放，最小延时)
    global ForceMoveActive, ForceMoveReplacementKey, ForceMovePassthroughKeys, SendKeyMode, TargetWin

    ; 强制移动期间,白名单内的按键(位移技能如 RButton 闪现)正常发送,
    ; 其他按键全部替换为配置的替换键(通常是交互键 F,实现"边跑边捡装备/对话")
    ; HP/MP 等紧急动作永远绕过替换,否则会出现边跑路边把药剂替换成 F 的致命问题
    if (ForceMoveActive && !forceMoveBypass) {
        if (!ForceMovePassthroughKeys.Has(CachedStrLower(key))) {
            SendKeyInternal(ForceMoveReplacementKey)
            return
        }
        ; 在白名单里 → 落到下面的正常发送路径
    }

    ; 正常按键处理
    if (ShouldAddShiftModifier(key)) {
        ; 带shift修饰符
        SendKeyInternal("+" . key)
    } else {
        SendKeyInternal(key)
    }
}

SendKeyInternal(key) {
    ; 内部发送函数 - 根据模式选择发送方式
    global SendKeyMode, TargetWin

    if (SendKeyMode = "control" && TargetWin != "") {
        ; ControlSend模式 - 直接发送到目标窗口
        try {
            ControlSend FormatKeyForSend(key), , TargetWin
        } catch {
            ; 如果ControlSend失败，回退到直接模式
            SendDirect(key)
        }
    } else {
        ; 直接发送模式 (SendInput)
        SendDirect(key)
    }
}

SendDirect(key) {
    ; 直接发送模式 - 使用SendInput
    ; 🔧 BUG修复(#3): 必须区分 "+1"(Shift+主键) 与 "+"(字面加号键,如管理按键 target="+")
    if (StrLen(key) > 1 && SubStr(key, 1, 1) = "+") {
        ; "+1" → "+{1}" (Shift 修饰符 + 主键花括号包装)
        Send "+{" SubStr(key, 2) "}"
    } else if (key = "+") {
        ; 字面加号键(管理按键映射 target="+" 时的场景)
        Send "{+}"
    } else if (InStr(key, "+")) {
        ; 其他形如 "ctrl+x" 的组合键(罕见,sequence 中可能出现)
        Send key
    } else {
        ; 普通按键
        Send "{" key " down}"
        Sleep 5
        Send "{" key " up}"
    }
}

FormatKeyForSend(key) {
    ; 把内部 key 格式转换为 AHK Send 兼容字符串(用于 ControlSend)
    if (StrLen(key) > 1 && SubStr(key, 1, 1) = "+") {
        return "+{" SubStr(key, 2) "}"
    } else if (key = "+") {
        return "{+}"
    } else if (InStr(key, "+")) {
        return key
    } else {
        return "{" key "}"
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

; ExecuteSequence 已废弃: sequence 现在在 EnqueueAction 入口直接展开为
; 多个原子动作进入同优先级队列,复用 DelayUntil 异步机制,不再需要同步执行

ExecuteMouseClick(data) {
    ; 鼠标点击: "left" 或 "right" 或 "middle"
    Click data
}

; ===============================================================================
; Hook管理
; ===============================================================================
RegisterHook(key, mode) {
    ; 简化版本：直接注册，不检查是否已存在
    ; 永久根热键(F8/F7/F9)不加入 RegisteredHooks 记录,故 ClearAllConfigurableHooks 不会清它们
    ; 🔧 关键修复：使用"On"选项确保热键被启用（即使之前被禁用过）

    key_upper := StrUpper(key)

    ; 记录Hook（永久根热键 F8/F7/F9 除外）
    if (key_upper != "F8" && key_upper != "F7" && key_upper != "F9") {
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
    ; 🔧 AHK v2 作用域:函数内对全局变量赋值会自动 local 化,顶部统一 global 声明
    global RegisteredHooks, SpecialKeysPressed, SpecialKeysPaused

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

    ; 🔧 special 模式注销时清理 Pause 状态:
    ; 防止 Space 等键在按住期间被注销 → up 事件永远收不到 → SpecialKeysPaused 卡 true
    ; → 下次入队的 release:* 被卡住 → 按住模式 stuck key 灾难
    ; (SpecialKeysPressed/SpecialKeysPaused 已在函数顶部 global 声明)
    if (mode = "special") {
        if (SpecialKeysPressed.Has(key)) {
            SpecialKeysPressed.Delete(key)
        }
        if (SpecialKeysPressed.Count = 0 && SpecialKeysPaused) {
            SpecialKeysPaused := false
            SendEventToPython("special_key_pause:end")
        }
    }

    ; 删除记录
    RegisteredHooks.Delete(key)
}

; ===============================================================================
; Hook处理器
; ===============================================================================
HandleInterceptKey(key) {
    ; 拦截模式 - 按键按下（简化版本，只处理按下事件）

    ; 所有拦截按键都完全拦截，只通知Python
    SendEventToPython("intercept_key_down:" key)

    ; 🎯 F8不再在AHK端主动切换，由Python完成UI切换后主动通知AHK

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

; 🎯 管理按键处理（如RButton/e）- 拦截+延迟+映射 + 去重
HandleManagedKey(key) {
    global ManagedKeysConfig, EmergencyQueue, HighQueue, NormalQueue, LowQueue, IsPaused

    ; 🎯 去重机制：防止快速重复按键（Master方案学习）
    if (IsManagedKeyActive(key)) {
        return  ; 该按键序列正在处理中，忽略
    }

    ; 标记为处理中
    MarkManagedKeyActive(key)

    ; 🚀 关键修复：清空所有非紂急队列，同时同步计数器！
    if (QueueCounts["high"] > 0 || QueueCounts["normal"] > 0 || QueueCounts["low"] > 0) {
        ClearNonEmergencyQueues()  ; 使用统一函数确保计数器同步
    }

    SendEventToPython("managed_key_down:" key)

    ; 将延迟+映射操作放入Emergency队列
    if (ManagedKeysConfig.Has(key)) {
        config := ManagedKeysConfig[key]
        target := config.target
        delay := config.delay
        hold_ms := config.HasOwnProp("hold_ms") ? config.hold_ms : 0

        ; 🚀 放入Emergency队列（使用EnqueueAction确保计数器同步）
        ; 🎯 按键前后都加delay - 用 delay_clear 确保延迟期间非紧急队列被清空,保护管理按键独占
        if (delay > 0) {
            EnqueueAction(0, "delay_clear:" delay)  ; 按键前delay,清非紧急队列
        }
        if (hold_ms > 0) {
            EnqueueAction(0, "hold:" target)
            EnqueueAction(0, "delay_clear:" hold_ms)
            EnqueueAction(0, "release:" target)
        } else {
            EnqueueAction(0, "press:" target)
        }

        if (delay > 0) {
            EnqueueAction(0, "delay_clear:" delay)  ; 按键后delay,清非紧急队列
        }

        ; 🎯 关键修复：添加恢复通知，让Python恢复调度器
        EnqueueAction(0, "notify:managed_key_complete:" key)

        ; 添加清理标记（序列执行完后清除去重标记）
        EnqueueAction(0, "cleanup:" key)
    } else {
        ; 🚀 如果没有配置，使用原按键（确保计数器同步）
        EnqueueAction(0, "press:" key)
        EnqueueAction(0, "notify:managed_key_complete:" key)
        EnqueueAction(0, "cleanup:" key)
    }
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

; ===============================================================================
; 事件发送到Python
; ===============================================================================
SendEventToPython(event) {
    global CurrentPythonWindow, CachedPythonHwnd

    ; 🎯 使用缓存的窗口句柄
    if (CachedPythonHwnd != 0) {
        ; 直接使用缓存的句柄
        if (SendWMCopyDataToPython(CachedPythonHwnd, event)) {
            return  ; 发送成功，直接返回
        }
        ; 发送失败，清除缓存
        CachedPythonHwnd := 0
    }

    ; 🎯 缓存失效或首次调用：根据F8状态查找正确的窗口
    CachedPythonHwnd := WinExist(CurrentPythonWindow)

    ; 最后尝试发送
    if (CachedPythonHwnd) {
        ; 🎯 如果最后一次发送也失败，清除缓存
        if (!SendWMCopyDataToPython(CachedPythonHwnd, event)) {
            CachedPythonHwnd := 0
        }
    }
}

; 发送WM_COPYDATA消息到Python的辅助函数（简单高效版本）
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
        result := DllCall("user32.dll\SendMessageW",
            "Ptr", hwnd,      ; 目标窗口句柄
            "UInt", 0x004A,   ; WM_COPYDATA
            "Ptr", 0,         ; wParam
            "Ptr", cds.Ptr)   ; lParam

        ; 返回成功状态
        return (result != 0)

    } catch as err {
        ; 发送失败，返回失败
        return false
    }
}

SendStatsToPython() {
    ; 🚀 发送统计信息
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
; 注: AHK v2 已有内置 Trim(),原自定义实现已删除以避免 shadow 内置函数

; ===============================================================================
; 启动信息
; ===============================================================================
; TrayTip("AHK输入系统已启动", "hold_server_extended.ahk", 1)  ; 已禁用系统通知

; ===============================================================================
; Hook清理函数
; ===============================================================================
ClearAllConfigurableHooks() {
    ; 简化版本：清空所有记录的 Hook
    ; F8/F7/F9 永久根热键不在 RegisteredHooks 中,自动被保留(见 RegisterHook 的 key_upper 检查)
    global ActiveManagedKeys, SpecialKeysPressed, SpecialKeysPaused

    ; 收集所有要删除的键
    keysToRemove := []
    for key, mode in RegisteredHooks {
        keysToRemove.Push(key)
    }

    ; 删除所有键(UnregisterHook 已经为每个 special 键单独清理了 SpecialKeysPressed/Paused)
    for index, key in keysToRemove {
        UnregisterHook(key)
    }

    ; 配置切换:所有 managed_keys 即将注销,残留 single-flight 锁无意义
    ActiveManagedKeys := Map()

    ; 兜底:即使 per-key 注销有遗漏,也确保 special 状态彻底归零
    SpecialKeysPressed := Map()
    if (SpecialKeysPaused) {
        SpecialKeysPaused := false
        SendEventToPython("special_key_pause:end")
    }
}

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
