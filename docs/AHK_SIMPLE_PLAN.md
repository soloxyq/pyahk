# AHK输入系统 - 简洁实施方案

## 🎯 核心原则

**简单、直接、纯粹**

- ✅ 全面使用AHK，不搞降级
- ✅ 遇到问题就解决，不绕路
- ✅ 保持代码简洁，不增加复杂度
- ✅ Python负责决策，AHK负责执行

---

## 📋 实施步骤

### 第一步：扩展AHK Server（核心）

创建 `hold_server_extended.ahk`，包含：

1. **四级优先队列**
2. **队列处理器**（10ms定时器）
3. **Hook管理**（动态注册/取消）
4. **按键序列支持**
5. **事件发送**（UDP到Python）
6. **命令接收**（WM_COPYDATA）

### 第二步：Python侧简化

1. **移除旧代码**：
   - 删除 `CtypesHotkeyManager`
   - 删除 `MultiPriorityQueue`
   - 删除 Python侧的Hook逻辑
   - 删除 Python侧的按键队列

2. **创建新组件**：
   - `AHKCommandSender` - 发送命令到AHK
   - `AHKEventReceiver` - 接收AHK事件
   - `AHKInputHandler` - 统一接口

3. **更新引用**：
   - 所有使用 `InputHandler` 的地方改为 `AHKInputHandler`
   - 所有使用 `HotkeyManager` 的地方改为 AHK命令

### 第三步：测试验证

1. **功能测试**：Hook、队列、序列
2. **性能测试**：延迟、吞吐量
3. **稳定性测试**：长时间运行

---

## 🔧 核心代码结构

### AHK Server（hold_server_extended.ahk）

```autohotkey
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ============================================================================
; 全局状态
; ============================================================================
global EmergencyQueue := []
global HighQueue := []
global NormalQueue := []
global LowQueue := []
global IsPaused := false
global PriorityKeysActive := Map()
global RegisteredHooks := Map()

; ============================================================================
; GUI窗口（接收WM_COPYDATA）
; ============================================================================
WinTitle := "HoldServer_Window_UniqueName_12345"
gui1 := Gui()
gui1.Title := WinTitle
gui1.Hide()
OnMessage(0x4A, WM_COPYDATA)

; ============================================================================
; 队列处理器（10ms定时器）
; ============================================================================
ProcessQueue() {
    ; 紧急队列永远执行
    if (EmergencyQueue.Length > 0) {
        ExecuteAction(EmergencyQueue.RemoveAt(1))
        return
    }
    
    ; 暂停时不处理其他队列
    if (IsPaused) {
        return
    }
    
    ; 按优先级处理
    if (HighQueue.Length > 0) {
        ExecuteAction(HighQueue.RemoveAt(1))
    } else if (NormalQueue.Length > 0) {
        ExecuteAction(NormalQueue.RemoveAt(1))
    } else if (LowQueue.Length > 0) {
        ExecuteAction(LowQueue.RemoveAt(1))
    }
}

SetTimer(ProcessQueue, 10)

; ============================================================================
; 命令接收（WM_COPYDATA）
; ============================================================================
WM_COPYDATA(wParam, lParam, msg, hwnd) {
    str := StrGet(NumGet(lParam + A_PtrSize*2, "UPtr"), "UTF-8")
    if !str {
        return 0
    }

    parts := StrSplit(str, ":", 3)
    cmd := parts[1]
    
    switch cmd {
        case "enqueue":
            ; enqueue:priority:action
            EnqueueAction(Integer(parts[2]), parts[3])
            return 1
        case "pause":
            IsPaused := true
            return 1
        case "resume":
            IsPaused := false
            return 1
        case "hook_register":
            RegisterHook(parts[2], parts[3])
            return 1
        case "hook_unregister":
            UnregisterHook(parts[2])
            return 1
    }
    return 0
}

; ============================================================================
; 队列操作
; ============================================================================
EnqueueAction(priority, action) {
    switch priority {
        case 0: EmergencyQueue.Push(action)
        case 1: HighQueue.Push(action)
        case 2: NormalQueue.Push(action)
        case 3: LowQueue.Push(action)
    }
}

ExecuteAction(action) {
    parts := StrSplit(action, ":", 2)
    actionType := parts[1]
    actionData := parts[2]
    
    switch actionType {
        case "press":
            Send "{" actionData "}"
        case "sequence":
            ExecuteSequence(actionData)
        case "hold":
            Send "{" actionData " down}"
        case "release":
            Send "{" actionData " up}"
    }
}

ExecuteSequence(sequence) {
    parts := StrSplit(sequence, ",")
    for index, part in parts {
        if (InStr(part, "delay")) {
            Sleep SubStr(part, 6)
        } else {
            Send "{" part "}"
        }
    }
}

; ============================================================================
; Hook管理
; ============================================================================
RegisterHook(key, mode) {
    if (RegisteredHooks.Has(key)) {
        return
    }
    RegisteredHooks[key] := mode
    
    if (mode = "intercept") {
        Hotkey("$" key, (*) => HandleInterceptKey(key))
        Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key))
    }
}

UnregisterHook(key) {
    if (!RegisteredHooks.Has(key)) {
        return
    }
    Hotkey("$" key, "Off")
    Hotkey("$" key " up", "Off")
    RegisteredHooks.Delete(key)
}

HandleInterceptKey(key) {
    ; 通知Python
    SendEventToPython("priority_key_down:" key)
    
    ; 暂停队列
    IsPaused := true
    PriorityKeysActive[key] := true
    
    ; 延迟后发送
    Sleep 50
    Send "{" key "}"
}

HandleInterceptKeyUp(key) {
    SendEventToPython("priority_key_up:" key)
    PriorityKeysActive.Delete(key)
    
    if (PriorityKeysActive.Count = 0) {
        IsPaused := false
    }
    
    Send "{" key " up}"
}

; ============================================================================
; 事件发送（UDP到Python）
; ============================================================================
SendEventToPython(event) {
    ; 简单实现：写入文件
    ; Python通过文件监控接收事件
    try {
        FileAppend(event "`n", "ahk_events.txt")
    }
}
```

### Python侧（简化版）

```python
# ahk_command_sender.py
class AHKCommandSender:
    def __init__(self):
        self.window = "HoldServer_Window_UniqueName_12345"
    
    def enqueue(self, action: str, priority: int = 2):
        send_ahk_cmd(self.window, f"enqueue:{priority}:{action}")
    
    def send_key(self, key: str, priority: int = 2):
        self.enqueue(f"press:{key}", priority)
    
    def send_sequence(self, sequence: str, priority: int = 2):
        self.enqueue(f"sequence:{sequence}", priority)
    
    def register_hook(self, key: str, mode: str = "intercept"):
        send_ahk_cmd(self.window, f"hook_register:{key}:{mode}")

# ahk_event_receiver.py
class AHKEventReceiver:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.running = True
        threading.Thread(target=self._watch_events, daemon=True).start()
    
    def _watch_events(self):
        # 监控ahk_events.txt文件
        while self.running:
            if os.path.exists("ahk_events.txt"):
                with open("ahk_events.txt", "r") as f:
                    events = f.readlines()
                if events:
                    os.remove("ahk_events.txt")
                    for event in events:
                        self._handle_event(event.strip())
            time.sleep(0.01)
    
    def _handle_event(self, event: str):
        if event.startswith("priority_key_down:"):
            self.event_bus.publish("scheduler_pause_requested")
        elif event.startswith("priority_key_up:"):
            self.event_bus.publish("scheduler_resume_requested")

# ahk_input_handler.py
class AHKInputHandler:
    def __init__(self, event_bus=None):
        self.sender = AHKCommandSender()
        self.receiver = AHKEventReceiver(event_bus)
        self._init_hooks()
    
    def _init_hooks(self):
        # 注册优先级按键
        for key in ["space", "right_mouse", "e"]:
            self.sender.register_hook(key, "intercept")
    
    def send_key(self, key: str):
        self.sender.send_key(key, priority=2)
    
    def execute_hp_potion(self, key: str):
        self.sender.send_key(key, priority=0)  # 紧急
    
    def execute_skill_normal(self, key: str):
        self.sender.send_key(key, priority=2)  # 普通
```

---

## 🎯 迁移清单

### 删除的文件/代码
- [ ] `torchlight_assistant/utils/hotkey_manager.py` - 删除
- [ ] `torchlight_assistant/utils/multi_priority_queue.py` - 删除
- [ ] `torchlight_assistant/core/input_handler.py` 中的队列逻辑 - 删除
- [ ] 所有 `CtypesHotkeyManager` 的引用 - 删除

### 新增的文件
- [ ] `hold_server_extended.ahk` - AHK服务器
- [ ] `torchlight_assistant/core/ahk_command_sender.py` - 命令发送
- [ ] `torchlight_assistant/core/ahk_event_receiver.py` - 事件接收
- [ ] `torchlight_assistant/core/ahk_input_handler.py` - 统一接口

### 修改的文件
- [ ] `main.py` - 使用 `AHKInputHandler`
- [ ] `torchlight_assistant/core/macro_engine.py` - 移除Hook管理
- [ ] 所有使用 `InputHandler` 的模块 - 改为 `AHKInputHandler`

---

## 🚀 实施顺序

1. **Day 1-2**: 完成 `hold_server_extended.ahk`
2. **Day 3**: 完成Python侧三个新文件
3. **Day 4**: 删除旧代码，更新引用
4. **Day 5**: 测试和修复问题
5. **Day 6**: 文档更新

**总工期：6天**

---

## 💡 关键决策

1. **事件通信方式**：使用文件（简单可靠）
   - AHK写入 `ahk_events.txt`
   - Python监控文件变化
   - 延迟<10ms，完全够用

2. **不做降级**：AHK不可用就报错
   - 启动时检查AHK
   - 不可用就退出
   - 提示用户安装/启动AHK

3. **不做兼容**：直接替换旧代码
   - 不保留旧的InputHandler
   - 不保留旧的HotkeyManager
   - 彻底迁移

---

**创建时间**：2025-10-15
**分支**：feature/ahk-input-system
**原则**：简单、直接、纯粹
