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
CoordMode "Mouse", "Screen"  ; Python 传入的是 DXGI/桌面屏幕坐标

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

; 🎯 intercept 键自动重复去重:key → 最近一次 down 的 MonotonicMs()。
; 事件发送改为微秒级返回(QueuedConnection + 短超时)后,热键伪线程立即结束,
; MaxThreadsPerHotkey=1 不再能吸收键盘自动重复 —— 按住 F8 会连发 intercept_key_down,
; Python 端每条都执行一次完整状态转换。配对注册 $key up 复位按下状态;
; 时间窗口是 up 边沿丢失时的自愈兜底(见 INTERCEPT_REPEAT_WINDOW_MS)。
global InterceptKeysPressed := Map()
; 判定自动重复的滚动窗口:Windows 最慢的重复初始延迟约 1000ms,取 1100 覆盖。
; 正常路径靠 up 边沿删除按下状态,窗口只在 up 丢失(如安全桌面吞掉钩子)时兜底 ——
; 键至多"死"1.1 秒,不会永久失效。
global INTERCEPT_REPEAT_WINDOW_MS := 1100
; 特殊键松开后只延迟**自动输入恢复**,物理 key-up 与 special_key_up 事件仍立即透传/回发。
; 0 = 旧行为(立即恢复);D4 序列模式当前配 125ms,避免宏复位后的 LButton 抢占闪避输入。
global SpecialKeyResumeDelayMs := 0

; 🎯 新增：管理按键配置存储
global ManagedKeysConfig := Map()   ; 存储管理按键的延迟和映射配置
global TargetWin := "" ; 目标窗口标识符
; direct/SendInput 是全局输入。显式配置目标后，每个新 down/click 都必须复核目标仍在
; 前台；ProcessQueue 还会在切走时释放所有在飞全局按键，避免它们粘到新前台应用。
; 空目标保留旧的“当前前台窗口”兼容语义。
global DirectTargetInputSuspended := false

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
; 普通 press 动作的 down→up 持续时间。由 Python 的 key_press_duration 批量同步。
global KeyPressDurationMs := 10
; press 不得在 ProcessQueue/MacroTick 中 Sleep：同步等待会阻塞 HP/MP 紧急队列，
; 而 F8/shutdown 中断 Sleep 后还可能在 ExitApp 前永久丢失 up。每个实际键以
; route+key 为维度记录最晚释放时刻；重叠 press 仍每次发 down，但只在最后
; 一个保持窗口到期后发 up，避免早到 up 剪断后到 down。
global TransientPressKeys := Map()
global TransientPressOrder := []
; 显式 down/up 的路由账本。control 模式的持久键必须在同一个目标窗口上配对
; ControlSend up；direct 模式则配对全局 Send up。只按 key 记一条，是因为现有
; 技能/宏/管理键账本本就把同一物理键视为单一所有权。
global PersistentPressRoutes := Map()

; 运行时闸门所有者。epoch 是 Python 分配的世代号，用于拒绝同一 owner
; 的迟到旧命令。不同 owner 只能在关闸时交接；真正开闸可再携带
; owner+epoch 复核，从而不会让旧洗练/寻路回调打开新模式的闸门。
global RuntimeOwner := "none"
global RuntimeOwnerEpoch := 0
; 每个 owner 的最高已接受世代是停止后仍保留的 tombstone。RESET 只清当前
; owner，不清这张表；否则超时后迟到的旧 owner:epoch 会在 STOPPED 里重新被接受。
global RuntimeOwnerEpochs := Map("main", 0, "affix", 0, "pathfinding", 0)
; F7 对洗练 owner 的本地 stop latch。与 PhysicalStopLatched(F8/主模式)分开，
; 但两者都拒绝后续 true，只有完整 RESET_RUNTIME 成功才解锁。
global RuntimeOwnerStopLatched := false

; 🎯 异步延迟机制
; 管理键独占延迟(delay_clear:)的结束时刻(单调毫秒),0 = 无。
; ⚠️ 这是**唯一**的全局延迟闸门,只服务管理键的独占窗口(期间清空非紧急队列)。
; 普通 delay(序列内或裸 delay:N)已改为**按队列** notBefore:只挡住自己所在的
; 优先级队列,别的队列照常执行 —— 低优先级的 delay 不再压住高优先级技能。
global ManagedDelayUntil := 0

; 🎯 AHK 端通用宏解释器:Python 只下发步骤,AHK 保证顺序、循环和中止释放
global MacroSteps := []
global MacroActive := false
global MacroIndex := 1
global MacroDueTime := 0
global MacroHeldKeys := Map()
global MacroHeldOrder := []
global MacroSpecialSuppressed := false
global MacroManagedSuppressed := false
; 仅是检查 MacroDueTime/推进下一步的轮询预算，不是宏步骤的保证间隔。
; Windows timer 会按系统粒度量化；需要可控节奏必须在 MacroSteps 写显式 delay。
global MACRO_POLL_INTERVAL_MS := 5

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
global CoordinateMouseHoldActive := false
global CoordinateMouseHoldPriority := -1

; 🎯 运行时闸门:Python 进入 STOPPED/PAUSED 时**第一件事**就是关掉它。
; 理由:UnifiedScheduler.stop() 只 join 2 秒,若在飞回调(如 OCR)超时未退出,
; 它之后仍可能继续下发命令,而此时 Python 侧的停止流程已经走完。
; 关闸 = 原子停止屏障(CMD_SET_ACCEPTING_ACTIONS(false) 同一条消息内 ClearQueue(-1)),
; 且闸门封住**所有**输入生产路径:EnqueueAction(最深卡点,覆盖 CMD_ENQUEUE/管理键/
; sequence 逐原子推进)、MacroTick、HandleManagedKey、CMD_START_MACRO、非空持键声明,
; 以及 IsSkillHoldSuppressed(压住 Reconcile 的补按环节)。
; 清队列/停宏/释放持键/空持键声明等安全清理命令永远放行,释放(up)永不被闸门拦截。
; 进程启动必须 fail-closed：只有主状态机进入 READY/RUNNING，或独立
; 洗练模式成功启动时，才能显式开闸。
global RuntimeAcceptingActions := false
; WM_COPYDATA 返回值:0 留给未处理/默认窗口过程,业务拒绝必须返回非零专用值。
; Python 据此区分“AHK 明确拒绝”与“没有取得协议层结果”。
global AHK_RESULT_REJECTED := 2

; 🎯 基于F8状态的智能窗口句柄缓存
global CurrentPythonWindow := "TorchLightAssistant_MainWindow_12345"  ; 启动时默认主窗口
global CachedPythonHwnd := 0  ; 缓存的Python窗口句柄
; AHK→Python 事件不能用命令通道的 500ms 预算:单次卡住 500ms 会直接饿死
; ProcessQueue/MacroTick/HP 药剂。Python 原生窗口过程只 emit Qt 队列信号,业务 handler
; 不在接收栈内执行,因此 50ms 对正常接收仍有充足余量。状态事件失败后退避 1 秒;
; 人手 intercept 绕过退避,stats 失败不武装退避(见 SendEventToPython 调用点)。
; 刻意不使用 SMTO_BLOCK,允许双向同步 WM_COPYDATA 在等待期间互相泵浦消息。
global PYTHON_SEND_TIMEOUT_MS := 50
global PYTHON_SEND_BACKOFF_MS := 1000
global PythonSendBackoffUntil := 0
; 状态类事件保留**最新状态**并由 timer 重试:monitor 供 Python OSD/账本对齐
; (AHK 本地物理账本独立保证按键替换);special pause:end 让 Python 停止丢弃
; 非紧急生产。普通观察事件允许丢失。
global PendingPythonStateEvents := Map()
; 人工边沿事件不能像 monitor 状态那样合并，否则 F8→Z 等连续操作会改变语义。
; Hotkey 线程只入 FIFO 后立即返回；timer 负责发送和重试。信封里的 session+seq 由
; Python 去重，解决 SendMessageTimeoutW 已投递但发送方超时后重试造成的双触发。
global PythonEventSession := String(MonotonicMs())
global PythonReliableEventSeq := 0
global PendingPythonReliableEvents := []
global MAX_PENDING_PYTHON_RELIABLE_EVENTS := 64
; 给停机 F8 预留容量，滚轮等高频业务事件不能把可靠队列占满后吞掉安全入口。
global PYTHON_RELIABLE_F8_RESERVE := 4
; 动态 Hook 存在时，F8 是“停机意图”而不是普通 toggle。在 Python
; 可靠信封仍在 AHK FIFO 时保持 pending，合并 GUI 卡顿期间用户重复按下的 F8。
global F8StopIntentPending := false
; 主状态机已开始进入 READY，直到完整 STOPPED Hook 清理成功前保持 true。
; 不能用 RegisteredHooks.Count 代替：Python 原先先开闸后注册首个 Hook，二者之间
; 的物理 F8 会被误判成 STOPPED 启动键。显式 armed 标记把状态意图提前到开闸之前。
global MainModeArmed := false
; STOPPED→READY 的启动 F8 若仍物理按住，后续 down 是 Windows auto-repeat，
; 不能误判成“再次按 F8 停机”。正常由永久 up Hook 清除；安全桌面吞掉 up 时，
; 临时物理键态轮询在检测到释放后自愈。
global MainModeF8AwaitRelease := false
global MAIN_MODE_F8_RELEASE_POLL_MS := 25
; 物理 F8 的本地停机锁存。它先于关闸/清场置位，完整动态 Hook 清理成功前，
; 任何迟到的 SET_ACCEPTING_ACTIONS(true) 都不得重新打开输入。
global PhysicalStopLatched := false
global PythonReliableRetryUntil := 0
global PythonReliableRetryDelayMs := 100
global PYTHON_RELIABLE_RETRY_BASE_MS := 100
global PYTHON_RELIABLE_RETRY_MAX_MS := 1000
global PythonStatsRetryAt := 0
global PYTHON_STATS_RETRY_MS := 2000
global SMTO_ABORTIFHUNG := 0x0002
global SMTO_ERRORONEXIT := 0x0020

; 统计信息
global QueueStats := Map(
    "emergency", 0,
    "high", 0,
    "normal", 0,
    "low", 0,
    "processed", 0,
    "dropped", 0,     ; 过载丢弃(入队速度超过执行上限,预算裁剪)
    "expired", 0      ; 过期丢弃(排队等待超过 STALE_MS,出队时判定)
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
; 年龄必须**绑在每个队列项上**(入队时记录时刻),不能按队列计数:
; 每队列一个计数器有两个实测到的错误 ——
;   (a) 清队列不重置计数,PAUSED 后新入队的动作"继承"旧年龄,等 1 个 tick 就被丢;
;   (b) 计数只描述队首,队首被丢后重新从 0 数,排在后面、等了同样久的动作照样被执行。
; 判定在**出队时**做:取到的项年龄 ≥ STALE_MS 且可丢 → 丢弃换下一个。
; 过期决策没有价值,执行一个 500ms 前的决策比不执行更糟。
;
; 时钟用 MonotonicMs()(GetTickCount64,单调毫秒,含系统休眠时间),不用"调度 tick 数":
; tick 只在定时器成功触发时才走,系统休眠或 AHK 线程被长时间阻塞后恢复,
; 按 tick 数算,真实等了几秒的动作仍会被当成"年轻"照常执行 —— 契约是真实等待时间。
global STALE_MS := 500

; 单调毫秒时钟 —— 本文件所有时刻/时长比较的唯一来源。
; ⚠️ 不能用 A_TickCount:它是 32 位 GetTickCount,约 49.7 天回绕。回绕瞬间
; now - at 变成巨大负数:过期判定失效(跨回绕的旧动作被照常执行)、通知节流
; 被压制(最长再等 49.7 天)、ManagedDelayUntil / MacroDueTime 的比较同样冻结。
; GetTickCount64 无回绕,同样包含系统休眠时间,符合"真实等待"契约。
; 禁止在本文件再写裸 A_TickCount(tests/test_ahk_queue_throughput.py 有静态守卫)。
MonotonicMs() {
    return DllCall("Kernel32\GetTickCount64", "UInt64")
}

; 已经发出过至少一个原子的序列(剩余部分被放回队首)。与未开始的 sequence: 区分开:
; 未开始的可以整条丢(原子性);已经开打的**不能**丢 —— 那是连招打一半停手。
global ACTION_SEQ_RUNNING := "seqrun"
global LastOverloadNotifyAt := 0     ; 过载通知节流(避免刷屏)
; 丢弃发生在 EnqueueAction 里,而它常在 WM_COPYDATA 处理中执行 —— 此刻 Python 正在
; SendMessageTimeoutW 里等这条消息返回。虽然双向发送现允许消息泵重入,这里仍推迟到
; ProcessQueue 再上报,避免在命令处理栈内引入不必要的嵌套事件。
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
global ACTION_MOUSE_CLICK_AT := "mouse_click_at"
global ACTION_NOTIFY := "notify"
global MAX_MOUSE_CLICK_HOLD_MS := 5000

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
    global ManagedDelayUntil, TotalQueueCount, QueueCounts
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue
    global QueueStats, IsPaused, SpecialKeysPaused, RuntimeAcceptingActions
    global PendingOverloadNotify, STALE_MS

    ; direct 模式的目标窗口安全边界也负责释放“切走前已经按下”的键。
    ; 放在空队列快速返回之前，才能覆盖 TriggerMode=2 持键但当前没有排队动作的情况。
    if (!RefreshDirectTargetSafety()) {
        return
    }

    ; 物理 F8 / PAUSED 的关闸可能中断一个较早的 timer 线程。入口先挡住后续 tick；
    ; ClearQueue(-1) 与 ExecuteAction 的纵深检查负责已经在飞的那一个 tick。
    if (!RuntimeAcceptingActions) {
        return
    }

    ; 过载通知:丢弃发生在 WM_COPYDATA 上下文,通知推迟到这里(定时器上下文)发出。
    ; 对开闸状态放在“空队列”快速返回之前，确保队列刚被普通清空时通知不丢；
    ; 关闸状态由上面的安全 guard 先返回，旧诊断留到下一次合法开闸后再发送。
    ; 标志由 NotifyQueueOverload 在**真正发出**时才清除(被节流时保留,下一 tick 重试)。
    if (PendingOverloadNotify) {
        NotifyQueueOverload()
    }

    ; 🚀 性能优化：快速检查 - 如果没有任何任务且不在管理键独占延迟中，直接返回
    if (TotalQueueCount = 0 && ManagedDelayUntil = 0) {
        return
    }

    ; 🎯 管理键独占延迟窗口(delay_clear:)。⚠️ 只有管理键走这里 ——
    ; 普通 delay(序列内/裸 delay:N)是按队列的 notBefore(见 NextToExecuteQueueName),
    ; 只挡自己的优先级队列,不会再把高优先级技能一起压住。
    if (ManagedDelayUntil > 0) {
        if (MonotonicMs() < ManagedDelayUntil) {
            ; 管理键独占:延迟期间清空非紧急队列(E 键闪避/强力技的独占语义)
            if (QueueCounts["high"] > 0 || QueueCounts["normal"] > 0 || QueueCounts["low"] > 0) {
                ClearNonEmergencyQueues()
            }
            ; 🔧 BUG修复: 独占延迟期间放行 HP/MP 救命药剂。本延迟检查在 emergency 取队之前
            ; return,管理键较长的独占窗口(前 delay + hold_ms + 后 delay)会把救命药剂
            ; 整段压住 → 血量危急时漏吃药。
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
            ManagedDelayUntil := 0
        }
    }

    ; 🚀 索急队列永远执行（使用计数器检查）
    if (QueueCounts["emergency"] > 0) {
        ; 坐标长按的 release 可带 notBefore。尚未到期时允许后来的救命药剂越过，
        ; 不能让鼠标保持时间压住 HP/MP；每次仍最多执行一个 ready 项。
        now := MonotonicMs()
        loop EmergencyQueue.Length {
            if (EmergencyQueue[A_Index].notBefore <= now) {
                item := EmergencyQueue.RemoveAt(A_Index)
                DecrementQueueCount("emergency")
                ExecuteAction(item.action, 0)
                QueueStats["processed"] := QueueStats["processed"] + 1
                return
            }
        }
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

    ; 🚀 正常模式:按优先级出队。取到的项若已过期(真实等待 ≥ STALE_MS)且可丢,
    ; 直接丢弃换下一个 —— 执行一个 500ms 前的决策(如那时该放的技能)比不执行更糟。
    ; 年龄绑在项上,所以"排在被丢队首后面、等了同样久"的项也会被逐个判掉,
    ; 不会像旧的每队列计数那样重新从 0 数。一个 tick 内仍最多**执行**一个动作;
    ; 丢弃只是扔掉字符串,不发键,连丢多个不影响节奏。
    ; ⚠️ 过期丢弃计入 expired(不是 dropped):两者原因不同,诊断建议也不同,
    ; 混在一起会让"只是配了个长 delay"的用户收到"入队速度超上限"的错误诊断。
    loop 64 {   ; 防御上界(预算约束下积压有限,正常一次最多丢十几个)
        name := NextToExecuteQueueName()
        if (name = "") {
            break
        }
        q := QueueByName(name)
        item := q.RemoveAt(1)
        DecrementQueueCount(name)
        if (MonotonicMs() - item.at >= STALE_MS && IsDroppableAction(item.action)) {
            QueueStats["expired"] := QueueStats["expired"] + 1
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

; 宏解释器独立 tick:只推进 AHK 端宏状态机,不占用队列与任何延迟闸门
SetTimer(MacroTick, MACRO_POLL_INTERVAL_MS)

; 队列观测:每秒把实时深度 + 累计丢弃计数推给 Python(OSD 展示)。
; 主动推送,不加轮询命令 —— Python 端 get_stats 请求路径已删除。
SetTimer(SendStatsToPython, 1000)
SetTimer(FlushPendingPythonEvents, 100)

; 进程退出兜底:仅覆盖**正常退出路径**(ExitApp / CMD_SHUTDOWN / 用户手动关脚本)。
; ⚠️ Windows 的 TerminateProcess(Python Popen.terminate/kill)不会触发 OnExit ——
; 所以 Python 停 AHK 必须先发 CMD_SHUTDOWN 优雅关闭,terminate 只是超时兜底
; (走到 terminate 时持键可能残留,无法避免)。
OnExit(AhkOnExitHandler)
AhkOnExitHandler(reason, code) {
    try {
        ReleaseAllTransientPressKeys(false)
    } catch {
        ; 退出路径不再抛错
    }
    try {
        ReleaseAllSkillHoldKeys()
    } catch {
    }
    try {
        ReleaseMacroHeldKeys()
    } catch {
    }
    try {
        ReleaseAllManagedHoldTargets()
    } catch {
    }
    try {
        ReleaseAllPersistentPressKeys()
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

    ; 目标窗口被切走时从第 1 步安全暂停；ProcessQueue 通常会先发现，但这里
    ; 自身也做检查，不能依赖两个 timer 的触发顺序。
    if (!RefreshDirectTargetSafety()) {
        return
    }

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
    if (MacroDueTime > 0 && MonotonicMs() < MacroDueTime) {
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
        MacroDueTime := MonotonicMs() + Max(Integer(data), 1)
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
        ; 普通 press 的延迟 up 还未到期时不能补按同名持键：否则
        ; 当前 Reconcile 发出的 down 会被之后的临时 up 立即剪断。
        if (IsTransientGlobalPressActive(key)) {
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

    ; AHK 键名不区分大小写，但 Map 默认区分。Python 配置归一化会
    ; 把 shift 保存为小写，而修饰键序列解析为 Shift；必须按物理键语义
    ; 不区分大小写地污染账本，否则临时 up 后持键会永久丢失。
    lower := CachedStrLower(key)
    heldKey := ""
    for candidate, _ in SkillHeldKeys {
        if (CachedStrLower(candidate) = lower) {
            heldKey := candidate
            break
        }
    }
    if (heldKey = "") {
        return false
    }
    SkillHeldKeys.Delete(heldKey)
    idx := SkillHeldOrder.Length
    while (idx > 0) {
        if (CachedStrLower(SkillHeldOrder[idx]) = lower) {
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
ReleaseAllManagedHoldTargets(preserveNonEmergencyCoordinate := false) {
    global ManagedHoldTargets, CoordinateMouseHoldActive, CoordinateMouseHoldPriority

    forgotHold := false
    preserved := Map()
    for key, _ in ManagedHoldTargets {
        if (preserveNonEmergencyCoordinate && key = "LButton"
            && CoordinateMouseHoldActive && CoordinateMouseHoldPriority > 0) {
            preserved[key] := true
            continue
        }
        SendUp(key)
        ; 该 target 可能同时是 TriggerMode=2 持久持键:这里真实发了 up,
        ; 技能账本必须同步,否则 Reconcile 以为还按着 → 永不补按。
        if (ForgetSkillHeldKey(key)) {
            forgotHold := true
        }
    }
    ManagedHoldTargets := preserved
    if (!preserveNonEmergencyCoordinate || preserved.Count = 0) {
        CoordinateMouseHoldActive := false
        CoordinateMouseHoldPriority := -1
    }
    return forgotHold
}

ReleaseCoordinateMouseHoldIfCleared(priority) {
    global CoordinateMouseHoldActive, CoordinateMouseHoldPriority

    if (!CoordinateMouseHoldActive) {
        return false
    }
    clearsHold := priority = -1
        || (priority = -2 && CoordinateMouseHoldPriority > 0)
        || priority = CoordinateMouseHoldPriority
    if (!clearsHold) {
        return false
    }

    SendUp("LButton")
    ClearManagedHoldTarget("LButton")
    CoordinateMouseHoldActive := false
    CoordinateMouseHoldPriority := -1
    return true
}

ClearCoordinateMouseHoldState(key, priority) {
    global CoordinateMouseHoldActive, CoordinateMouseHoldPriority

    if (key = "LButton" && CoordinateMouseHoldActive
        && priority = CoordinateMouseHoldPriority) {
        CoordinateMouseHoldActive := false
        CoordinateMouseHoldPriority := -1
    }
}

; SHUTDOWN 的实际退出动作(由一次性定时器调用,确保 WM_COPYDATA 已返回)
AhkShutdownNow() {
    ExitApp 0
}

; 安全收尾:LIFO 释放全部技能持键,并清空期望+实际账本(清空后不会被 Reconcile 重新按下)。
ReleaseAllSkillHoldKeys(clearDesired := true) {
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

    if (clearDesired) {
        SkillHoldDesiredOrder := []
    }
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
    global RuntimeAcceptingActions, AHK_RESULT_REJECTED

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
            ; 空值是有意义的清理操作：切换到未配置目标的配置时，不能继续
            ; 沿用上一份配置的窗口并把 ControlSend 发到旧进程。
            TargetWin := param
            return 1

        case CMD_ACTIVATE:
            ; ACTIVATE - 异步激活目标窗口。WinActivate 受默认 SetWinDelay(100ms)影响,
            ; 不能放在同步 WM_COPYDATA 处理栈内,否则无意义地占用命令超时预算。
            global TargetWin

            if (TargetWin != "" && WinExist(TargetWin)) {
                SetTimer(ActivateTargetWindow, -1)
                return 1
            }
            return AHK_RESULT_REJECTED

        case CMD_ENQUEUE:
            ; ENQUEUE - 添加到队列
            ; 参数格式: "priority:action"
            ; 闸门关闭(Python 已进入 STOPPED)时拒绝入队:这是"按了 F8 绝不再有键打进游戏"
            ; 的最后一道防线,覆盖 join 超时后仍在跑的在飞回调。
            if (!RuntimeAcceptingActions) {
                return AHK_RESULT_REJECTED
            }
            parts := CachedStrSplit(param, ":", , 2)
            if (parts.Length >= 2) {
                if (!IsInteger(parts[1])) {
                    return AHK_RESULT_REJECTED
                }
                priority := Integer(parts[1])
                action := parts[2]
                if (priority < 0 || priority > 3 || !IsValidQueuedAction(action)) {
                    return AHK_RESULT_REJECTED
                }
                return AcceptPythonQueuedAction(priority, action) ? 1 : AHK_RESULT_REJECTED
            }
            return AHK_RESULT_REJECTED

        case CMD_SET_ACCEPTING_ACTIONS:
            ; SET_ACCEPTING_ACTIONS - 运行时闸门开关,参数 "true" / "false"
            ; "arm_main" 先原子关闸清场，再声明主模式正在进入 READY。
            if (param = "arm_main") {
                return ArmMainMode() ? 1 : AHK_RESULT_REJECTED
            }
            ; 关闸 = **原子停止屏障**:同一条消息内完成 关闸+清队+停宏+释放全部持键。
            ; 只关闸不清场是不够的:已入队的动作仍会被 ProcessQueue 消费,
            ; MacroTick 仍会在下一次 poll 真实发键 —— 靠 Python 侧后续命令补清必然有空窗。
            ; 兼容旧参数 true/false；新路径使用 true:owner:epoch 在开闸的
            ; 最后一个临界点复核所有者，拒绝旧世代回调打开新运行的闸门。
            return SetRuntimeActionGateFromParam(param) ? 1 : AHK_RESULT_REJECTED

        case CMD_SHUTDOWN:
            ; SHUTDOWN - 优雅关闭。Python 的 Popen.terminate() 在 Windows 上是
            ; TerminateProcess,**不会**触发 OnExit,所以这里是唯一可靠的释放时机。
            ; 必须先原子清场:若只释放持键不清队列,本消息返回后、退出定时器触发前,
            ; 已到期的 ProcessQueue 可能先执行一次旧队列 → 退出前多发按键。
            ; ClearQueue(-1) 已包含 停宏(含宏持键)+队列级临时持键+技能持键 三类释放。
            ResetRuntime()
            ; 不在消息处理函数里直接 ExitApp:先让本次 SendMessage 正常返回 1,
            ; 再由一次性定时器退出,先让 Python 的 SendMessageTimeoutW 正常返回。
            SetTimer(AhkShutdownNow, -1)
            return 1

        case CMD_RESET_RUNTIME:
            ; 单条原子 STOPPED 事务：一次有界 WM_COPYDATA 即完成所有
            ; AHK 安全清理，不再让 Python 在真挂死时串行等待多个 500ms。
            return ResetRuntime() ? 1 : AHK_RESULT_REJECTED

        case CMD_SET_RUNTIME_OWNER:
            ; 参数 owner:epoch，owner ∈ none/main/affix/pathfinding。
            return SetRuntimeOwner(param) ? 1 : AHK_RESULT_REJECTED

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
                return RegisterHook(parts[1], parts[2]) ? 1 : AHK_RESULT_REJECTED
            }
            return AHK_RESULT_REJECTED

        case CMD_HOOK_UNREGISTER:
            ; HOOK_UNREGISTER - 取消Hook
            return UnregisterHook(param) ? 1 : AHK_RESULT_REJECTED

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
                nextActive := (parts[1] = "true")
                nextModeType := CachedStrLower(Trim(parts[2]))
                ; 激活是危险方向，只接受已知模式；关闭始终安全放行并清掉旧类型。
                if (nextActive && !IsSupportedStationaryMode(nextModeType))
                    return AHK_RESULT_REJECTED
                StationaryModeActive := nextActive
                StationaryModeType := nextActive ? nextModeType : ""
                ; block_mouse 会吞掉鼠标键的 down(此时账本刻意不记账)。
                ; 关闭原地模式后必须补按,否则鼠标持久持键要等到下次 Z/F8 才恢复。
                ReconcileSkillHoldKeys()
                return 1
            }
            return AHK_RESULT_REJECTED

        case CMD_SET_FORCE_MOVE_KEY:
            ; SET_FORCE_MOVE_KEY - 设置强制移动键
            ; 参数格式: "key" 例如: "a"，空字符串表示清空配置
            global ForceMoveKey
            ForceMoveKey := param  ; 接受任何值，包括空字符串
            ReconcileForceMoveState()
            return 1

        case CMD_SET_FORCE_MOVE_STATE:
            ; SET_FORCE_MOVE_STATE - 设置强制移动状态
            ; 参数格式: "true" 或 "false"
            ; 闸门关闭(STOPPED/PAUSED)时拒绝 true:QueuedConnection 下迟到的激活
            ; 不得在 monitor Hook 已注销后复活按键替换。两个方向最终都从
            ; AHK 物理账本重算，Python 回发仅作纵深对齐，不是执行态权威。
            global ForceMoveActive
            if (param != "true" && param != "false") {
                return AHK_RESULT_REJECTED
            }
            if (param = "true" && !RuntimeAcceptingActions) {
                return AHK_RESULT_REJECTED
            }
            ; AHK 物理 monitor 账本是执行态权威。Python 命令只触发重对齐，
            ; 不盲写 param；否则迟到 down 回发可在物理 up 后重新点亮替换。
            ReconcileForceMoveState()
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
            return AHK_RESULT_REJECTED

        case CMD_CLEAR_HOOKS:
            ; CLEAR_HOOKS - 清空所有可配置的Hook（保留 F8/F7/F9 永久根热键）
            return ClearAllConfigurableHooks() ? 1 : AHK_RESULT_REJECTED

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
                return AHK_RESULT_REJECTED
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
                return AHK_RESULT_REJECTED
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
            return AHK_RESULT_REJECTED
    }

    ; 未识别的命令
    return AHK_RESULT_REJECTED
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

; Python 的 pause:start 只是提前止流优化，不能作为正确性边界：状态事件按 channel
; 合并，快速点按时 start 可能在 GUI 消费前被 end 覆盖。AHK 在同步 CMD_ENQUEUE
; 接收边界依据本地 SpecialKeysPaused 再判一次；非安全动作按“已成功丢弃”返回，
; 避免发送方把设计内丢弃误报为传输/协议失败。
AcceptPythonQueuedAction(priority, action) {
    global SpecialKeysPaused

    if (SpecialKeysPaused && !IsAllowedDuringPause(action)) {
        return true
    }
    EnqueueAction(priority, action)
    return true
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
    ; delay_clear: 是管理键独占窗口的开关(ExecuteAction 里置 ManagedDelayUntil)。
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

; 队列项:动作字符串 + 入队时刻 + 最早可执行时刻(都是单调毫秒)。
; 年龄 = MonotonicMs() - at,绑在项上,清队列/丢队首都不会让别的项"继承"或"清零"年龄。
; notBefore > 0 表示"在此之前本队列不出队"(delay 只挡自己所在的优先级队列):
; 只有序列推进/裸 delay 转出的 seqrun: 等待项会带非零 notBefore,普通入队恒为 0。
QueueItem(action) {
    return {action: action, at: MonotonicMs(), notBefore: 0}
}

; 下一个要执行的动作在哪条非紧急队列的队首(严格优先级:high → normal → low)。
; 队首"未到期"(notBefore 还没到)的队列**整条跳过** —— 这就是按队列延时:
; normal 序列里的 delay 只挡 normal,high/low 照常轮到(优先级不再被 delay 打破)。
; 无可执行动作时返回 ""。
NextToExecuteQueueName() {
    global QueueCounts, HighQueue, NormalQueue, LowQueue

    now := MonotonicMs()
    if (QueueCounts["high"] > 0 && HighQueue[1].notBefore <= now) {
        return "high"
    }
    if (QueueCounts["normal"] > 0 && NormalQueue[1].notBefore <= now) {
        return "normal"
    }
    if (QueueCounts["low"] > 0 && LowQueue[1].notBefore <= now) {
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

; 丢弃可见化:静默丢弃会让用户以为"技能没触发是配置问题"。节流到每秒最多一条。
; 事件带两个**累计**计数:overload(预算裁剪)与 expired(等待过期)。
; 原因必须分开:混成一条"入队速度超上限"会让"只是配了个长 delay"的用户
; 收到错误诊断,去调根本没问题的技能生产率。
NotifyQueueOverload() {
    global LastOverloadNotifyAt, QueueStats, PendingOverloadNotify

    now := MonotonicMs()
    if (now - LastOverloadNotifyAt < 1000) {
        ; 被节流:**保留**待发标志,让下一 tick 继续尝试。
        ; (若在这里把标志清掉,一段"上一条通知刚发过 500ms"的短促丢弃就会
        ;  永远无人知晓 —— 丢弃发生了,却一条日志都没有。)
        return
    }
    LastOverloadNotifyAt := now
    PendingOverloadNotify := false
    ; 诊断观测失败即丢，不得用它武装状态/人工事件的退避。
    SendEventToPython("queue_drop:overload=" QueueStats["dropped"] ",expired=" QueueStats["expired"], false, false)
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
    if (MonotonicMs() - ActiveManagedKeys[key] > MANAGED_KEY_TIMEOUT_MS) {
        ActiveManagedKeys.Delete(key)
        return false
    }
    return true
}

; 标记管理按键为活跃状态
MarkManagedKeyActive(key) {
    global ActiveManagedKeys
    ActiveManagedKeys[key] := MonotonicMs()
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
    global CachedHpKey, CachedMpKey, StationaryModeType, SpecialKeyResumeDelayMs
    global KeyPressDurationMs

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
                    ; 批量配置只预置类型，不激活。未知/空值都清空，不能沿用旧 profile。
                    normalizedMode := CachedStrLower(value)
                    StationaryModeType := IsSupportedStationaryMode(normalizedMode) ? normalizedMode : ""
                case "special_key_resume_delay_ms":
                    if (IsInteger(value)) {
                        SpecialKeyResumeDelayMs := Min(Max(Integer(value), 0), 1000)
                    }
                case "key_press_duration":
                    if (IsInteger(value)) {
                        KeyPressDurationMs := Min(Max(Integer(value), 1), 1000)
                    }
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

    ; 坐标长按的 release 可能正在任一非紧急队首等待；清队前必须补 up。
    ReleaseCoordinateMouseHoldIfCleared(-2)
    
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

    ; 🚧 最深的统一卡点:闸门关闭时任何来源(WM_COPYDATA / 管理键 / sequence 逐原子推进 /
    ; 未来新增调用方)都不得向队列写入。关闸即"完全停下",队列必须保持空。
    if (!RuntimeAcceptingActions) {
        return
    }

    ; 🔧 BUG修复(B5+B16): 拦截 sequence 类型,规范化成原子动作串
    ; (序列内 delay 由 ExecuteAction 转成按队列 notBefore 等待,无同步 Sleep)。
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
    PushFrontItem(priority, QueueItem(action))
}

; 把等待中的项放回队首:notBefore 之前本队列不出队(只挡自己,不挡别的队列)。
; 序列里的 delay 原子与裸 delay:N 都经此转成 seqrun: 等待项 —— 剩余原子可为空
; (尾部 delay,如 "q,delay100"),空 seqrun: 就是纯等待哨兵,到期出队即完成,
; **不会**再次重置延时(notBefore 只在这里设一次)。
PushFrontWait(priority, action, waitMs) {
    item := QueueItem(action)
    item.notBefore := MonotonicMs() + waitMs
    PushFrontItem(priority, item)
}

PushFrontItem(priority, item) {
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue

    ; at 取当前时刻:seqrun 正在执行中,不参与年龄丢弃(不可丢),取值只为字段完整
    switch priority {
        case 0:
            EmergencyQueue.InsertAt(1, item)
            IncrementQueueCount("emergency")
        case 1:
            HighQueue.InsertAt(1, item)
            IncrementQueueCount("high")
        case 3:
            LowQueue.InsertAt(1, item)
            IncrementQueueCount("low")
        default:
            NormalQueue.InsertAt(1, item)
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
    global ManagedDelayUntil

    switch priority {
        case 0:
            TotalQueueCount := TotalQueueCount - QueueCounts["emergency"]
            QueueCounts["emergency"] := 0
            EmergencyQueue := []
            ; emergency 里 cleanup:key 动作丢失,同步清 single-flight 锁
            ActiveManagedKeys := Map()
            ; release:target 也随 emergency 一起丢失 → 必须补发 up,否则该键卡在按下。
            ; 与 case -1 不同:这里只清了 emergency,持久持键仍应保持,所以要补按。
            if (ReleaseAllManagedHoldTargets(true)) {
                ReconcileSkillHoldKeys()
            }
        case 1:
            ReleaseCoordinateMouseHoldIfCleared(1)
            TotalQueueCount := TotalQueueCount - QueueCounts["high"]
            QueueCounts["high"] := 0
            HighQueue := []
        case 2:
            ReleaseCoordinateMouseHoldIfCleared(2)
            TotalQueueCount := TotalQueueCount - QueueCounts["normal"]
            QueueCounts["normal"] := 0
            NormalQueue := []
        case 3:
            ReleaseCoordinateMouseHoldIfCleared(3)
            TotalQueueCount := TotalQueueCount - QueueCounts["low"]
            QueueCounts["low"] := 0
            LowQueue := []
        case -1:
            ReleaseCoordinateMouseHoldIfCleared(-1)
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
            ; 队列已全空,继续保留管理键独占延迟语义不干净:窗口残留会在下次入队前
            ; 继续清掉非紧急队列(包括 hold 模式 resume 时的 hold:N)
            ManagedDelayUntil := 0
            ; press down 已脱离队列，其延迟 up 在独立账本里。完全清场必须
            ; 先立即释放，不能等原定时器，否则 F8 后仍有在飞 key-up/卡键窗口。
            ReleaseAllTransientPressKeys(false)
            StopMacro()
            ; 先释放队列级临时持键:被丢弃的 release:target 不会再执行,只有这里能补 up。
            ; 这里刻意**不**补按持久持键 —— 紧随其后的 ReleaseAllSkillHoldKeys() 就要全部释放。
            ReleaseAllManagedHoldTargets()
            ; 技能持久按住键与队列无关(走声明式命令),但 ClearQueue(-1) 语义是"完全停下",
            ; 必须同步释放并清空账本,否则 PAUSED 后仍有键按住
            ReleaseAllSkillHoldKeys()
            ; 纵深兜底：任何在 SendDown 成功与上层账本登记之间被异常打断的 route
            ; 也必须配平，不能跨 STOPPED 残留。
            ReleaseAllPersistentPressKeys()
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
    global ACTION_CLEANUP, ACTION_PRESS, ACTION_SEQUENCE, ACTION_HOLD, ACTION_RELEASE
    global ACTION_MOUSE_CLICK, ACTION_MOUSE_CLICK_AT, ACTION_DELAY, ACTION_NOTIFY
    global ACTION_SEQ_RUNNING
    global ManagedDelayUntil, RuntimeAcceptingActions
    global SkillHeldKeys, SkillHeldOrder, ManagedHoldTargets

    ; ProcessQueue 可能在取出 item 后被物理 F8 热键线程中断。返回原 timer 后不能
    ; 执行这个已脱离全局队列的局部 item；release 由关闸的 ClearQueue(-1) 统一补齐。
    if (!RuntimeAcceptingActions) {
        return
    }

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
            return   ; 空 seqrun: = 纯等待哨兵(尾部 delay),到期出队即完成,无键可发
        }
        first := atoms.RemoveAt(1)
        rest := ""
        for i, a in atoms {
            rest .= (i > 1 ? "," : "") a
        }
        ; delay 原子:把剩余部分(可为空哨兵)带 notBefore 放回队首 —— 只挡本队列,
        ; 其他优先级照常执行。**不**设全局闸门(那是 delay_clear 的管理键独占专利)。
        ; 尾部 delay(rest 为空)也要放哨兵占住队首,否则 "q,delay100" 的延时直接消失。
        if (InStr(first, "delay:") = 1) {
            waitStr := SubStr(first, 7)
            waitMs := IsInteger(waitStr) ? Integer(waitStr) : 0
            if (priority > 0 && waitMs > 0) {
                PushFrontWait(priority, ACTION_SEQ_RUNNING ":" rest, waitMs)
                return
            }
            ; emergency 队列不支持延时(救命动作不等待);0/畸形 delay 原子直接跳过,
            ; 剩余原子放回队首下一 tick 继续
            if (rest != "") {
                PushFrontAction(priority, ACTION_SEQ_RUNNING ":" rest)
            }
            return
        }
        if (atoms.Length > 0) {
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
        ClearCoordinateMouseHoldState(actionData, priority)
        if (ForgetSkillHeldKey(actionData)) {
            ReconcileSkillHoldKeys()
        }
    } else if (actionType = ACTION_MOUSE_CLICK) {
        ExecuteMouseClick(actionData)
    } else if (actionType = ACTION_MOUSE_CLICK_AT) {
        ExecuteMouseClickAt(actionData, priority)
    } else if (actionType = ACTION_DELAY) {
        ; 兼容路径:裸 delay:N 队列项(非序列内)。转成本队列的等待哨兵:
        ; 只挡自己所在的优先级队列,到期后哨兵出队即完成,不会再次重置延时。
        ; emergency(priority 0)不支持延时 —— 救命动作永不等待,直接忽略。
        waitMs := IsInteger(actionData) ? Integer(actionData) : 0
        if (priority > 0 && waitMs > 0) {
            PushFrontWait(priority, ACTION_SEQ_RUNNING ":", waitMs)
        }
    } else if (actionType = "delay_clear") {
        ; 🔧 管理按键专用延迟:唯一的全局延迟闸门。延迟期间清空非紧急队列,
        ; 保证管理键独占执行(仍放行 HP/MP 救命药剂,见 ProcessQueue)
        ManagedDelayUntil := MonotonicMs() + Integer(actionData)
    } else if (actionType = ACTION_NOTIFY) {
        ; 🎯 发送通知到Python
        QueuePythonReliableEvent(actionData)
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

    preferredMode := (SendKeyMode = "control" && TargetWin != "") ? "control" : "direct"
    usedDirect := false
    if (!StartTransientPress(key, preferredMode, TargetWin, &usedDirect)) {
        return false
    }
    ; ControlSend 只投递目标窗口，不改变全局键态。返回值继续
    ; 表示“key 本身是否写入了全局输入流”，供持键账本调用方判定。
    return usedDirect
}

SendDirect(key) {
    ; 直接发送也走同一套非阻塞 press 账本，不再有“普通键支持时长，
    ; 修饰键/鼠标键却隐式瞬时”的分支差异。
    global TargetWin

    usedDirect := false
    return StartTransientPress(key, "direct", TargetWin, &usedDirect) && usedDirect
}

CanEmitDirectInput(target := "") {
    ; SendInput/Click 修改的是系统全局输入流。配置了目标选择器时，目标不在
    ; 前台就必须 fail-closed；未配置目标时保留 direct 的传统前台语义。
    if (target = "") {
        return true
    }
    try {
        return WinActive(target) != 0
    } catch {
        return false
    }
}

RefreshDirectTargetSafety() {
    global RuntimeAcceptingActions, SendKeyMode, TargetWin
    global DirectTargetInputSuspended

    targetInactive := RuntimeAcceptingActions
        && TargetWin != ""
        && !CanEmitDirectInput(TargetWin)
    shouldSuspend := targetInactive
        && (SendKeyMode = "direct" || HasGlobalInputInFlight())

    if (shouldSuspend) {
        if (!DirectTargetInputSuspended) {
            DirectTargetInputSuspended := true
            ; up 永远不受目标前台检查：切走时必须把已经写入全局键态的边沿
            ; 立刻配平。技能 desired 保留，切回后只补当前仍期望的持键。
            ReleaseAllTransientPressKeys(false)
            AbortMacroRuntime()
            ReleaseAllManagedHoldTargets()
            ReleaseAllSkillHoldKeys(false)
        }
        ; control/ControlSend 可以继续向后台目标发送；这里只禁止仍会写系统
        ; 全局输入流的路径。direct 模式则暂停消费，等用户切回目标。
        return SendKeyMode != "direct"
    }

    if (DirectTargetInputSuspended) {
        DirectTargetInputSuspended := false
        if (RuntimeAcceptingActions) {
            ReconcileSkillHoldKeys()
        }
    }
    return true
}

HasGlobalInputInFlight() {
    global TransientPressKeys, PersistentPressRoutes

    for id, entry in TransientPressKeys {
        if (entry.affectsGlobal) {
            return true
        }
    }
    for id, entry in PersistentPressRoutes {
        if (entry.mode = "direct") {
            return true
        }
    }
    return false
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

; 把 AHK 标准修饰符前缀拆成真实按下顺序。支持 +/^/!/#
; (Shift/Ctrl/Alt/Win) 及组合前缀；单独 "+" 仍是字面加号键。
; "Ctrl+X" 这类文本别名不是仓库的 AHK 标准键名，旧代码会把它当作
; 混合文本发出。这种语义无法可靠配对 up，因此安全拒绝，调用方改用 ^x。
ParseTransientPressKeys(key) {
    key := Trim(key)
    if (key = "") {
        return false
    }
    if (key = "+") {
        return ["+"]
    }

    keys := []
    index := 1
    while (index < StrLen(key)) {
        symbol := SubStr(key, index, 1)
        modifierKey := ""
        switch symbol {
            case "+":
                modifierKey := "Shift"
            case "^":
                modifierKey := "Ctrl"
            case "!":
                modifierKey := "Alt"
            case "#":
                modifierKey := "LWin"
        }
        if (modifierKey = "") {
            break
        }
        keys.Push(modifierKey)
        index += 1
    }

    baseKey := SubStr(key, index)
    if (baseKey = "" || InStr(baseKey, "+") || InStr(baseKey, "^")
        || InStr(baseKey, "!") || InStr(baseKey, "#")) {
        return false
    }
    keys.Push(baseKey)
    return keys
}

TransientPressId(mode, target, key) {
    return mode "|" target "|" CachedStrLower(key)
}

IsTransientModifierKey(key) {
    lower := CachedStrLower(key)
    return lower = "shift" || lower = "ctrl" || lower = "alt"
        || lower = "lwin" || lower = "rwin"
}

SendTransientKeyEdge(mode, target, key, isDown) {
    edge := isDown ? "down" : "up"
    try {
        if (mode = "control") {
            ; ControlSend 只投递键盘消息；鼠标按钮须使用 ControlClick 的
            ; D/U 边沿，并用 NA 保持后台目标不激活。
            buttons := Map("lbutton", "Left", "rbutton", "Right",
                "mbutton", "Middle", "xbutton1", "X1", "xbutton2", "X2")
            lower := StrLower(key)
            if (buttons.Has(lower)) {
                ; 保持时间由 press 账本/显式 up 控制，不使用 ControlClick 的隐式 Sleep。
                previousDelay := SetControlDelay(-1)
                try {
                    ControlClick , target, , buttons[lower], 1, isDown ? "NA D" : "NA U"
                } finally {
                    SetControlDelay previousDelay
                }
            } else {
                ControlSend "{" key " " edge "}", , target
            }
        } else {
            Send "{" key " " edge "}"
        }
        return true
    } catch {
        return false
    }
}

TrackTransientPressKey(mode, target, key, dueAt, affectsGlobal) {
    global TransientPressKeys, TransientPressOrder

    id := TransientPressId(mode, target, key)
    if (TransientPressKeys.Has(id)) {
        entry := TransientPressKeys[id]
        entry.due := Max(entry.due, dueAt)
        return
    }
    TransientPressKeys[id] := {
        mode: mode,
        target: target,
        key: key,
        due: dueAt,
        affectsGlobal: affectsGlobal
    }
    TransientPressOrder.Push(id)
}

StartTransientPress(key, preferredMode, target, &usedDirect) {
    global KeyPressDurationMs

    usedDirect := false
    keys := ParseTransientPressKeys(key)
    if (!IsObject(keys) || keys.Length = 0) {
        return false
    }

    previousCritical := A_IsCritical
    Critical "On"
    try {
        mode := preferredMode
        routeTarget := (mode = "control") ? target : ""
        sentKeys := []

        ; ControlSend 中途失败时，先在同一目标上 LIFO 补 up，再将整个
        ; chord 回退到 direct。不允许把半条 Control chord 留在目标窗口里。
        if (mode = "control") {
            for index, actualKey in keys {
                if (!SendTransientKeyEdge(mode, routeTarget, actualKey, true)) {
                    idx := sentKeys.Length
                    while (idx > 0) {
                        SendTransientKeyEdge(mode, routeTarget, sentKeys[idx], false)
                        idx -= 1
                    }
                    mode := "direct"
                    routeTarget := ""
                    sentKeys := []
                    break
                }
                sentKeys.Push(actualKey)
            }
        }

        if (mode = "direct") {
            ; ControlSend 失败后可以退回 direct，但只有目标此刻仍在前台才安全。
            ; 超时/失败不应把按键改投到用户正在操作的其他应用。
            if (!CanEmitDirectInput(target)) {
                return false
            }
            for index, actualKey in keys {
                if (!SendTransientKeyEdge(mode, "", actualKey, true)) {
                    idx := sentKeys.Length
                    while (idx > 0) {
                        SendTransientKeyEdge(mode, "", sentKeys[idx], false)
                        idx -= 1
                    }
                    return false
                }
                sentKeys.Push(actualKey)
            }
            usedDirect := true
        }

        dueAt := MonotonicMs() + KeyPressDurationMs
        for index, actualKey in keys {
            TrackTransientPressKey(mode, routeTarget, actualKey, dueAt, usedDirect)
        }

        ; 临时 press 的最终 up 会剪断同一 route 上的键。不仅 base，
        ; Shift/Ctrl/Alt/Win 修饰键也可能同时是 TriggerMode=2 持键；先让
        ; 所有参与本次 chord 的持键账本失忆，release timer 才能补按。
        for index, actualKey in keys {
            ForgetSkillHeldKey(actualKey)
        }
        ScheduleTransientPressRelease()
        return true
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

ScheduleTransientPressRelease() {
    global TransientPressKeys

    SetTimer(ReleaseDueTransientPressKeys, 0)
    if (TransientPressKeys.Count = 0) {
        return
    }
    now := MonotonicMs()
    nextDue := 0
    for id, entry in TransientPressKeys {
        if (nextDue = 0 || entry.due < nextDue) {
            nextDue := entry.due
        }
    }
    SetTimer(ReleaseDueTransientPressKeys, -Max(nextDue - now, 1))
}

ReleaseDueTransientPressKeys() {
    global TransientPressKeys, TransientPressOrder

    previousCritical := A_IsCritical
    Critical "On"
    try {
        now := MonotonicMs()
        reconcileNeeded := false
        ; 即使一条 chord 的 base 在更早的重叠 press 中已存在，释放时也必须
        ; 先抬普通键、后抬 modifier。不能单纯依赖“首次出现顺序”做 LIFO。
        loop 2 {
            releaseModifiers := (A_Index = 2)
            idx := TransientPressOrder.Length
            while (idx > 0) {
                id := TransientPressOrder[idx]
                if (!TransientPressKeys.Has(id)) {
                    TransientPressOrder.RemoveAt(idx)
                    idx -= 1
                    continue
                }
                entry := TransientPressKeys[id]
                if (entry.due <= now
                    && IsTransientModifierKey(entry.key) = releaseModifiers) {
                    SendTransientKeyEdge(entry.mode, entry.target, entry.key, false)
                    reconcileNeeded := true
                    TransientPressKeys.Delete(id)
                    TransientPressOrder.RemoveAt(idx)
                }
                idx -= 1
            }
        }
        ScheduleTransientPressRelease()
        if (reconcileNeeded) {
            ReconcileSkillHoldKeys()
        }
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

ReleaseAllTransientPressKeys(reconcile := true) {
    global TransientPressKeys, TransientPressOrder

    SetTimer(ReleaseDueTransientPressKeys, 0)
    reconcileNeeded := false
    loop 2 {
        releaseModifiers := (A_Index = 2)
        idx := TransientPressOrder.Length
        while (idx > 0) {
            id := TransientPressOrder[idx]
            if (TransientPressKeys.Has(id)) {
                entry := TransientPressKeys[id]
                if (IsTransientModifierKey(entry.key) = releaseModifiers) {
                    SendTransientKeyEdge(entry.mode, entry.target, entry.key, false)
                    reconcileNeeded := true
                }
            }
            idx -= 1
        }
    }
    TransientPressKeys := Map()
    TransientPressOrder := []
    if (reconcile && reconcileNeeded) {
        ReconcileSkillHoldKeys()
    }
}

IsTransientGlobalPressActive(key) {
    global TransientPressKeys

    lower := CachedStrLower(key)
    for id, entry in TransientPressKeys {
        if (CachedStrLower(entry.key) = lower) {
            return true
        }
    }
    return false
}

PersistentPressId(key) {
    return CachedStrLower(key)
}

TrackPersistentPressRoute(key, mode, target) {
    global PersistentPressRoutes, TransientPressKeys, TransientPressOrder

    PersistentPressRoutes[PersistentPressId(key)] := {
        key: key,
        mode: mode,
        target: target
    }
    ; 成功的显式 down 接管同一路由的临时 press，最终 up 由持键所有者负责。
    ; 只撤销该键的旧 release；同一 chord 的其他键、其他目标仍按原时刻释放。
    transientId := TransientPressId(mode, target, key)
    if (TransientPressKeys.Has(transientId)) {
        TransientPressKeys.Delete(transientId)
        for index, id in TransientPressOrder {
            if (id = transientId) {
                TransientPressOrder.RemoveAt(index)
                break
            }
        }
        ScheduleTransientPressRelease()
    }
}

ReleaseAllPersistentPressKeys() {
    global PersistentPressRoutes

    for id, entry in PersistentPressRoutes {
        SendTransientKeyEdge(entry.mode, entry.target, entry.key, false)
    }
    PersistentPressRoutes := Map()
}

SendDown(key, forceDirect := false, directTarget := "") {
    ; 按住按键。control 模式也必须记录 route，后续 release/STOPPED 才能在
    ; 原目标上配对 up，而不是误向当前前台窗口发送全局边沿。
    global TargetWin, SendKeyMode

    if (ShouldBlockMouseInStationary(key)) {
        return false   ; 被原地模式吞掉 → 调用方不得记账,否则账本谎报"已按下"
    }
    ; down 和接管必须原子完成，旧 release timer 不能插在实际 down 与记账之间。
    previousCritical := A_IsCritical
    Critical "On"
    try {
        if (!forceDirect && SendKeyMode = "control" && TargetWin != "") {
            if (SendTransientKeyEdge("control", TargetWin, key, true)) {
                TrackPersistentPressRoute(key, "control", TargetWin)
                return true
            }
            ; ControlSend 失败只能在目标仍是前台时回退 global SendInput。
        }
        targetForDirect := directTarget != "" ? directTarget : TargetWin
        if (!CanEmitDirectInput(targetForDirect)
            || !SendTransientKeyEdge("direct", "", key, true)) {
            return false
        }
        TrackPersistentPressRoute(key, "direct", "")
        return true
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

SendUp(key) {
    ; 释放永不被目标/抑制门禁拦截。优先沿 down 的真实 route 配对；没有账本
    ; 时仍发一个全局 up 作为历史状态/异常路径的防卡键兜底。
    global PersistentPressRoutes

    id := PersistentPressId(key)
    if (PersistentPressRoutes.Has(id)) {
        entry := PersistentPressRoutes[id]
        PersistentPressRoutes.Delete(id)
        return SendTransientKeyEdge(entry.mode, entry.target, entry.key, false)
    }
    return SendTransientKeyEdge("direct", "", key, false)
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

IsSupportedStationaryMode(modeType) {
    normalized := CachedStrLower(Trim(modeType))
    return normalized = "shift_modifier" || normalized = "block_mouse"
}

; 滚轮"键":没有 up 边沿(实测 "$WheelUp up" 可注册但永远不触发),也没有键盘
; 自动重复 —— 每个刻度都是独立的用户动作。intercept 去重与 up 配对都必须跳过它,
; 否则 up 永远不来,连续滚动会被 1.1s 兜底窗口吞掉(BOSS 键/原地键配滚轮时)。
IsWheelKey(key) {
    lower := CachedStrLower(key)
    return (lower = "wheelup") || (lower = "wheeldown")
        || (lower = "wheelleft") || (lower = "wheelright")
}

; ExecuteSequence 已废弃: sequence 现在在 EnqueueAction 入口规范化为单个队列项,
; 由 ExecuteAction 每 tick 推进一个原子;序列内 delay 走按队列 notBefore,无同步执行

ExecuteMouseClick(data) {
    ; 鼠标点击: "left" 或 "right" 或 "middle"
    global TargetWin, SendKeyMode

    if (ShouldBlockMouseInStationary(data)) {
        return false
    }
    if (SendKeyMode = "control" && TargetWin != "") {
        buttons := Map("left", "LButton", "right", "RButton", "middle", "MButton")
        button := buttons.Has(CachedStrLower(data))
            ? buttons[CachedStrLower(data)]
            : data
        usedDirect := false
        return StartTransientPress(button, "control", TargetWin, &usedDirect)
    }
    if (!CanEmitDirectInput(TargetWin)) {
        return false
    }
    Click data
    return true
}

IsValidQueuedAction(action) {
    global ACTION_MOUSE_CLICK_AT

    parts := CachedStrSplit(action, ":", , 2)
    if (parts.Length < 2) {
        return action != ACTION_MOUSE_CLICK_AT
    }
    if (parts[1] != ACTION_MOUSE_CLICK_AT) {
        return true
    }
    return ParseMouseClickAt(parts[2], &x, &y, &holdMs)
}

ParseMouseClickAt(data, &x, &y, &holdMs) {
    global MAX_MOUSE_CLICK_HOLD_MS

    values := CachedStrSplit(data, ",")
    if (values.Length != 3 || !IsInteger(values[1]) || !IsInteger(values[2])
        || !IsInteger(values[3])) {
        return false
    }

    x := Integer(values[1])
    y := Integer(values[2])
    holdMs := Integer(values[3])
    virtualLeft := SysGet(76)
    virtualTop := SysGet(77)
    virtualRight := virtualLeft + SysGet(78)
    virtualBottom := virtualTop + SysGet(79)
    return x >= virtualLeft && x < virtualRight
        && y >= virtualTop && y < virtualBottom
        && holdMs >= 0 && holdMs <= MAX_MOUSE_CLICK_HOLD_MS
}

ExecuteMouseClickAt(data, priority) {
    global ACTION_RELEASE, TargetWin
    global ManagedHoldTargets, CoordinateMouseHoldActive, CoordinateMouseHoldPriority

    if (!ParseMouseClickAt(data, &x, &y, &holdMs)) {
        return false
    }

    ; Python 入队前已核对目标，但动作可能在 AHK 队列里稍后才执行。执行时
    ; 再做一次 fail-closed 校验：坐标点击必须有显式目标，目标仍是前台窗口，
    ; 且屏幕坐标仍位于该窗口当前客户区的半开边界内。
    ; 捕获本动作的目标：WM_COPYDATA 可以重入 timer 线程并更新全局
    ; TargetWin，后续的 click/down 不得悄然改用另一个目标的校验结果。
    target := TargetWin
    if (!IsPointInsideTargetClient(target, x, y)) {
        return false
    }

    if (holdMs = 0) {
        return ClickMouseAtOnce(x, y, target)
    }

    ; 同一物理 LButton 不能同时存在两条待释放账本；跨优先级重叠会让较早
    ; release 提前抬起较新的 hold。保持 single-flight，后续动作安全拒绝。
    if (CoordinateMouseHoldActive || ManagedHoldTargets.Has("LButton")) {
        return false
    }

    ; down 后把不可丢 release 放回同一优先级队首并设置 notBefore。
    ; 不用 Sleep，因此其它优先级与 HP/MP 在保持窗口内仍可运行。
    if (!PressMouseAt(x, y, target)) {
        return false
    }
    MarkManagedHoldTarget("LButton")
    CoordinateMouseHoldActive := true
    CoordinateMouseHoldPriority := priority
    PushFrontWait(priority, ACTION_RELEASE ":LButton", holdMs)
    return true
}

IsPointInsideTargetClient(target, x, y) {
    ; WinGetClientPos 返回虚拟桌面屏幕坐标，所以左/上副屏的负坐标
    ; 不需要特别转换。先用 WinActive 取得确切的前台匹配 HWND，避免
    ; 同一 selector 匹配多个窗口时从 WinExist 取到另一个客户区。
    if (target = "") {
        return false
    }
    try {
        hwnd := WinActive(target)
        if (!hwnd) {
            return false
        }
        WinGetClientPos(&clientX, &clientY, &clientWidth, &clientHeight,
            "ahk_id " hwnd)
        return clientWidth > 0 && clientHeight > 0
            && x >= clientX && x < clientX + clientWidth
            && y >= clientY && y < clientY + clientHeight
    } catch {
        return false
    }
}

ClickMouseAtOnce(x, y, target) {
    if (ShouldBlockMouseInStationary("LButton")) {
        return false
    }
    ; 靠近真实 Click 再复核一次，缩小入队校验与全局输入之间的时间窗。
    if (!IsPointInsideTargetClient(target, x, y)) {
        return false
    }
    Click x, y
    return true
}

PressMouseAt(x, y, target) {
    if (ShouldBlockMouseInStationary("LButton")) {
        return false
    }
    if (!IsPointInsideTargetClient(target, x, y)) {
        return false
    }
    MouseMove x, y, 0
    ; SendDown 也使用这份已捕获 target 做最后的前台检查，不读可重入更新的全局值。
    return SendDown("LButton", true, target)
}

; ===============================================================================
; Hook管理
; ===============================================================================
RegisterHook(key, mode) {
    global RegisteredHooks

    key := Trim(key)
    mode := CachedStrLower(Trim(mode))
    if (key = "" || !IsSupportedHookMode(mode)) {
        return false
    }
    ; 滚轮只有离散 notch，没有可触发的物理 up 边沿。special/monitor 都是按住状态
    ; 协议，允许注册会让 pause/force-move 永久卡在 true；intercept/priority 仍可用。
    if (IsWheelKey(key) && (mode = "special" || mode = "monitor")) {
        return false
    }

    key_upper := StrUpper(key)
    is_root := (key_upper = "F8" || key_upper = "F7" || key_upper = "F9")
    ; 永久根热键只能使用 intercept。即使调用方绕过 Python 的双层保留键检查，
    ; 也不能用 priority/special 等模式覆盖 down handler；root 不进 RegisteredHooks，
    ; 一旦覆盖，ClearAllConfigurableHooks 也无法发现或恢复。
    if (is_root && mode != "intercept") {
        return false
    }

    ; 动态 Hook 重复注册是幂等操作；同一键换模式必须先显式注销，避免两个模式
    ; 同时存活而 RegisteredHooks 只能记录其中一个。
    if (!is_root && RegisteredHooks.Has(key)) {
        return RegisteredHooks[key] = mode
    }

    downEnabled := false
    upEnabled := false
    try {
        switch mode {
            case "intercept":
                Hotkey("$" key, (*) => HandleInterceptKey(key), "On")
                downEnabled := true
                ; up 配对:提供自动重复去重的复位边沿,并拦掉孤儿 up(down 已被吞)。
                ; 滚轮键跳过:up 变体注册不报错但永远不触发(实测),配了也没意义。
                if (!IsWheelKey(key)) {
                    Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key), "On")
                    upEnabled := true
                }

            case "priority":
                Hotkey("$" key, (*) => HandleManagedKey(key), "On")
                downEnabled := true

            case "special":
                Hotkey("~" key, (*) => HandleSpecialKeyDown(key), "On")
                downEnabled := true
                Hotkey("~" key " up", (*) => HandleSpecialKeyUp(key), "On")
                upEnabled := true

            case "monitor":
                Hotkey("~" key, (*) => HandleMonitorKey(key), "On")
                downEnabled := true
                Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key), "On")
                upEnabled := true

            case "block":
                Hotkey("$" key, (*) => {}, "On")
                downEnabled := true
        }
    } catch {
        ; 事务回滚:第二个(up)变体失败时,不能留下只有 down 的半注册 Hook。
        if (upEnabled) {
            try DisableHookUp(key, mode)
        }
        if (downEnabled) {
            try DisableHookDown(key, mode)
        }
        return false
    }

    ; 永久根热键不进入动态登记，因此 ClearAllConfigurableHooks 永远不会碰它们。
    if (!is_root) {
        RegisteredHooks[key] := mode
    }
    return true
}

UnregisterHook(key) {
    ; 🔧 AHK v2 作用域:函数内对全局变量赋值会自动 local 化,顶部统一 global 声明
    global RegisteredHooks, SpecialKeysPressed, SpecialKeysPaused
    global ManagedKeysConfig, ActiveManagedKeys
    global MonitorKeysState, ForceMoveKey, ForceMoveActive
    global PendingPythonStateEvents, InterceptKeysPressed

    ; 注销不存在的动态 Hook 是幂等成功；永久根热键也不在此表中。
    if (!RegisteredHooks.Has(key)) {
        return true
    }

    mode := RegisteredHooks[key]

    downDisabled := false
    upDisabled := false
    try {
        DisableHookDown(key, mode)
        downDisabled := true
        if (HookModeHasUpEdge(key, mode)) {
            DisableHookUp(key, mode)
            upDisabled := true
        }
    } catch {
        ; 若只关掉了 down,恢复它；登记和所有运行时状态保持原样，调用方收到 rejected。
        if (downDisabled && !upDisabled) {
            try EnableHookDown(key, mode)
        }
        return false
    }

    ; intercept 按住期间被注销(如 STOPPED 注销 Z):up Hotkey 已关,按下状态等不到
    ; 复位边沿。残留虽有 1.1s 时间窗兜底,仍会把注销后 1.1s 内重注册的第一次按下
    ; 误判为自动重复 —— 这里直接清掉。
    if (mode = "intercept" && InterceptKeysPressed.Has(key)) {
        InterceptKeysPressed.Delete(key)
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
            ; 注销是配置/STOPPED 清理,必须立即结束且取消旧的一次性恢复定时器。
            ; 否则旧 timer 可能在下一轮 special key 按住时误解除新抑制。
            SetTimer(FinishSpecialKeyPause, 0)
            SpecialKeysPaused := false
            SetMacroSpecialSuppressed(false)
            ReconcileSkillHoldKeys()  ; 抑制解除,补齐被推迟的技能持键
            ; 当前可能位于 Python→AHK 的 WM_COPYDATA 栈内,只入 pending,由 timer 回发。
            QueuePythonStateEvent("special_pause", "special_key_pause:end", false)
        }
    }

    ; monitor 模式与 special 一样依赖物理 key-up。按住期间注销后 up Hook 已不存在,
    ; 必须直接归零 AHK 状态,并用最新 up 覆盖可能滞留的 pending down。
    if (mode = "monitor") {
        key_upper := StrUpper(key)
        channel := "monitor:" key_upper
        was_active := MonitorKeysState.Has(key_upper) && MonitorKeysState[key_upper]
        had_pending := PendingPythonStateEvents.Has(channel)

        if (MonitorKeysState.Has(key_upper)) {
            MonitorKeysState.Delete(key_upper)
        }
        if (StrUpper(ForceMoveKey) = key_upper) {
            ForceMoveActive := false
        }
        if (was_active || had_pending) {
            QueuePythonStateEvent(channel, "monitor_key_up:" key, false)
        }
    }

    ; 删除记录
    RegisteredHooks.Delete(key)
    return true
}

IsSupportedHookMode(mode) {
    return mode = "intercept" || mode = "priority" || mode = "special"
        || mode = "monitor" || mode = "block"
}

HookModeHasUpEdge(key, mode) {
    return (mode = "intercept" && !IsWheelKey(key)) || mode = "special" || mode = "monitor"
}

EnableHookDown(key, mode) {
    switch mode {
        case "intercept":
            Hotkey("$" key, (*) => HandleInterceptKey(key), "On")
        case "priority":
            Hotkey("$" key, (*) => HandleManagedKey(key), "On")
        case "special":
            Hotkey("~" key, (*) => HandleSpecialKeyDown(key), "On")
        case "monitor":
            Hotkey("~" key, (*) => HandleMonitorKey(key), "On")
        case "block":
            Hotkey("$" key, (*) => {}, "On")
        default:
            throw Error("Unsupported hook mode: " mode)
    }
}

EnableHookUp(key, mode) {
    switch mode {
        case "intercept":
            Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key), "On")
        case "special":
            Hotkey("~" key " up", (*) => HandleSpecialKeyUp(key), "On")
        case "monitor":
            Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key), "On")
        default:
            throw Error("Hook mode has no up edge: " mode)
    }
}

DisableHookDown(key, mode) {
    prefix := (mode = "special" || mode = "monitor") ? "~" : "$"
    Hotkey(prefix key, "Off")
}

DisableHookUp(key, mode) {
    prefix := (mode = "special" || mode = "monitor") ? "~" : "$"
    Hotkey(prefix key " up", "Off")
}

; ===============================================================================
; Hook处理器
; ===============================================================================
HandleInterceptKey(key) {
    ; 拦截模式 - 按键按下
    global InterceptKeysPressed, INTERCEPT_REPEAT_WINDOW_MS, RegisteredHooks
    global MainModeArmed, MainModeF8AwaitRelease, PhysicalStopLatched
    global RuntimeOwner

    ; 活跃 F8 必须在通用重复去重前判定，且本次判定随后不再重读 Hook 数量。
    ; STOPPED→READY 的启动 F8 若丢了 up，InterceptKeysPressed 会残留上一世代 down；
    ; 新世代第一条 stop 必须无条件覆盖这个 stale down，不能在 1.1s 窗口内被吞。
    keyUpper := StrUpper(key)
    isActiveF8Stop := keyUpper = "F8"
        && (PhysicalStopLatched || MainModeArmed || RegisteredHooks.Count > 0
            || RuntimeOwner != "none")
    if (isActiveF8Stop && MainModeF8AwaitRelease) {
        ; 这是触发 STOPPED→READY 的同一次物理长按产生的 auto-repeat。
        ; 只有永久 up 边沿或物理键态轮询确认释放后，新 down 才能成为 stop。
        InterceptKeysPressed[key] := MonotonicMs()
        return
    }
    if (isActiveF8Stop && !PhysicalStopLatched) {
        InterceptKeysPressed[key] := MonotonicMs()
        LatchPhysicalStop("intercept_key_down:" key)
        return
    }

    ; 键盘自动重复去重:up 之前的重复 down 只承认第一次(special 键在
    ; HandleSpecialKeyDown 有同型去重;intercept 此前没配 up 边沿,无法判断)。
    ; 窗口滚动刷新:按住期间每次重复推进时间戳;若 up 边沿丢失,1.1 秒后自愈。
    ; 滚轮键跳过:无 up 边沿也无自动重复,参与去重只会吞掉连续滚动(见 IsWheelKey)。
    if (!IsWheelKey(key)) {
        now := MonotonicMs()
        if (InterceptKeysPressed.Has(key)
            && now - InterceptKeysPressed[key] < INTERCEPT_REPEAT_WINDOW_MS) {
            InterceptKeysPressed[key] := now
            return
        }
        InterceptKeysPressed[key] := now
    }

    ; 洗练/寻路的 owner 协议让各自的第二次根热键先在 AHK 当地关闸清场，
    ; 然后才可靠回发停止意图。判定放在通用重复去重之后，避免启动时的同一次
    ; 长按因键盘 auto-repeat 立即又停掉新一轮。
    isOwnerStop := (keyUpper = "F7" && RuntimeOwner = "affix")
        || (keyUpper = "F9" && RuntimeOwner = "pathfinding")
    if (isOwnerStop) {
        LatchRuntimeOwnerStop("intercept_key_down:" key)
        return
    }

    ; 动态 Hook/明确 RuntimeOwner 表示某个输入模式正在武装或运行。此时物理 F8
    ; 必须先在 AHK 当地止血，不等 Python GUI 线程：即使事件通道连续超时，
    ; 本边也已关闸+清队+停宏+释放全部持键。STOPPED 的 F8 启动和独立
    ; F7 洗练都没有动态 Hook，不会被这条路径误关闸。
    if (isActiveF8Stop) {
        ; 置 latch、关闸、作废旧世代 FIFO、放入 stop 信封必须是同一原子片段。
        ; 中间若允许 F7/F9 或 CLEAR_HOOKS 插入，会误删 stop 后的新事件，或把本次
        ; F8 重新判成 STOPPED 的普通启动 toggle。
        LatchPhysicalStop("intercept_key_down:" key)
    } else {
        ; 所有拦截按键都完全拦截，只通知Python。只入可靠 FIFO；同步 WM_COPYDATA
        ; 由 timer 执行，GUI 停顿不能把当前 Hotkey 线程卡住或静默吞掉后续按键。
        QueuePythonReliableEvent("intercept_key_down:" key)
    }

    ; 🎯 F8不再在AHK端主动切换，由Python完成UI切换后主动通知AHK

    ; 不发送到目标应用程序（完全拦截）
}

HandleInterceptKeyUp(key) {
    ; 拦截模式 - 按键释放:只复位去重状态,不通知 Python(状态机只消费按下边沿)。
    ; up 同样被 $ 拦截:down 已被吞,孤儿 up 不该打进游戏。
    global InterceptKeysPressed, MainModeF8AwaitRelease

    if (InterceptKeysPressed.Has(key)) {
        InterceptKeysPressed.Delete(key)
    }
    if (StrUpper(key) = "F8" && MainModeF8AwaitRelease) {
        MainModeF8AwaitRelease := false
        SetTimer(PollMainModeF8Release, 0)
    }
}

; 🎯 特殊按键处理（如space）- 不拦截，持续状态检测
HandleSpecialKeyDown(key) {
    global SpecialKeysPressed, SpecialKeysPaused

    ; 键盘自动重复会再次触发 key-down Hotkey。物理键因 ~ 前缀仍正常透传,
    ; 这里只去重状态事件,避免每次重复都同步回发 WM_COPYDATA 占住 AHK 主线程。
    if (SpecialKeysPressed.Has(key)) {
        return
    }

    ; 若在松开保护窗口内重新按下,继续沿用同一段暂停并取消旧恢复 timer。
    SetTimer(FinishSpecialKeyPause, 0)

    ; 记录按键按下状态
    SpecialKeysPressed[key] := true

    ; 如果这是第一个特殊按键，暂停系统
    if (SpecialKeysPressed.Count = 1 && !SpecialKeysPaused) {
        SpecialKeysPaused := true
        SetMacroSpecialSuppressed(true)
        QueuePythonStateEvent("special_pause", "special_key_pause:start")
    }

    ; 通知Python特殊按键状态（边沿不可合并，走可靠 FIFO）。
    QueuePythonReliableEvent("special_key_down:" key)
}

HandleSpecialKeyUp(key) {
    global SpecialKeysPressed, SpecialKeysPaused, SpecialKeyResumeDelayMs

    ; 移除按键状态
    if (SpecialKeysPressed.Has(key)) {
        SpecialKeysPressed.Delete(key)
    }

    ; key-up 立即入可靠 FIFO；~Hook 已让物理 key-up 同样立即透传给游戏。
    QueuePythonReliableEvent("special_key_up:" key)

    ; 所有特殊按键都释放后,仅自动输入的恢复可以按配置延后。
    if (SpecialKeysPressed.Count = 0 && SpecialKeysPaused) {
        if (SpecialKeyResumeDelayMs > 0) {
            SetTimer(FinishSpecialKeyPause, -SpecialKeyResumeDelayMs)
        } else {
            FinishSpecialKeyPause()
        }
    }
}

FinishSpecialKeyPause() {
    global SpecialKeysPressed, SpecialKeysPaused

    ; 一次性 timer 到期前可能有另一个 special key 按下；此时绝不能解除新抑制。
    if (SpecialKeysPressed.Count > 0 || !SpecialKeysPaused) {
        return
    }

    SetTimer(FinishSpecialKeyPause, 0)
    SpecialKeysPaused := false
    SetMacroSpecialSuppressed(false)
    ; 抑制解除:补齐抑制期间被推迟的技能持键 down
    ReconcileSkillHoldKeys()
    QueuePythonStateEvent("special_pause", "special_key_pause:end")
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

    QueuePythonReliableEvent("managed_key_down:" key)

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
    ; 执行态必须在物理边沿当地立即更新。Python 可能卡顿，状态事件
    ; 也会 latest-wins 合并 down/up，不能让按键替换的正确性依赖往返。
    ReconcileForceMoveState()

    ; 状态事件保留最新值并失败重试,避免 down/up 任一丢失后两端永久分叉。
    QueuePythonStateEvent("monitor:" key_upper, "monitor_key_down:" key)
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
    ReconcileForceMoveState()

    QueuePythonStateEvent("monitor:" key_upper, "monitor_key_up:" key)
}

ReconcileForceMoveState() {
    ; AHK 是按键替换的执行端，因此物理 monitor 账本才是权威。
    ; 闸门关闭时始终 false；开闸/换键/物理边沿都调用本函数重算。
    global MonitorKeysState, ForceMoveKey, ForceMoveActive, RuntimeAcceptingActions

    key_upper := StrUpper(Trim(ForceMoveKey))
    ForceMoveActive := RuntimeAcceptingActions
        && key_upper != ""
        && MonitorKeysState.Has(key_upper)
        && MonitorKeysState[key_upper]
}

SetRuntimeOwner(param) {
    global RuntimeOwner, RuntimeOwnerEpoch, RuntimeOwnerEpochs, RuntimeAcceptingActions

    parts := StrSplit(param, ":", , 2)
    if (parts.Length != 2) {
        return false
    }
    owner := StrLower(Trim(parts[1]))
    epochText := Trim(parts[2])
    if ((owner != "none" && owner != "main" && owner != "affix"
        && owner != "pathfinding") || !IsInteger(epochText)) {
        return false
    }
    epoch := Integer(epochText)
    if (epoch < 0) {
        return false
    }

    previousCritical := A_IsCritical
    Critical "On"
    try {
        ; owner 交接与新世代建立都必须发生在关闸期。开闸中只接受
        ; 完全相同的幂等重发，不允许偷换运行所有权。
        if (RuntimeAcceptingActions
            && (owner != RuntimeOwner || epoch != RuntimeOwnerEpoch)) {
            return false
        }
        ; 每个 owner 的 epoch 跨 RESET 保留单调 tombstone。当前 owner 允许
        ; 完全相同的幂等重发；停止后再来的同世代则必须拒绝。
        if (owner != "none") {
            lastEpoch := RuntimeOwnerEpochs[owner]
            if ((owner = RuntimeOwner && epoch < lastEpoch)
                || (owner != RuntimeOwner && epoch <= lastEpoch)) {
                return false
            }
            RuntimeOwnerEpochs[owner] := epoch
        }
        RuntimeOwner := owner
        RuntimeOwnerEpoch := epoch
        return true
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

RuntimeOwnerMatches(owner, epochText) {
    global RuntimeOwner, RuntimeOwnerEpoch

    owner := StrLower(Trim(owner))
    epochText := Trim(epochText)
    return IsInteger(epochText)
        && Integer(epochText) >= 0
        && owner = RuntimeOwner
        && Integer(epochText) = RuntimeOwnerEpoch
}

SetRuntimeActionGateFromParam(param) {
    parts := StrSplit(param, ":", , 3)
    if (parts.Length = 1) {
        if (param != "true" && param != "false") {
            return false
        }
        return SetRuntimeActionGate(param = "true")
    }
    if (parts.Length != 3 || (parts[1] != "true" && parts[1] != "false")
        || !RuntimeOwnerMatches(parts[2], parts[3])) {
        return false
    }
    return SetRuntimeActionGate(parts[1] = "true")
}

ResetRuntime() {
    global RuntimeAcceptingActions, RuntimeOwner
    global IsPaused, StationaryModeActive, PhysicalStopLatched, RuntimeOwnerStopLatched
    global DirectTargetInputSuspended

    previousCritical := A_IsCritical
    Critical "On"
    try {
        ; 必须先写关闸，再触碰任何可能被当前 timer/hotkey 观察的状态。
        RuntimeAcceptingActions := false
        DirectTargetInputSuspended := false
        ReconcileForceMoveState()
        ClearQueue(-1)
        IsPaused := false
        StationaryModeActive := false

        ; ClearQueue(-1) 已完成关闸/持键释放，清 Hook 时不再重跑屏障。
        hooksCleared := ClearAllConfigurableHooks(true)
        if (hooksCleared) {
            RuntimeOwner := "none"
            RuntimeOwnerStopLatched := false
        } else {
            ; 部分 Hook 注销失败时保持 latch，后续开闸只能被拒绝。
            PhysicalStopLatched := true
        }
        return hooksCleared
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

ArmMainMode() {
    ; READY 的两阶段入口：先 armed，完成 Hook/捕获准备后才由 Python 真正开闸。
    ; 已有物理 stop latch 时拒绝重新 armed，入口会按正常失败路径回退 STOPPED。
    global InterceptKeysPressed, MainModeArmed, MainModeF8AwaitRelease
    global MAIN_MODE_F8_RELEASE_POLL_MS, PhysicalStopLatched

    previousCritical := A_IsCritical
    Critical "On"
    try {
        if (PhysicalStopLatched) {
            return false
        }
        ; 不信任上一模式的 Python→AHK false 已经执行。尤其洗练停机可能在
        ; SendMessageTimeoutW 超时后只恢复了 PING，旧 gate 仍为 true。
        ; 在同一 Critical 内先建立“关闸且清场”不变量，再暴露 armed 状态。
        if (!SetRuntimeActionGate(false)) {
            return false
        }
        MainModeArmed := true
        MainModeF8AwaitRelease := IsPhysicalKeyPressed("F8")
        if (MainModeF8AwaitRelease) {
            SetTimer(PollMainModeF8Release, MAIN_MODE_F8_RELEASE_POLL_MS)
        } else {
            ; up Hook 可能在安全桌面切换时丢失，但 arm 时键已经物理释放。
            ; 此时旧启动 down 只是 stale 账本，直接清掉，允许真正的第二次按下。
            SetTimer(PollMainModeF8Release, 0)
            if (InterceptKeysPressed.Has("F8")) {
                InterceptKeysPressed.Delete("F8")
            }
        }
        return true
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

PollMainModeF8Release() {
    global InterceptKeysPressed, MainModeF8AwaitRelease

    if (!MainModeF8AwaitRelease) {
        SetTimer(PollMainModeF8Release, 0)
        return
    }
    if (IsPhysicalKeyPressed("F8")) {
        return
    }

    ; 物理释放是真正的世代边界。轮询路径与 up Hook 做同一份幂等清理。
    MainModeF8AwaitRelease := false
    if (InterceptKeysPressed.Has("F8")) {
        InterceptKeysPressed.Delete("F8")
    }
    SetTimer(PollMainModeF8Release, 0)
}

IsPhysicalKeyPressed(key) {
    try {
        return GetKeyState(key, "P")
    } catch {
        ; GetKeyState 正常不会失败；异常时不让 F8 永久变成死键。
        return false
    }
}

LatchPhysicalStop(event) {
    ; latch 与关闸必须是同一个不可重入片段。若迟到的 Python 开闸消息夹在二者之间，
    ; 它可能短暂重按持键，甚至覆盖本次 stop 的最终状态。
    global PhysicalStopLatched

    previousCritical := A_IsCritical
    Critical "On"
    try {
        PhysicalStopLatched := true
        SetRuntimeActionGate(false)
        ; forceStopIntent 固化进入本 Critical 区时的状态判定。即使未来清理逻辑变化，
        ; 这次物理 F8 也只能是 stop-only，绝不能在 STOPPED 中反向启动。
        return QueuePythonReliableEvent(event, true, true)
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

LatchRuntimeOwnerStop(event) {
    global RuntimeOwnerStopLatched

    previousCritical := A_IsCritical
    Critical "On"
    try {
        RuntimeOwnerStopLatched := true
        SetRuntimeActionGate(false)
        return QueuePythonReliableEvent(event)
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

SetRuntimeActionGate(accepting) {
    ; 运行时输入闸门的唯一写入点。Python 命令、物理 F8 止血和 shutdown
    ; 共用同一条原子语义，避免新的持键/队列类型只在某条停机路径释放。
    global RuntimeAcceptingActions, PhysicalStopLatched, RuntimeOwnerStopLatched

    previousCritical := A_IsCritical
    Critical "On"
    try {
        if (accepting && (PhysicalStopLatched || RuntimeOwnerStopLatched)) {
            return false
        }

        RuntimeAcceptingActions := accepting ? true : false
        ; 无论开/关闸都立即从 AHK 物理账本重算：关闸必为 false，
        ; PAUSED 期间仍按住强制移动键则在开闸时当地恢复。
        ReconcileForceMoveState()
        if (!RuntimeAcceptingActions) {
            ClearQueue(-1)
        } else {
            ; 开闸与抑制解除同型:补按被推迟的持键(正常流程 desired 已空,是空转;
            ; Python 随后会重新声明完整集合)
            ReconcileSkillHoldKeys()
        }
        return true
    } finally {
        if (previousCritical) {
            Critical previousCritical
        } else {
            Critical "Off"
        }
    }
}

; ===============================================================================
; 事件发送到Python
; ===============================================================================
QueuePythonStateEvent(channel, event, tryNow := true) {
    global PendingPythonStateEvents

    ; 同一状态通道只保留最新值:down 尚未补发时若已经 up,补发 up 才是当前真相。
    PendingPythonStateEvents[channel] := event
    if (tryNow) {
        ; 当前可能是 Hotkey 或 WM_COPYDATA 线程；仅安排一次性 timer，不内联等待。
        SchedulePythonEventFlush()
    }
}

QueuePythonReliableEvent(event, tryNow := true, forceStopIntent := false) {
    global PythonEventSession, PythonReliableEventSeq
    global PendingPythonReliableEvents, MAX_PENDING_PYTHON_RELIABLE_EVENTS
    global PYTHON_RELIABLE_F8_RESERVE, RegisteredHooks, F8StopIntentPending
    global PythonReliableRetryUntil, PythonReliableRetryDelayMs
    global PYTHON_RELIABLE_RETRY_BASE_MS

    isF8Event := IsF8StopEvent(event)
    isActiveStopIntent := isF8Event && (forceStopIntent || RegisteredHooks.Count > 0)
    if (isActiveStopIntent && F8StopIntentPending) {
        ; 第一条停机意图已入队或已交给 Qt，继续按 F8 不应在恢复后
        ; 排成 STOP→START→STOP。返回 true 表示该安全意图已被承认。
        return true
    }

    PythonReliableEventSeq += 1
    ; 显式标记 stop-only：若 GUI 在事件排队期间已经停机，Python 必须
    ; 忽略这条迟到意图，不能把它当成 STOPPED→READY 的新启动。
    wireEvent := isActiveStopIntent ? "intercept_key_down:f8_stop" : event
    envelope := Format("evt:{}:{}:{}", PythonEventSession, PythonReliableEventSeq, wireEvent)

    if (isActiveStopIntent) {
        ; 物理 stop 是旧运行世代的终点。尚未交给 Python 的 F7/F9/Z/管理键等旧边沿
        ; 全部失效；否则把 stop 插到它们前面后，这些旧根热键会在 STOPPED 中反向生效。
        ; 本次调用返回后产生的新人工事件仍正常追加在 stop 后面。
        PendingPythonReliableEvents := []
        PendingPythonReliableEvents.Push(envelope)
        ; 旧队首失败形成的退避也属于旧世代，不能让安全 stop 再等最多 1 秒。
        PythonReliableRetryUntil := 0
        PythonReliableRetryDelayMs := PYTHON_RELIABLE_RETRY_BASE_MS
        F8StopIntentPending := true
    } else {
        ; 业务边沿最多占 max-reserve，确保 Python 长暂停/滚轮洪泛后 F8 仍能入队。
        ; 普通 F8 绝对满时优先驱逐一个非 F8；若 64 项全是旧 F8，则驱逐最旧
        ; toggle 保留最新意图，不能让根热键永久拒绝新输入。
        softLimit := MAX_PENDING_PYTHON_RELIABLE_EVENTS - PYTHON_RELIABLE_F8_RESERVE
        if (!isF8Event && PendingPythonReliableEvents.Length >= softLimit) {
            OutputDebug("[pyahk] reliable Python event FIFO full; rejected seq=" PythonReliableEventSeq)
            return false
        }
        if (isF8Event
            && PendingPythonReliableEvents.Length >= MAX_PENDING_PYTHON_RELIABLE_EVENTS) {
            evicted := false
            loop PendingPythonReliableEvents.Length {
                index := PendingPythonReliableEvents.Length - A_Index + 1
                if (!IsF8Envelope(PendingPythonReliableEvents[index])) {
                    PendingPythonReliableEvents.RemoveAt(index)
                    evicted := true
                    break
                }
            }
            if (!evicted) {
                PendingPythonReliableEvents.RemoveAt(1)
            }
        }
        PendingPythonReliableEvents.Push(envelope)
    }

    if (tryNow) {
        SchedulePythonEventFlush()
    }
    return true
}

IsF8StopEvent(event) {
    return CachedStrLower(event) = "intercept_key_down:f8"
}

IsF8Envelope(envelope) {
    return RegExMatch(envelope, "i):intercept_key_down:F8(?:_stop)?$")
}

IsF8StopEnvelope(envelope) {
    return RegExMatch(envelope, "i):intercept_key_down:F8_stop$")
}

SchedulePythonEventFlush() {
    ; FlushPendingPythonEvents 自身还有一个 100ms 周期 timer 负责失败重试。
    ; AHK v2 对同一 callback 调 SetTimer(..., -1) 会把原周期 timer 改成一次性，
    ; 因此“尽快发送”必须使用独立 callback，不能覆盖周期重试器。
    SetTimer(FlushPendingPythonEventsSoon, -1)
}

FlushPendingPythonEventsSoon() {
    FlushPendingPythonEvents()
}

FlushPendingPythonEvents() {
    global PendingPythonReliableEvents

    ; 人工边沿优先，且每次最多真实发送一条，避免 GUI 持续卡顿时一次 timer
    ; 串行吃满多个 50ms。成功后用一次性 timer 继续推进队列。
    if (PendingPythonReliableEvents.Length > 0) {
        FlushPendingPythonReliableEvents()
        return
    }
    FlushPendingPythonStateEvents()
}

FlushPendingPythonReliableEvents() {
    global PendingPythonReliableEvents
    global F8StopIntentPending
    global PythonReliableRetryUntil, PythonReliableRetryDelayMs
    global PYTHON_RELIABLE_RETRY_BASE_MS, PYTHON_RELIABLE_RETRY_MAX_MS

    if (PendingPythonReliableEvents.Length = 0) {
        return
    }

    now := MonotonicMs()
    if (now < PythonReliableRetryUntil) {
        return
    }

    envelope := PendingPythonReliableEvents[1]
    ; 独立于观测/状态退避:人工事件必须真实尝试；失败也不污染共享状态退避。
    if (SendEventToPython(envelope, true, false)) {
        removed := false
        if (PendingPythonReliableEvents.Length > 0
            && PendingPythonReliableEvents[1] = envelope) {
            PendingPythonReliableEvents.RemoveAt(1)
            removed := true
        }
        ; pending 只描述“stop-only 信封仍在本地 FIFO”。交给 Qt 后即结束合并；
        ; 后续 F8 可再排一条幂等 stop，不再依赖 CLEAR_HOOKS 成功与否永久解锁。
        if (removed && IsF8StopEnvelope(envelope)) {
            F8StopIntentPending := false
        }
        PythonReliableRetryUntil := 0
        PythonReliableRetryDelayMs := PYTHON_RELIABLE_RETRY_BASE_MS
        if (PendingPythonReliableEvents.Length > 0) {
            SchedulePythonEventFlush()
        }
        return
    }

    ; 发送期间物理 F8 可能已用 stop-only 信封替换整个旧世代 FIFO。旧队首的失败
    ; 不能在返回后重新武装退避，把刚刚重置为“立即尝试”的安全 stop 再压住。
    if (PendingPythonReliableEvents.Length = 0
        || PendingPythonReliableEvents[1] != envelope) {
        PythonReliableRetryUntil := 0
        PythonReliableRetryDelayMs := PYTHON_RELIABLE_RETRY_BASE_MS
        if (PendingPythonReliableEvents.Length > 0) {
            SchedulePythonEventFlush()
        }
        return
    }

    PythonReliableRetryUntil := now + PythonReliableRetryDelayMs
    PythonReliableRetryDelayMs := Min(
        PythonReliableRetryDelayMs * 2,
        PYTHON_RELIABLE_RETRY_MAX_MS
    )
}

FlushPendingPythonStateEvents() {
    global PendingPythonStateEvents

    ; 每 tick 最多尝试一个状态事件。失败会开启退避,不应在同一轮继续累计超时。
    for channel, event in PendingPythonStateEvents {
        if (SendEventToPython(event)) {
            ; 发送期间若发生状态更新,不能删除后来写入的新值。
            if (PendingPythonStateEvents.Has(channel)
                && PendingPythonStateEvents[channel] = event) {
                PendingPythonStateEvents.Delete(channel)
            }
        }
        return
    }
}

SendEventToPython(event, bypassBackoff := false, armBackoff := true) {
    global CurrentPythonWindow, CachedPythonHwnd
    global PythonSendBackoffUntil, PYTHON_SEND_BACKOFF_MS

    now := MonotonicMs()
    if (!bypassBackoff && now < PythonSendBackoffUntil) {
        return false
    }

    ; 🎯 使用缓存的窗口句柄
    if (CachedPythonHwnd != 0) {
        ; 直接使用缓存的句柄
        if (SendWMCopyDataToPython(CachedPythonHwnd, event)) {
            PythonSendBackoffUntil := 0
            return true
        }
        ; 失败后清缓存并退避。不要在同一事件内对同一窗口再等第二次。
        CachedPythonHwnd := 0
        if (armBackoff) {
            PythonSendBackoffUntil := now + PYTHON_SEND_BACKOFF_MS
        }
        return false
    }

    ; 🎯 缓存失效或首次调用：根据F8状态查找正确的窗口
    CachedPythonHwnd := WinExist(CurrentPythonWindow)

    ; 最后尝试发送
    if (CachedPythonHwnd) {
        ; 🎯 如果最后一次发送也失败，清除缓存
        if (!SendWMCopyDataToPython(CachedPythonHwnd, event)) {
            CachedPythonHwnd := 0
            if (armBackoff) {
                PythonSendBackoffUntil := now + PYTHON_SEND_BACKOFF_MS
            }
            return false
        }
        PythonSendBackoffUntil := 0
        return true
    }
    if (armBackoff) {
        PythonSendBackoffUntil := now + PYTHON_SEND_BACKOFF_MS
    }
    return false
}

; 发送WM_COPYDATA消息到Python的辅助函数（简单高效版本）
SendWMCopyDataToPython(hwnd, eventData) {
    global PYTHON_SEND_TIMEOUT_MS, SMTO_ABORTIFHUNG, SMTO_ERRORONEXIT

    try {
        ; 准备UTF-8编码的数据
        eventBytes := Buffer(StrLen(eventData) * 3 + 1)  ; UTF-8最多3字节/字符
        dataSize := StrPut(eventData, eventBytes, "UTF-8") - 1  ; 不包含null终止符

        ; 创建COPYDATASTRUCT
        cds := Buffer(A_PtrSize * 3)
        NumPut("Ptr", 9999, cds, 0)                        ; dwData = 9999 (事件标识)
        NumPut("UInt", dataSize, cds, A_PtrSize)           ; cbData = 数据长度
        NumPut("Ptr", eventBytes.Ptr, cds, A_PtrSize * 2)  ; lpData = 数据指针

        ; 同步发送但设短超时上限。返回值表示消息是否成功送达;
        ; receiverResult 是 Python 窗口过程的返回值(当前可能为 0),不用于判断传输成功。
        receiverResult := Buffer(A_PtrSize, 0)
        sent := DllCall("user32.dll\SendMessageTimeoutW",
            "Ptr", hwnd,      ; 目标窗口句柄
            "UInt", 0x004A,   ; WM_COPYDATA
            "Ptr", 0,         ; wParam
            "Ptr", cds.Ptr,   ; lParam
            "UInt", SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT,
            "UInt", PYTHON_SEND_TIMEOUT_MS,
            "Ptr", receiverResult.Ptr,
            "Ptr")

        return (sent != 0)

    } catch as err {
        ; 发送失败，返回失败
        return false
    }
}

; 每秒推送一次队列观测(SetTimer 见 ProcessQueue 定时器旁)。
; e/h/n/l 是**实时**队列深度(QueueCounts,不是累计入队数 —— 累计数只涨不落,
; 看不出"现在积压多少");p/d/x 是累计 处理/过载丢弃/等待过期。
; Python 端(main_window)把它压成 OSD 的一行,RUNNING/PAUSED 时展示。
SendStatsToPython() {
    global QueueCounts, QueueStats, PendingPythonReliableEvents, PendingPythonStateEvents
    global PythonStatsRetryAt, PYTHON_STATS_RETRY_MS

    now := MonotonicMs()
    ; 观测永远给控制/状态事件让路；失败后静默 2 秒，避免 GUI 卡顿期间每秒固定
    ; 占住 AHK 主线程 50ms。
    if (PendingPythonReliableEvents.Length > 0 || PendingPythonStateEvents.Count > 0
        || now < PythonStatsRetryAt) {
        return
    }

    stats := Format("stats:e={},h={},n={},l={},p={},d={},x={}",
        QueueCounts["emergency"],
        QueueCounts["high"],
        QueueCounts["normal"],
        QueueCounts["low"],
        QueueStats["processed"],
        QueueStats["dropped"],
        QueueStats["expired"]
    )
    ; stats 是纯观测:失败直接丢弃,不得武装共享退避门封锁随后的人手热键。
    if (SendEventToPython(stats, false, false)) {
        PythonStatsRetryAt := 0
    } else {
        PythonStatsRetryAt := now + PYTHON_STATS_RETRY_MS
    }
}

ActivateTargetWindow() {
    global TargetWin

    if (TargetWin != "" && WinExist(TargetWin)) {
        WinActivate(TargetWin)
    }
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
ClearAllConfigurableHooks(barrierAlreadyApplied := false) {
    ; 简化版本：清空所有记录的 Hook
    ; F8/F7/F9 永久根热键不在 RegisteredHooks 中,自动被保留(见 RegisterHook 的 key_upper 检查)
    global ActiveManagedKeys, SpecialKeysPressed, SpecialKeysPaused, ManagedKeysConfig
    global MonitorKeysState, ForceMoveActive, PendingPythonStateEvents
    global F8StopIntentPending, MainModeArmed, MainModeF8AwaitRelease
    global PhysicalStopLatched

    ; 收集所有要删除的键
    keysToRemove := []
    for key, mode in RegisteredHooks {
        keysToRemove.Push(key)
    }

    allRemoved := true
    ; 删除所有键(UnregisterHook 已经为每个 special 键单独清理了 SpecialKeysPressed/Paused)
    for index, key in keysToRemove {
        if (!UnregisterHook(key)) {
            allRemoved := false
        }
    }

    ; 配置切换:所有 managed_keys 即将注销,残留 single-flight 锁/旧映射无意义
    ActiveManagedKeys := Map()
    ManagedKeysConfig := Map()
    SetMacroManagedSuppressed(false)

    ; 兜底:即使 per-key 注销有遗漏,也确保 special 状态彻底归零
    SpecialKeysPressed := Map()
    if (SpecialKeysPaused) {
        SetTimer(FinishSpecialKeyPause, 0)
        SpecialKeysPaused := false
        SetMacroSpecialSuppressed(false)
    }

    ; CLEAR_HOOKS 只用于进入 STOPPED。所有动态状态在两端本地归零,旧 down 不得在
    ; 下一轮 timer 中迟到；逐键注销生成的 monitor up/special end 也无需再回发。
    MonitorKeysState := Map()
    ForceMoveActive := false
    PendingPythonStateEvents := Map()

    ; 动态 Hook 已确认清完才解除物理 stop latch。先在 Critical 区内再次确认关闸
    ; 和清场，最后才解锁；同一命令通道中排在 CLEAR_HOOKS 前的旧 true 无法复活。
    if (allRemoved && RegisteredHooks.Count = 0) {
        previousCritical := A_IsCritical
        Critical "On"
        try {
            if (!barrierAlreadyApplied) {
                SetRuntimeActionGate(false)
            }
            ; 抑制状态已彻底归零：在 latch 仍为 true、闸门仍关闭时完成最终对齐。
            ; STOPPED 路径下 desired 已空，这是幂等空转；不能放到解锁之后。
            ReconcileSkillHoldKeys()
            F8StopIntentPending := false
            MainModeArmed := false
            MainModeF8AwaitRelease := false
            SetTimer(PollMainModeF8Release, 0)
            PhysicalStopLatched := false
        } finally {
            if (previousCritical) {
                Critical previousCritical
            } else {
                Critical "Off"
            }
        }
    } else if (MainModeArmed) {
        ; 主模式 Hook 只清掉一部分时保持 fail-closed。后续物理 F8 由 armed/latch
        ; 继续编码为 stop-only，Python 在 STOPPED 重跑本幂等清理。
        PhysicalStopLatched := true
        SetRuntimeActionGate(false)
    }

    ; 可靠 FIFO 刻意保留:它可能包含紧随 F8 的另一个真实人工边沿。当前 F8 若已被
    ; Python 处理,本次 SendMessage 返回后正常出队；若发送方超时则按 seq 重试并由
    ; Python 去重。清空它反而会把 F7/F9 等后续用户意图静默吞掉。

    ; ⚠️ 刻意**不**整体清 InterceptKeysPressed:此刻用户可能正按着 F8(本次 STOPPED
    ; 的来源),清掉它的按下状态会让键盘自动重复立刻再发一条 intercept_key_down
    ; (把刚停下的状态又切回去)。可配置 intercept 键(Z/原地键/BOSS 键)已由上面的
    ; 逐键 UnregisterHook 清理;F8/F7/F9 永久注册,其 up 边沿始终存在,无残留风险。

    ; 失败路径仍做安全方向的本地对齐；PhysicalStopLatched 保持 true，不能补按新键。
    if (!allRemoved || RegisteredHooks.Count > 0) {
        ReconcileSkillHoldKeys()
    }
    return allRemoved
}

; ===============================================================================
; 保持运行
; ===============================================================================
; 脚本会一直运行，直到手动关闭
