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

; 🎯 AHK 端通用宏解释器:Python 只下发步骤,AHK 保证顺序、循环和中止释放
global MacroSteps := []
global MacroActive := false
global MacroIndex := 1
global MacroDueTime := 0
global MacroHeldKeys := Map()
global MacroHeldOrder := []
global MacroSpecialSuppressed := false
global MacroManagedSuppressed := false
global MACRO_TICK_MS := 5

; 🎯 技能持久按住键(TriggerMode=2)—— 声明式账本,AHK 独占"实际按下"状态
; Python 只声明"期望按住哪些键"(有序,鼠标键在前),不再镜像实际状态。
; 好处:hold 不再经动作队列,管理键清队/特殊键抑制都不会让两端账本分叉;
; 抑制期间只推迟新增 down,抑制结束后 ReconcileSkillHoldKeys() 自动补按。
global SkillHoldDesiredOrder := []  ; 期望持键(按 Python 下发顺序)
global SkillHeldKeys := Map()       ; 实际已 SendDown 的键
global SkillHeldOrder := []         ; 实际按下顺序(LIFO 释放用)

; 🎯 队列级临时持键(目前唯一来源:管理键 hold_ms 配置生成的 hold:target)
; 它与技能持键账本是两套独立机制,但同样必须在"完全停下"时被释放,
; 否则 PAUSED/STOPPED 恰好落在 hold:target 与 release:target 之间时,
; release 会随 emergency 队列一起被清掉 → 该键真实卡在按下状态。
global ManagedHoldTargets := Map()

; 🎯 运行时闸门:Python 进入 STOPPED/PAUSED 时**第一件事**就是关掉它。
; 理由:UnifiedScheduler.stop() 只 join 2 秒,若在飞回调(如 OCR)超时未退出,
; 它之后仍可能继续下发命令,而此时 Python 侧的停止流程已经走完。
; 关闸 = 原子停止屏障(CMD_SET_ACCEPTING_ACTIONS(false) 同一条消息内 ClearQueue(-1)),
; 且闸门封住**所有**输入生产路径:EnqueueAction(最深卡点,覆盖 CMD_ENQUEUE/管理键/
; sequence 展开)、MacroTick、HandleManagedKey、CMD_START_MACRO、非空持键声明,
; 以及 IsSkillHoldSuppressed(压住 Reconcile 的补按环节)。
; 清队列/停宏/释放持键/空持键声明等安全清理命令永远放行,释放(up)永不被闸门拦截。
global RuntimeAcceptingActions := true

; 🎯 基于F8状态的智能窗口句柄缓存
global CurrentPythonWindow := "TorchLightAssistant_MainWindow_12345"  ; 启动时默认主窗口
global CachedPythonHwnd := 0  ; 缓存的Python窗口句柄

; 统计信息
global QueueStats := Map(
    "emergency", 0,
    "high", 0,
    "normal", 0,
    "low", 0,
    "processed", 0,
    "dropped", 0
)

; ===============================================================================
; 队列预算(= 排队延迟上限)
; ===============================================================================
; 吞吐是硬上限:ProcessQueue 由定时器驱动且**每 tick 最多执行一个动作**
; → 实测 63 动作/秒(见 QUEUE_TICK_MS 处的定时器实测表)。
; 而生产侧没有任何背压:check_cooldowns 每 100ms 对每个"图标就绪"的冷却技能各入队一次,
; 8 个这样的技能就是 80/s;逗号序列 "q,w,e" 还会 1 变 3。
; 一旦生产 > 上限,旧实现的队列会无限增长 —— 实测生产 100/s 跑 10 秒后积压 500 项、
; 正在打出去的是 10 秒前决策的按键,而且随时间线性恶化。表现为"技能乱放",不报任何错。
; (数字来自 tests/test_ahk_queue_throughput.py 的实测,不是估算)
;
; 因此给非紧急队列设**待执行原子总预算**:预算就是延迟上限 —— 16 × 15.8ms ≈ 250ms。
; 超限时丢**最旧**的可丢动作:ARPG 里过期的决策毫无价值,最新决策才反映当前局面。
; 紧急队列(HP/MP 保命)不计入预算,永不丢弃。
;
; ⚠️ 为什么是"全局预算"而不是"每队列深度":出队是**严格优先级**的(high 空了才轮到
; normal)。按队列各限 16,三条非紧急队列就能同时积压 48 项,低优先级要等 ~750ms ——
; "≤250ms"只在单一优先级下成立。全局预算才让这个上限对混合优先级也成立。
;
; ⚠️ 为什么按"原子"而不是"队列项":sequence 作为**一个决策**只占一个队列项(见
; EnqueueAction),但它要花 N 个 tick 才发得完。按项算的话 16 个 20 原子的序列
; = 320 个 tick ≈ 5 秒,延迟上限就名存实亡了。按原子算 = 按真实执行时间算。
global MAX_PENDING_ATOMS := 16

; 严格优先级出队还有第二种"变陈旧"的方式:队列**不长**(预算管住了),但可以**很久轮不到**。
; 高优先级持续生产时,低优先级那些项会一直排不上,某天高优先级一空就打出几十秒前的决策。
; 预算约束不了这个(总量本来就没超)。
;
; 年龄必须**绑在每个队列项上**(入队时记录 QueueTickCount),不能按队列计数:
; 每队列一个计数器有两个实测到的错误 ——
;   (a) 清队列不重置计数,PAUSED 后新入队的动作"继承"旧年龄,等 1 个 tick 就被丢;
;   (b) 计数只描述队首,队首被丢后重新从 0 数,排在后面、等了同样久的动作照样被执行。
; 判定在**出队时**做:取到的项年龄 ≥ STALE_TICKS 且可丢 → 丢弃换下一个。
; 过期决策没有价值,执行一个 500ms 前的决策比不执行更糟。~32 tick × 15.8ms ≈ 500ms。
global STALE_TICKS := 32
; 全局 tick 计数:ProcessQueue 每次触发 +1(含 PAUSED/delay 期间 —— 真实时间照样流逝,
; 年龄语义是"真实等待了多久",不是"被跳过了几次")
global QueueTickCount := 0

; 已经发出过至少一个原子的序列(剩余部分被放回队首)。与未开始的 sequence: 区分开:
; 未开始的可以整条丢(原子性);已经开打的**不能**丢 —— 那是连招打一半停手。
global ACTION_SEQ_RUNNING := "seqrun"
global LastOverloadNotifyAt := 0     ; 过载通知节流(避免刷屏)
; 丢弃发生在 EnqueueAction 里,而它常在 WM_COPYDATA 处理中执行 —— 此刻 Python 正阻塞在
; SendMessageW 里等这条消息返回。在那里回发消息会把两边的消息处理嵌套起来,没必要冒这个险。
; 因此只置标志,由 ProcessQueue(定时器上下文)在下一 tick 发出通知。
global PendingOverloadNotify := false


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
; 队列处理器 (由 QUEUE_TICK_MS 驱动,每 tick 最多执行一个动作)
; ===============================================================================
ProcessQueue() {
    ; 🔧 BUG修复(AHK v2 作用域): 必须显式声明所有用到的全局变量
    ; 否则函数内对其赋值会创建局部变量,读取也会读到未初始化的局部变量
    global DelayUntil, DelayClearOthers, TotalQueueCount, QueueCounts
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue
    global QueueStats, IsPaused, SpecialKeysPaused
    global PendingOverloadNotify, QueueTickCount, STALE_TICKS

    ; 全局 tick 计数无条件递增(含 PAUSED/delay):队列项年龄的语义是"真实等了多久"
    QueueTickCount := QueueTickCount + 1

    ; 过载通知:丢弃发生在 WM_COPYDATA 上下文,通知推迟到这里(定时器上下文)发出。
    ; 放在快速返回**之前** —— 队列刚被清空(如 PAUSED)时这条通知也不该丢。
    ; 标志由 NotifyQueueOverload 在**真正发出**时才清除(被节流时保留,下一 tick 重试)。
    if (PendingOverloadNotify) {
        NotifyQueueOverload()
    }

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
            ; 🔧 BUG修复: 延迟期间放行 HP/MP 救命药剂。本延迟检查在 emergency 取队之前 return,
            ; 用户技能序列里的大 delay(如 delay500)会把救命药剂整段压住 → 血量危急时漏吃药。
            ; 按索引扫描出第一个 IsEmergencyAction(press:hp/mp 键)执行,保持其余项顺序不变。
            ; ⚠️ 故意【不】放行 release:* —— 管理键 hold 模式自己的 release:target 也排在 emergency
            ; 队列里(见 HandleManagedKey),按字符串无法与 TriggerMode=2 的保命 release 区分,若提前
            ; 放行会在管理键 hold 延迟期内错误释放目标键、打乱时序。这是相比原始 bug 报告收窄的安全修复:
            ; 只解决"救命药剂被延迟压住",release 的延迟窗口极短(≤delay)且不触碰管理键状态机。
            if (QueueCounts["emergency"] > 0) {
                loop EmergencyQueue.Length {
                    if (IsEmergencyAction(EmergencyQueue[A_Index].action)) {
                        item := EmergencyQueue.RemoveAt(A_Index)
                        DecrementQueueCount("emergency")
                        ExecuteAction(item.action, 0)
                        QueueStats["processed"] := QueueStats["processed"] + 1
                        break
                    }
                }
            }
            return  ; 还在延迟中,非紧急队列不处理
        } else {
            ; 延迟结束，重置
            DelayUntil := 0
            DelayClearOthers := false
        }
    }

    ; 🚀 索急队列永远执行（使用计数器检查）
    if (QueueCounts["emergency"] > 0) {
        item := EmergencyQueue.RemoveAt(1)
        DecrementQueueCount("emergency")
        ExecuteAction(item.action, 0)
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
            if (IsAllowedDuringPause(HighQueue[1].action)) {
                item := HighQueue.RemoveAt(1)
                DecrementQueueCount("high")
                ExecuteAction(item.action, 1)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        if (QueueCounts["normal"] > 0) {
            if (IsAllowedDuringPause(NormalQueue[1].action)) {
                item := NormalQueue.RemoveAt(1)
                DecrementQueueCount("normal")
                ExecuteAction(item.action, 2)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        if (QueueCounts["low"] > 0) {
            if (IsAllowedDuringPause(LowQueue[1].action)) {
                item := LowQueue.RemoveAt(1)
                DecrementQueueCount("low")
                ExecuteAction(item.action, 3)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
        return  ; 非安全动作在 SpecialKeysPaused 期间被过滤
    }

    ; 🚀 正常模式:按优先级出队。取到的项若已过期(真实年龄 ≥ STALE_TICKS)且可丢,
    ; 直接丢弃换下一个 —— 执行一个 500ms 前的决策(如那时该放的技能)比不执行更糟。
    ; 年龄绑在项上,所以"排在被丢队首后面、等了同样久"的项也会被逐个判掉,
    ; 不会像旧的每队列计数那样重新从 0 数。一个 tick 内仍最多**执行**一个动作;
    ; 丢弃只是扔掉字符串,不发键,连丢多个不影响节奏。
    loop 64 {   ; 防御上界(预算约束下积压有限,正常一次最多丢十几个)
        name := NextToExecuteQueueName()
        if (name = "") {
            break
        }
        q := QueueByName(name)
        item := q.RemoveAt(1)
        DecrementQueueCount(name)
        if (QueueTickCount - item.tick >= STALE_TICKS && IsDroppableAction(item.action)) {
            QueueStats["dropped"] := QueueStats["dropped"] + 1
            PendingOverloadNotify := true
            continue   ; 过期且可丢 → 丢弃,看下一个
        }
        ExecuteAction(item.action, PriorityOfQueueName(name))
        QueueStats["processed"] := QueueStats["processed"] + 1
        break
    }
}

; 队列调度周期。⚠️ 这个数字不能凭直觉挑,它决定整套吞吐预算:
; Windows 消息定时器的粒度是 ~15.6ms,SetTimer 会**向上凑到整数个系统 tick**。
; 实测(tests/test_ahk_queue_throughput.py 的墙钟探针):
;     请求 20ms → 实际 31.6ms → 31.7 动作/秒   ← 凑成了 2 个系统 tick,白白浪费一半
;     请求 16ms → 实际 25.0ms → 40.0 动作/秒
;     请求 15ms → 实际 15.8ms → 63.3 动作/秒   ← 1 个系统 tick
;     请求 10/5/1ms → 实际 ~15.9ms → ~63 动作/秒(触底,再小也没用)
; 原来的 20 落在最差的位置上:比 15 慢一倍,却什么都没换来。
; 31.7/s 的上限连仓库里的现成配置都跑不住(last.json 37.3/s、d4灵巫.json 55.9/s
; 都超了 → 永久过载、持续丢动作)。改成 15 后上限 63/s,现有配置全部有余量。
; 注意这里仍是**每 tick 最多一个动作**,按键间距只是从 31.6ms 变成 15.8ms,
; 单次仍是独立的 SendInput,不会出现"同一毫秒连发两个键"。
global QUEUE_TICK_MS := 15
SetTimer(ProcessQueue, QUEUE_TICK_MS)

; 宏解释器独立 tick:只推进 AHK 端宏状态机,不占用全局 DelayUntil/队列
SetTimer(MacroTick, MACRO_TICK_MS)

; 进程退出兜底:仅覆盖**正常退出路径**(ExitApp / CMD_SHUTDOWN / 用户手动关脚本)。
; ⚠️ Windows 的 TerminateProcess(Python Popen.terminate/kill)不会触发 OnExit ——
; 所以 Python 停 AHK 必须先发 CMD_SHUTDOWN 优雅关闭,terminate 只是超时兜底
; (走到 terminate 时持键可能残留,无法避免)。
OnExit(AhkOnExitHandler)
AhkOnExitHandler(reason, code) {
    try {
        ReleaseAllSkillHoldKeys()
    } catch {
        ; 退出路径不再抛错
    }
    try {
        ReleaseMacroHeldKeys()
    } catch {
    }
    try {
        ReleaseAllManagedHoldTargets()
    } catch {
    }
    return 0  ; 允许退出
}

; ===============================================================================
; AHK 端通用宏解释器
; ===============================================================================
SetMacroSteps(param) {
    global MacroSteps

    MacroSteps := []
    if (param = "") {
        return
    }

    lines := StrSplit(param, "`n", "`r")
    for index, line in lines {
        line := Trim(line, "`r`n `t ")
        if (line = "") {
            continue
        }

        parts := CachedStrSplit(line, ":", , 2)
        if (parts.Length < 2) {
            continue
        }

        stype := parts[1]
        data := parts[2]
        if (stype = "delay") {
            if (!IsInteger(data)) {
                continue
            }
            MacroSteps.Push({ type: stype, data: String(Max(Integer(data), 0)) })
        } else if (stype = "down" || stype = "up" || stype = "press") {
            if (data != "") {
                MacroSteps.Push({ type: stype, data: data })
            }
        }
    }
}

StartMacro() {
    global MacroSteps, MacroActive, MacroIndex, MacroDueTime
    global MacroSpecialSuppressed, MacroManagedSuppressed, SpecialKeysPaused, ActiveManagedKeys

    ReleaseMacroHeldKeys()
    MacroIndex := 1
    MacroDueTime := 0
    MacroManagedSuppressed := (ActiveManagedKeys.Count > 0)
    MacroSpecialSuppressed := SpecialKeysPaused
    MacroActive := (MacroSteps.Length > 0)
}

StopMacro() {
    global MacroActive, MacroIndex, MacroDueTime
    global MacroSpecialSuppressed, MacroManagedSuppressed

    MacroActive := false
    MacroIndex := 1
    MacroDueTime := 0
    MacroSpecialSuppressed := false
    MacroManagedSuppressed := false
    ReleaseMacroHeldKeys()
}

MacroTick() {
    global MacroSteps, MacroActive, MacroIndex, MacroDueTime
    global MacroSpecialSuppressed, MacroManagedSuppressed, RuntimeAcceptingActions

    ; 闸门关闭时宏必须静默:正常关闸走 ClearQueue(-1)→StopMacro 已置 MacroActive=false,
    ; 这里是防御第二层 —— 关闸与本 tick 之间不留任何"再发一键"的窗口
    if (!RuntimeAcceptingActions) {
        return
    }
    if (!MacroActive || MacroSpecialSuppressed || MacroManagedSuppressed) {
        return
    }
    if (MacroSteps.Length = 0) {
        return
    }
    if (MacroDueTime > 0 && A_TickCount < MacroDueTime) {
        return
    }

    MacroDueTime := 0
    if (MacroIndex < 1 || MacroIndex > MacroSteps.Length) {
        MacroIndex := 1
    }

    step := MacroSteps[MacroIndex]
    MacroIndex += 1
    if (MacroIndex > MacroSteps.Length) {
        MacroIndex := 1
    }

    stype := step.type
    data := step.data

    if (stype = "delay") {
        MacroDueTime := A_TickCount + Max(Integer(data), 1)
    } else if (stype = "down") {
        ; 同技能持键账本:被 block_mouse 吞掉时不记账,避免 ReleaseMacroHeldKeys 发出多余的 up
        if (SendDown(data)) {
            TrackMacroDown(data)
        }
    } else if (stype = "up") {
        SendUp(data)
        TrackMacroUp(data)
    } else if (stype = "press") {
        SendPress(data, false)
    }
}

TrackMacroDown(key) {
    global MacroHeldKeys, MacroHeldOrder
    if (key = "") {
        return
    }
    MacroHeldKeys[key] := true
    for index, existing in MacroHeldOrder {
        if (existing = key) {
            return
        }
    }
    MacroHeldOrder.Push(key)
}

TrackMacroUp(key) {
    global MacroHeldKeys, MacroHeldOrder
    if (key = "") {
        return
    }
    if (MacroHeldKeys.Has(key)) {
        MacroHeldKeys.Delete(key)
    }
    idx := MacroHeldOrder.Length
    while (idx > 0) {
        if (MacroHeldOrder[idx] = key) {
            MacroHeldOrder.RemoveAt(idx)
        }
        idx -= 1
    }
}

ReleaseMacroHeldKeys() {
    global MacroHeldKeys, MacroHeldOrder

    released := Map()
    idx := MacroHeldOrder.Length
    while (idx > 0) {
        key := MacroHeldOrder[idx]
        if (MacroHeldKeys.Has(key)) {
            SendUp(key)
            released[key] := true
        }
        idx -= 1
    }

    for key, _ in MacroHeldKeys {
        if (!released.Has(key)) {
            SendUp(key)
        }
    }

    MacroHeldKeys := Map()
    MacroHeldOrder := []
}

AbortMacroRuntime() {
    global MacroIndex, MacroDueTime
    ReleaseMacroHeldKeys()
    MacroIndex := 1
    MacroDueTime := 0
}

SetMacroSpecialSuppressed(enabled) {
    global MacroSpecialSuppressed, MacroDueTime
    MacroSpecialSuppressed := enabled
    if (enabled) {
        AbortMacroRuntime()
    } else {
        MacroDueTime := 0
    }
}

SetMacroManagedSuppressed(enabled) {
    global MacroManagedSuppressed, MacroDueTime
    MacroManagedSuppressed := enabled
    if (enabled) {
        AbortMacroRuntime()
    } else {
        MacroDueTime := 0
    }
}

; ===============================================================================
; 技能持久按住键(TriggerMode=2)声明式同步
; ===============================================================================
; 是否处于输入抑制期(特殊键按住 / 管理键序列进行中 / 运行时闸门关闭)。
; 抑制只推迟"新增 down";释放(up)永不受抑制影响。
; 闸门关闭视同抑制:关闸后 desired 必为空(非空声明在协议层被拒),
; 这里是防御第二层 —— 即使有残留 desired,关闸期间也绝不按下新键。
IsSkillHoldSuppressed() {
    global SpecialKeysPaused, ActiveManagedKeys, RuntimeAcceptingActions
    return !RuntimeAcceptingActions || SpecialKeysPaused || ActiveManagedKeys.Count > 0
}

; 接收 Python 下发的完整期望集合(每行一个键名)。空 param = 释放全部技能持键。
SetSkillHoldKeys(param) {
    global SkillHoldDesiredOrder

    desired := []
    seen := Map()
    if (param != "") {
        for index, line in StrSplit(param, "`n", "`r") {
            key := Trim(line, "`r`n `t ")
            if (key = "" || seen.Has(key)) {
                continue
            }
            seen[key] := true
            desired.Push(key)
        }
    }
    SkillHoldDesiredOrder := desired
    ReconcileSkillHoldKeys()
}

; 差量同步:先 LIFO 释放不再期望的键(始终立即执行),再按下缺失的键(抑制期推迟)。
; 幂等:用同一集合重复调用不会产生额外按键。
ReconcileSkillHoldKeys() {
    global SkillHoldDesiredOrder, SkillHeldKeys, SkillHeldOrder

    desired := Map()
    for index, key in SkillHoldDesiredOrder {
        desired[key] := true
    }

    ; 1) 释放不再期望的键 —— 任何抑制都不能推迟 up,否则就是卡键
    idx := SkillHeldOrder.Length
    while (idx > 0) {
        key := SkillHeldOrder[idx]
        if (!desired.Has(key)) {
            SendUp(key)
            SkillHeldKeys.Delete(key)
            SkillHeldOrder.RemoveAt(idx)
        }
        idx -= 1
    }

    ; 2) 抑制期不新增 down:期望集合已记录,抑制结束时本函数会被再次调用补齐
    if (IsSkillHoldSuppressed()) {
        return
    }

    ; 3) 按 Python 下发顺序补按缺失键(鼠标键先于键盘键由 Python 侧排序保证)
    for index, key in SkillHoldDesiredOrder {
        if (SkillHeldKeys.Has(key)) {
            continue
        }
        ; 只在**真正发出** down 之后才记账:block_mouse 原地模式会吞掉鼠标键的 down,
        ; 若无条件记账,账本就会谎报"已按住",此后 Reconcile 永远跳过它 → 技能静默失效。
        ; 不记账则该键留在 desired 里,下一次 Reconcile(含关闭原地模式时)会重试。
        if (!SendDown(key)) {
            continue
        }
        SkillHeldKeys[key] := true
        SkillHeldOrder.Push(key)
    }
}

; 注销账本里的某个键(不发任何按键)。
; 用途:队列动作(管理键的 release:X / press:X)会**物理抬起**与持久持键同名的键,
; 此时账本必须同步失忆,否则 Reconcile 会因"账本说还按着"而不补按 → 持续技能永久失效。
; 返回 true 表示确实注销了(调用方据此决定是否立刻 Reconcile 补按)。
ForgetSkillHeldKey(key) {
    global SkillHeldKeys, SkillHeldOrder

    if (!SkillHeldKeys.Has(key)) {
        return false
    }
    SkillHeldKeys.Delete(key)
    idx := SkillHeldOrder.Length
    while (idx > 0) {
        if (SkillHeldOrder[idx] = key) {
            SkillHeldOrder.RemoveAt(idx)
        }
        idx -= 1
    }
    return true
}

; ---------- 队列级临时持键(管理键 hold_ms)----------
MarkManagedHoldTarget(key) {
    global ManagedHoldTargets
    ManagedHoldTargets[key] := true
}

ClearManagedHoldTarget(key) {
    global ManagedHoldTargets
    if (ManagedHoldTargets.Has(key)) {
        ManagedHoldTargets.Delete(key)
    }
}

; 清队列会丢弃还没执行的 release:target,必须在这里补发 up,否则该键真实卡死。
; 返回 true 表示补发的 up 里有键同时是持久持键(账本已同步失忆),
; 调用方需要决定是否 ReconcileSkillHoldKeys() 补按 —— 若紧接着就要
; ReleaseAllSkillHoldKeys()(完全停下),则不该补按。
ReleaseAllManagedHoldTargets() {
    global ManagedHoldTargets

    forgotHold := false
    for key, _ in ManagedHoldTargets {
        SendUp(key)
        ; 该 target 可能同时是 TriggerMode=2 持久持键:这里真实发了 up,
        ; 技能账本必须同步,否则 Reconcile 以为还按着 → 永不补按。
        if (ForgetSkillHeldKey(key)) {
            forgotHold := true
        }
    }
    ManagedHoldTargets := Map()
    return forgotHold
}

; SHUTDOWN 的实际退出动作(由一次性定时器调用,确保 WM_COPYDATA 已返回)
AhkShutdownNow() {
    ExitApp 0
}

; 安全收尾:LIFO 释放全部技能持键,并清空期望+实际账本(清空后不会被 Reconcile 重新按下)。
ReleaseAllSkillHoldKeys() {
    global SkillHoldDesiredOrder, SkillHeldKeys, SkillHeldOrder

    released := Map()
    idx := SkillHeldOrder.Length
    while (idx > 0) {
        key := SkillHeldOrder[idx]
        if (SkillHeldKeys.Has(key)) {
            SendUp(key)
            released[key] := true
        }
        idx -= 1
    }
    ; 兜底:未进 Order 的账本残留也一并释放
    for key, _ in SkillHeldKeys {
        if (!released.Has(key)) {
            SendUp(key)
        }
    }

    SkillHoldDesiredOrder := []
    SkillHeldKeys := Map()
    SkillHeldOrder := []
}

; ===============================================================================
; 命令接收 (WM_COPYDATA)
; ===============================================================================
WM_COPYDATA(wParam, lParam, msg, hwnd) {
    ; ⚠️ AHK v2 作用域:被本函数**赋值**的全局必须在这里声明(super-global 也一样),
    ; 且声明必须出现在第一次使用之前 —— RuntimeAcceptingActions 在 CMD_ENQUEUE 分支
    ; 里就要被读取,所以统一提到函数顶部,不能放在后面的 case 里。
    global RuntimeAcceptingActions

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
            ; 闸门关闭(Python 已进入 STOPPED)时拒绝入队:这是"按了 F8 绝不再有键打进游戏"
            ; 的最后一道防线,覆盖 join 超时后仍在跑的在飞回调。
            if (!RuntimeAcceptingActions) {
                return 0
            }
            parts := CachedStrSplit(param, ":", , 2)
            if (parts.Length >= 2) {
                priority := Integer(parts[1])
                action := parts[2]
                EnqueueAction(priority, action)
                return 1
            }
            return 0

        case CMD_SET_ACCEPTING_ACTIONS:
            ; SET_ACCEPTING_ACTIONS - 运行时闸门开关,参数 "true" / "false"
            ; 关闸 = **原子停止屏障**:同一条消息内完成 关闸+清队+停宏+释放全部持键。
            ; 只关闸不清场是不够的:已入队的动作仍会被 ProcessQueue 消费,
            ; MacroTick 仍以 5ms 周期真实发键 —— 靠 Python 侧后续命令补清必然有空窗。
            RuntimeAcceptingActions := (param = "true")
            if (!RuntimeAcceptingActions) {
                ClearQueue(-1)
            } else {
                ; 开闸与抑制解除同型:补按被推迟的持键(正常流程 desired 已空,是空转;
                ; Python 随后会重新声明完整集合)
                ReconcileSkillHoldKeys()
            }
            return 1

        case CMD_SHUTDOWN:
            ; SHUTDOWN - 优雅关闭。Python 的 Popen.terminate() 在 Windows 上是
            ; TerminateProcess,**不会**触发 OnExit,所以这里是唯一可靠的释放时机。
            ; 必须先原子清场:若只释放持键不清队列,本消息返回后、退出定时器触发前,
            ; 已到期的 ProcessQueue 可能先执行一次旧队列 → 退出前多发按键。
            ; ClearQueue(-1) 已包含 停宏(含宏持键)+队列级临时持键+技能持键 三类释放。
            RuntimeAcceptingActions := false
            ClearQueue(-1)
            ; 不在消息处理函数里直接 ExitApp:先让本次 SendMessage 正常返回 1,
            ; 再由一次性定时器退出,避免 Python 阻塞在无超时的 SendMessageW 上。
            SetTimer(AhkShutdownNow, -1)
            return 1

        case CMD_PAUSE:
            ; PAUSE - 暂停队列处理
            ; ⚠️ AHK v2 作用域:顶层 `global IsPaused := false` 只是 super-global,
            ; 函数内**赋值**仍需顶部 global 声明,否则写的是函数局部变量,
            ; ProcessQueue 的 `if (IsPaused) return` 闸门永远不会生效(实测已确认)。
            ; 本声明同时覆盖下方 CMD_RESUME 的赋值(AHK v2 声明是函数级的)。
            global IsPaused
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
                ; block_mouse 会吞掉鼠标键的 down(此时账本刻意不记账)。
                ; 关闭原地模式后必须补按,否则鼠标持久持键要等到下次 Z/F8 才恢复。
                ReconcileSkillHoldKeys()
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

        case CMD_SET_MACRO_STEPS:
            ; SET_MACRO_STEPS - 设置 AHK 端通用宏步骤
            ; 参数格式: 每行一个 type:data,如 "down:RButton`ndelay:50`nup:RButton"
            SetMacroSteps(param)
            return 1

        case CMD_START_MACRO:
            ; START_MACRO - 从第 1 步启动/重启通用宏循环
            ; 闸门关闭时拒绝:STOPPED/PAUSED 期间迟到的启动命令不得让 MacroTick 复活
            if (!RuntimeAcceptingActions) {
                return 0
            }
            StartMacro()
            return 1

        case CMD_STOP_MACRO:
            ; STOP_MACRO - 停止通用宏并按 LIFO 释放宏持键
            StopMacro()
            return 1

        case CMD_SET_SKILL_HOLD_KEYS:
            ; SET_SKILL_HOLD_KEYS - 声明式设置 TriggerMode=2 期望持键的完整集合
            ; 参数格式: 每行一个键名(顺序=按下顺序,鼠标键在前),如 "RButton`nq"
            ; 空 param = 释放全部技能持键(属安全清理命令,干跑/闸门关闭都必须放行)。
            ; 非空集合会按下按键,闸门关闭时**拒绝而非推迟**:若只记入 desired,
            ; 下次开闸的 Reconcile 会把它按下,但那已不是 Python 当时的最新意图。
            if (param != "" && !RuntimeAcceptingActions) {
                return 0
            }
            SetSkillHoldKeys(param)
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

; 过载时该动作可否被丢弃。
; 可丢:press / hold / click / delay —— 它们只是"想产生一次输入",过期即无意义。
; 不可丢:release: 和 cleanup: —— 它们是**状态恢复**动作,丢掉的后果是持久性的:
;   - 丢 release:key → 该键在游戏里一直按住(stuck key),用户只能重启客户端
;   - 丢 cleanup:key → ActiveManagedKeys 锁残留 → 该管理键此后永远点不出来
; notify: 同理不丢(Python 侧状态机依赖它)。
; 方向性安全:丢 hold: 而保留其 release: 只会多发一次无害的 SendUp;反过来则卡键。
IsDroppableAction(action) {
    if (InStr(action, "release:") = 1)
        return false
    if (InStr(action, "cleanup:") = 1)
        return false
    if (InStr(action, "notify:") = 1)
        return false
    ; delay_clear: 是管理键独占窗口的开关(ExecuteAction 里置 DelayClearOthers)。
    ; 丢掉它,管理键的 hold 窗口就不再清理竞争队列 → 独占语义失效。
    ; 今天它只进紧急队列(本来就不会被丢),这里补上是纵深防御。
    if (InStr(action, "delay_clear:") = 1)
        return false
    ; 已经发出过至少一个原子的连招:丢掉 = 打一半停手。未开始的 sequence: 仍可整条丢。
    if (InStr(action, "seqrun:") = 1)
        return false
    return true
}

; 队列超深时丢弃**最旧**的一个可丢动作。找不到可丢项就不丢(宁可暂时超深,
; 也绝不丢 release/cleanup 造成卡键)。返回是否真的丢掉了一项。
; 一个动作占多少个"执行位":sequence 有几个原子就要几个 tick 才发得完,其余动作算 1。
; 预算按原子算(= 真实执行时间),丢弃按整项算(= 保住序列的因果完整性)。
AtomCount(action) {
    if (InStr(action, "sequence:") = 1) {
        return StrSplit(SubStr(action, 10), ",").Length
    }
    if (InStr(action, "seqrun:") = 1) {
        return StrSplit(SubStr(action, 8), ",").Length
    }
    return 1
}

QueueByName(name) {
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue

    switch name {
        case "high":
            return HighQueue
        case "normal":
            return NormalQueue
        case "low":
            return LowQueue
    }
    return EmergencyQueue
}

PriorityOfQueueName(name) {
    switch name {
        case "high":
            return 1
        case "normal":
            return 2
        case "low":
            return 3
    }
    return 0
}

; 队列项:动作字符串 + 入队时刻。年龄 = QueueTickCount - tick,绑在项上,
; 清队列/丢队首都不会让别的项"继承"或"清零"年龄。
QueueItem(action) {
    global QueueTickCount
    return {action: action, tick: QueueTickCount}
}

; 下一个要执行的动作在哪条非紧急队列的队首(严格优先级:high → normal → low)。
; 无非紧急动作时返回 ""。
NextToExecuteQueueName() {
    global QueueCounts

    if (QueueCounts["high"] > 0) {
        return "high"
    }
    if (QueueCounts["normal"] > 0) {
        return "normal"
    }
    if (QueueCounts["low"] > 0) {
        return "low"
    }
    return ""
}

; 还在"排队等待"的原子总数(预算口径)。紧急队列不计入。两类豁免:
;
; 1. **每条队列队首的 seqrun:**(不只是全局队首)。seqrun 是"已承诺完成"的在飞工作
;    (不可丢),被更高优先级抢占时会滞留在自己队列的队首 —— 不同优先级可以**同时**
;    各有一条。只豁免全局队首的话,被抢占的那条(如 normal 的 99 个剩余原子)会把
;    预算永久顶爆:两条 seqrun 都不可丢、无法收敛,此后所有新动作一到就被丢。
;    结构不变量:seqrun 只可能出现在队首 —— 它由 PushFrontAction 放回位置 1,
;    第二条序列要开打必须先出队,而它排在第一条后面出不来。
;    因此每条队列最多一条 seqrun,豁免总量 ≤ 3 条 × 用户配置的序列长度,有界。
;
; 2. **全局队首**(下一个要执行的决策)。预算约束的是"排队等待",不是"一个决策本身
;    有多长":一条 20 键连招本来就要 20 个 tick 才发得完,把它算进预算会让它一遇到
;    别的动作就被整条丢掉 —— "配了长连招却永远放不出来"。
;
; 实际在飞上界 = 预算 + 全局队首 1 个决策 + ≤3 条已开打序列的剩余(长度由用户配置)。
PendingAtomCount() {
    global HighQueue, NormalQueue, LowQueue

    headName := NextToExecuteQueueName()
    total := 0
    for i, name in ["high", "normal", "low"] {
        q := QueueByName(name)
        for j, item in q {
            if (j = 1 && InStr(item.action, "seqrun:") = 1) {
                continue   ; 豁免 1:在飞序列
            }
            if (j = 1 && name = headName) {
                continue   ; 豁免 2:下一个要执行的决策
            }
            total += AtomCount(item.action)
        }
    }
    return total
}

; 从指定队列丢掉**最旧**的可丢项。
; ⚠️ protectTail:刚 push 进来的那一项不能丢。若整个队头都是不可丢动作,唯一"可丢"的
; 就是它 —— 丢了策略就从"丢最旧"退化成"丢最新",与"最新决策才反映当前局面"完全相反
; (实测:队列里 30 个 release: 之后,后续每个 press 都会在到达时被丢掉,30 个全军覆没)。
DropOldestDroppableFrom(queue, queueName, protectTail, protectHead := false) {
    global QueueCounts, TotalQueueCount, QueueStats

    limit := protectTail ? queue.Length - 1 : queue.Length
    if (limit < 1) {
        return false
    }
    ; protectHead:队首正是"下一个要执行的决策"(可能是已经发了一半的连招),
    ; 丢它就是打一半停手 —— 而且它也没算进预算,丢了也不会让预算更好看
    start := protectHead ? 2 : 1
    loop limit {
        if (A_Index < start) {
            continue
        }
        if (IsDroppableAction(queue[A_Index].action)) {
            queue.RemoveAt(A_Index)
            DecrementQueueCount(queueName)
            QueueStats["dropped"] := QueueStats["dropped"] + 1
            return true
        }
    }
    return false
}

; 过载可见化:静默丢弃会让用户以为"技能没触发是配置问题"。节流到每秒最多一条。
NotifyQueueOverload() {
    global LastOverloadNotifyAt, QueueStats, PendingOverloadNotify

    now := A_TickCount
    if (now - LastOverloadNotifyAt < 1000) {
        ; 被节流:**保留**待发标志,让下一 tick 继续尝试。
        ; (若在这里把标志清掉,一段"上一条通知刚发过 500ms"的短促过载就会
        ;  永远无人知晓 —— 丢弃发生了,却一条日志都没有。)
        return
    }
    LastOverloadNotifyAt := now
    PendingOverloadNotify := false
    SendEventToPython("queue_overload:" QueueStats["dropped"])
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
    if (ActiveManagedKeys.Count = 0) {
        SetMacroManagedSuppressed(false)
        ; 抑制解除:补齐被推迟的技能持键 down。
        ; "管理键临时 release:X/press:X 与持久持键同名"造成的误释放,靠 ExecuteAction 里的
        ; ForgetSkillHeldKey 把账本改脏,这次 Reconcile 才能看见缺口并补按(缺一不可)。
        ReconcileSkillHoldKeys()
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

    ; stationary_type 变化会改变 block_mouse 拦截范围,同 CMD_SET_STATIONARY 需要补按
    ReconcileSkillHoldKeys()
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
    global RuntimeAcceptingActions

    ; 🚧 最深的统一卡点:闸门关闭时任何来源(WM_COPYDATA / 管理键 / sequence 展开 /
    ; 未来新增调用方)都不得向队列写入。关闸即"完全停下",队列必须保持空。
    if (!RuntimeAcceptingActions) {
        return
    }

    ; 🔧 BUG修复(B5+B16): 拦截 sequence 类型,规范化成原子动作串
    ; 复用 DelayUntil 异步机制,避免 ExecuteSequence 中的同步 Sleep 阻塞所有队列。
    ;
    ; ⚠️ 这里**只规范化,不展开成多个队列项**。一次 sequence 是**一个决策**,
    ; 原子之间有因果关系,必须同生共死:
    ;   - 展开成 N 项时,后续任何普通入队都会按"丢最旧"从中间裁掉连招的前半段
    ;     (实测:20 步序列入队后再来一个普通动作,队头就从 k1 变成了 k6);
    ;   - 展开还会让"整个展开在同一次 WM_COPYDATA 里完成、ProcessQueue 一个也排不出去"
    ;     的长连招在**零过载**时自己吃掉自己。
    ; 作为单个队列项,它要么整体被丢,要么按序走完 —— 由 ExecuteAction 每 tick 推进
    ; 一个原子并把剩余部分放回队首(见 ACTION_SEQUENCE 分支)。
    ; 预算仍按原子计(见 AtomCount),所以延迟上限不会因为"一项 = N 个 tick"而失效。
    if (InStr(action, "sequence:") = 1) {
        sequenceData := SubStr(action, 10)
        parts := CachedStrSplit(sequenceData, ",")
        atoms := []
        for index, part in parts {
            part := Trim(part)
            if (part = "")
                continue
            if (InStr(part, "delay") = 1) {
                ; 🔧 BUG修复: 畸形 delay token(如 "delay" 无数字 / "delayx")会让 Integer()
                ; 抛未捕获异常,中断整条命令处理并丢弃该技能。校验后非法则跳过该项。
                numStr := SubStr(part, 6)
                if (numStr = "" || !IsInteger(numStr))
                    continue
                atoms.Push("delay:" Integer(numStr))
            } else {
                atoms.Push("press:" part)
            }
        }
        if (atoms.Length = 0) {
            return
        }
        if (atoms.Length = 1) {
            action := atoms[1]        ; 单原子退化成普通动作,省掉一层解释
        } else {
            joined := ""
            for i, a in atoms {
                joined .= (i > 1 ? "," : "") a
            }
            action := "sequence:" joined
        }
        ; 落到下面的 switch 正常入队(占一个队列项)
    }

    switch priority {
        case 0:
            ; 紧急队列(HP/MP 保命)不设上限:宁可积压也绝不丢救命药剂
            EmergencyQueue.Push(QueueItem(action))
            IncrementQueueCount("emergency")
            QueueStats["emergency"] := QueueStats["emergency"] + 1
        case 1:
            HighQueue.Push(QueueItem(action))
            IncrementQueueCount("high")
            QueueStats["high"] := QueueStats["high"] + 1
            EnforceQueueBudget("high")
        case 2:
            NormalQueue.Push(QueueItem(action))
            IncrementQueueCount("normal")
            QueueStats["normal"] := QueueStats["normal"] + 1
            EnforceQueueBudget("normal")
        case 3:
            LowQueue.Push(QueueItem(action))
            IncrementQueueCount("low")
            QueueStats["low"] := QueueStats["low"] + 1
            EnforceQueueBudget("low")
    }
}

; 全局原子预算 = 排队延迟上限(MAX_PENDING_ATOMS × 实际 tick)。超预算就丢最旧的可丢动作:
; 生产快于执行上限时,不丢的代价是延迟无限增长(实测 10 秒过载 → 打出去的是 10 秒前的决策)。
;
; pushedName:本次入队落到哪条队列 —— 只有那条队列的队尾需要保护(它是最新到达的)。
EnforceQueueBudget(pushedName) {
    ; ⚠️ AHK v2 作用域:PendingOverloadNotify 在本函数内被赋值,必须声明 global,
    ; 否则只会写进一个同名局部变量,通知永远发不出去(参见 AGENTS.md 4.3)
    global HighQueue, NormalQueue, LowQueue, MAX_PENDING_ATOMS, PendingOverloadNotify

    if (PendingAtomCount() <= MAX_PENDING_ATOMS) {
        return
    }
    dropped := false
    ; 上界只是防御性的:正常一次入队最多引发几次丢弃
    loop 256 {
        if (PendingAtomCount() <= MAX_PENDING_ATOMS) {
            break
        }
        headName := NextToExecuteQueueName()
        ; 先丢最低优先级:严格优先级出队下它们本来就排在最后,等得最久、最先过期。
        ; (持续的高优先级生产会把低优先级挤掉 —— 这是严格优先级的既定语义,
        ;  区别在于现在它变成"被丢弃并上报",而不是"无限期变陈旧后才发出去"。)
        if (DropOldestDroppableFrom(LowQueue, "low", pushedName = "low", headName = "low")) {
            dropped := true
        } else if (DropOldestDroppableFrom(NormalQueue, "normal", pushedName = "normal", headName = "normal")) {
            dropped := true
        } else if (DropOldestDroppableFrom(HighQueue, "high", pushedName = "high", headName = "high")) {
            dropped := true
        } else {
            break   ; 没有可丢的了(全是 release/cleanup、正在执行的队首、或最新到达的那一项)
        }
    }
    if (dropped) {
        PendingOverloadNotify := true   ; 实际通知推迟到 ProcessQueue,见该全局的说明
    }
}

; 把动作放回队首(序列推进时用)。这是"已经被接受的工作"回到队列,
; 不走闸门也不走预算 —— 它并没有新增待执行原子。
PushFrontAction(priority, action) {
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue

    ; tick 取当前值:seqrun 正在执行中,不参与年龄丢弃(不可丢),取值只为字段完整
    switch priority {
        case 0:
            EmergencyQueue.InsertAt(1, QueueItem(action))
            IncrementQueueCount("emergency")
        case 1:
            HighQueue.InsertAt(1, QueueItem(action))
            IncrementQueueCount("high")
        case 3:
            LowQueue.InsertAt(1, QueueItem(action))
            IncrementQueueCount("low")
        default:
            NormalQueue.InsertAt(1, QueueItem(action))
            IncrementQueueCount("normal")
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
            ; release:target 也随 emergency 一起丢失 → 必须补发 up,否则该键卡在按下。
            ; 与 case -1 不同:这里只清了 emergency,持久持键仍应保持,所以要补按。
            if (ReleaseAllManagedHoldTargets()) {
                ReconcileSkillHoldKeys()
            }
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
            StopMacro()
            ; 先释放队列级临时持键:被丢弃的 release:target 不会再执行,只有这里能补 up。
            ; 这里刻意**不**补按持久持键 —— 紧随其后的 ReleaseAllSkillHoldKeys() 就要全部释放。
            ReleaseAllManagedHoldTargets()
            ; 技能持久按住键与队列无关(走声明式命令),但 ClearQueue(-1) 语义是"完全停下",
            ; 必须同步释放并清空账本,否则 PAUSED 后仍有键按住
            ReleaseAllSkillHoldKeys()
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
; priority: 本动作是从哪条优先级队列取出来的 —— 序列推进时要把剩余部分放回同一条队列。
ExecuteAction(action, priority := 2) {
    ; 🔧 BUG修复(AHK v2 作用域): 把分散的 global 声明统一到函数顶部,
    ; 避免在 if 分支内零散声明导致维护困难
    global ACTION_CLEANUP, ACTION_PRESS, ACTION_SEQUENCE, ACTION_HOLD, ACTION_RELEASE, ACTION_MOUSE_CLICK, ACTION_DELAY, ACTION_NOTIFY
    global ACTION_SEQ_RUNNING
    global DelayUntil, DelayClearOthers
    global SkillHeldKeys, SkillHeldOrder, ManagedHoldTargets

    ; 🚀 处理清理标记（使用常量比较）
    if (InStr(action, ACTION_CLEANUP . ":")) {
        key := StrReplace(action, ACTION_CLEANUP . ":", "")
        ClearManagedKeyMark(key)
        return
    }

    ; 🚀 解析动作类型（使用缓存分割）
    parts := CachedStrSplit(action, ":", , 2)
    if parts.Length < 2 {
        ; 兼容旧的直接发送key的模式(Python 现在一律发 "press:key",此分支只为向后兼容)
        ; 同带前缀的 press:一样,裸键与持久持键同名时也会把它抬起,必须同步账本。
        if (SendPress(action, IsEmergencyAction(action))) {
            if (ForgetSkillHeldKey(action)) {
                ReconcileSkillHoldKeys()
            }
        }
        return
    }

    actionType := parts[1]
    actionData := parts[2]

    ; 序列推进:取出第一个原子执行,剩余部分原样放回**队首**。
    ; 序列作为一个队列项排队(见 EnqueueAction),因此它要么整体被丢,要么按序走完 ——
    ; 后续入队的普通动作不可能从中间把它裁断。
    if (actionType = ACTION_SEQUENCE || actionType = ACTION_SEQ_RUNNING) {
        ; ⚠️ 这里必须用裸 StrSplit,不能用 CachedStrSplit:下面的 RemoveAt(1) 会
        ; **原地修改**返回的数组,而缓存返回的是共享对象 —— 同一条序列第二次执行时
        ; 拿到的就是已被啃掉前几个原子的残骸(实测 sequence:q,w 第一次发 q,w、
        ; 第二次只剩 w、第三次什么都不发)。缓存结果一律只读,要改就自己 split。
        atoms := StrSplit(actionData, ",")
        if (atoms.Length = 0) {
            return
        }
        first := atoms.RemoveAt(1)
        if (atoms.Length > 0) {
            rest := ""
            for i, a in atoms {
                rest .= (i > 1 ? "," : "") a
            }
            ; 放回时改成 seqrun: —— 标记"已经开打",此后不再可丢(见 IsDroppableAction)
            PushFrontAction(priority, ACTION_SEQ_RUNNING ":" rest)
        }
        ExecuteAction(first, priority)
        return
    }

    ; 🚀 执行动作（直接常量比较，无函数调用开销）
    ; ⚠️ 队列动作是技能持键账本之外的**第二个物理写者**:管理键 hold_ms 会生成
    ; hold:target / release:target,hold_ms=0 生成 press:target(down+up)。若 target
    ; 与某个 TriggerMode=2 持久持键同名,这些动作会把持键物理抬起。必须同步账本,
    ; 否则 Reconcile 以为键还按着 → 永不补按 → 持续技能整局静默失效。
    if (actionType = ACTION_PRESS) {
        if (SendPress(actionData, IsEmergencyAction(action))) {
            ; press 的净效果是抬起;只有真正发出(未被替换/未被吞)时才注销账本
            if (ForgetSkillHeldKey(actionData)) {
                ReconcileSkillHoldKeys()   ; 抑制期内是空转,抑制结束时会再补按
            }
        }
    } else if (actionType = ACTION_HOLD) {
        if (SendDown(actionData)) {
            MarkManagedHoldTarget(actionData)
        }
    } else if (actionType = ACTION_RELEASE) {
        SendUp(actionData)
        ClearManagedHoldTarget(actionData)
        if (ForgetSkillHeldKey(actionData)) {
            ReconcileSkillHoldKeys()
        }
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

; 返回值语义(三个 Send* 函数统一):
;   true  = 已向全局输入流真实发出针对 key 本身的按键事件
;   false = 没有(被原地模式吞掉 / 被强制移动替换成别的键 / 走 ControlSend 未改全局键态)
; 调用方据此维护持键账本,避免"以为发了其实没发"或"以为没动其实已抬起"。
SendPress(key, forceMoveBypass := false) {
    ; 发送按键 (按下并释放，最小延时)
    global ForceMoveActive, ForceMoveReplacementKey, ForceMovePassthroughKeys, SendKeyMode, TargetWin

    ; 强制移动期间,白名单内的按键(位移技能如 RButton 闪现)正常发送,
    ; 其他按键全部替换为配置的替换键(通常是交互键 F,实现"边跑边捡装备/对话")
    ; HP/MP 等紧急动作永远绕过替换,否则会出现边跑路边把药剂替换成 F 的致命问题
    if (ForceMoveActive && !forceMoveBypass) {
        if (!ForceMovePassthroughKeys.Has(CachedStrLower(key))) {
            SendKeyInternal(ForceMoveReplacementKey)
            return false   ; 发的是替换键,key 本身没被碰过
        }
        ; 在白名单里 → 落到下面的正常发送路径
    }

    if (ShouldBlockMouseInStationary(key)) {
        return false
    }

    ; 正常按键处理
    if (ShouldAddShiftModifier(key)) {
        ; 带shift修饰符(+{key} 末尾仍是 key up)
        return SendKeyInternal("+" . key)
    }
    return SendKeyInternal(key)
}

SendKeyInternal(key) {
    ; 内部发送函数 - 根据模式选择发送方式
    global SendKeyMode, TargetWin

    if (SendKeyMode = "control" && TargetWin != "") {
        ; ControlSend模式 - 直接发送到目标窗口
        try {
            ControlSend FormatKeyForSend(key), , TargetWin
            ; ControlSend 只投递窗口消息,不改变全局键态 → 不影响持键账本
            return false
        } catch {
            ; 如果ControlSend失败，回退到直接模式
            return SendDirect(key)
        }
    }
    ; 直接发送模式 (SendInput)
    return SendDirect(key)
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
    ; 所有分支的净效果都是"key 最终处于抬起状态"
    return true
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
    if (ShouldBlockMouseInStationary(key)) {
        return false   ; 被原地模式吞掉 → 调用方不得记账,否则账本谎报"已按下"
    }
    Send "{" key " down}"
    return true
}

SendUp(key) {
    ; 释放按键(永不被任何模式拦截,否则就是卡键)
    Send "{" key " up}"
    return true
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

ShouldBlockMouseInStationary(key) {
    ; block_mouse 原地模式:吞掉自动发送的左右键 press/down,避免角色被鼠标技能带着移动。
    ; release 始终放行,避免切换模式或中止流程时产生卡键。
    global StationaryModeActive, StationaryModeType
    return StationaryModeActive && (StationaryModeType = "block_mouse") && IsMouseButtonKey(key)
}

IsMouseButtonKey(key) {
    lower := CachedStrLower(key)
    return (lower = "lbutton") || (lower = "rbutton") || (lower = "left") || (lower = "right")
}

; ExecuteSequence 已废弃: sequence 现在在 EnqueueAction 入口直接展开为
; 多个原子动作进入同优先级队列,复用 DelayUntil 异步机制,不再需要同步执行

ExecuteMouseClick(data) {
    ; 鼠标点击: "left" 或 "right" 或 "middle"
    if (ShouldBlockMouseInStationary(data)) {
        return
    }
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
    global ManagedKeysConfig, ActiveManagedKeys

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

    if (mode = "priority") {
        if (ManagedKeysConfig.Has(key)) {
            ManagedKeysConfig.Delete(key)
        }
        if (ActiveManagedKeys.Has(key)) {
            ActiveManagedKeys.Delete(key)
        }
        if (ActiveManagedKeys.Count = 0) {
            SetMacroManagedSuppressed(false)
            ReconcileSkillHoldKeys()  ; 抑制解除,补齐被推迟的技能持键
        }
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
            SetMacroSpecialSuppressed(false)
            ReconcileSkillHoldKeys()  ; 抑制解除,补齐被推迟的技能持键
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
        SetMacroSpecialSuppressed(true)
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
        SetMacroSpecialSuppressed(false)
        ; 抑制解除:补齐抑制期间被推迟的技能持键 down
        ReconcileSkillHoldKeys()
        SendEventToPython("special_key_pause:end")
    }

    ; 通知Python特殊按键状态
    SendEventToPython("special_key_up:" key)
}

; 🎯 管理按键处理（如RButton/e）- 拦截+延迟+映射 + 去重
HandleManagedKey(key) {
    global ManagedKeysConfig, EmergencyQueue, HighQueue, NormalQueue, LowQueue, IsPaused
    global RuntimeAcceptingActions

    ; 闸门关闭(STOPPED 过渡窗口 / PAUSED)= 完全停下:管理键不再重映射入队。
    ; $Hook 会吞掉原始按键,此时按管理键无任何输出,与"完全暂停"语义一致;
    ; STOPPED 的 Hook 注销完成后按键即恢复原生直通。
    if (!RuntimeAcceptingActions) {
        return
    }

    ; 🎯 去重机制：防止快速重复按键（Master方案学习）
    if (IsManagedKeyActive(key)) {
        return  ; 该按键序列正在处理中，忽略
    }

    ; 标记为处理中
    MarkManagedKeyActive(key)

    ; 管理按键要独占输入:中止当前宏持键,暂停宏解释器,待 emergency 序列末尾恢复
    SetMacroManagedSuppressed(true)

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
    stats := Format("stats:e={},h={},n={},l={},p={},d={}",
        QueueStats["emergency"],
        QueueStats["high"],
        QueueStats["normal"],
        QueueStats["low"],
        QueueStats["processed"],
        QueueStats["dropped"]
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
    global ActiveManagedKeys, SpecialKeysPressed, SpecialKeysPaused, ManagedKeysConfig

    ; 收集所有要删除的键
    keysToRemove := []
    for key, mode in RegisteredHooks {
        keysToRemove.Push(key)
    }

    ; 删除所有键(UnregisterHook 已经为每个 special 键单独清理了 SpecialKeysPressed/Paused)
    for index, key in keysToRemove {
        UnregisterHook(key)
    }

    ; 配置切换:所有 managed_keys 即将注销,残留 single-flight 锁/旧映射无意义
    ActiveManagedKeys := Map()
    ManagedKeysConfig := Map()
    SetMacroManagedSuppressed(false)

    ; 兜底:即使 per-key 注销有遗漏,也确保 special 状态彻底归零
    SpecialKeysPressed := Map()
    if (SpecialKeysPaused) {
        SpecialKeysPaused := false
        SetMacroSpecialSuppressed(false)
        SendEventToPython("special_key_pause:end")
    }

    ; 抑制状态已彻底归零:与期望集合对齐一次。
    ; STOPPED 路径下 Python 已先下发空集合,此处为无操作;配置热切换时则补齐被推迟的 down。
    ReconcileSkillHoldKeys()
}

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
