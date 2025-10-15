# AHK完整输入系统 - 实施计划

## 📅 总体时间表

- **总工期**：9-13天
- **当前分支**：`feature/ahk-input-system`
- **目标**：完整迁移到AHK输入系统

---

## 🎯 阶段1：扩展AHK Server（3-4天）

### Day 1-2：核心队列系统

#### 任务1.1：四级优先队列
```autohotkey
; 文件: hold_server_extended.ahk

; 队列数据结构
global EmergencyQueue := []
global HighQueue := []
global NormalQueue := []
global LowQueue := []

; 入队函数
EnqueueAction(priority, action) {
    switch priority {
        case 0: EmergencyQueue.Push(action)
        case 1: HighQueue.Push(action)
        case 2: NormalQueue.Push(action)
        case 3: LowQueue.Push(action)
    }
}
```

**验收标准**：
- [ ] 四个队列正常工作
- [ ] 入队/出队功能正常
- [ ] 队列长度统计正确

#### 任务1.2：队列处理器
```autohotkey
; 定时器驱动的队列处理
ProcessQueue() {
    if (IsPaused && !IsProcessingEmergency) {
        return  ; 暂停时只处理紧急队列
    }
    
    ; 按优先级处理
    if (EmergencyQueue.Length > 0) {
        ExecuteAction(EmergencyQueue.RemoveAt(1))
    } else if (!IsPaused) {
        if (HighQueue.Length > 0) {
            ExecuteAction(HighQueue.RemoveAt(1))
        } else if (NormalQueue.Length > 0) {
            ExecuteAction(NormalQueue.RemoveAt(1))
        } else if (LowQueue.Length > 0) {
            ExecuteAction(LowQueue.RemoveAt(1))
        }
    }
}

SetTimer(ProcessQueue, 10)  ; 每10ms处理一次
```

**验收标准**：
- [ ] 定时器正常工作
- [ ] 优先级顺序正确
- [ ] 暂停时只处理紧急队列

#### 任务1.3：动作执行器
```autohotkey
ExecuteAction(action) {
    parts := StrSplit(action, ":", 2)
    if (parts.Length < 2) {
        return
    }
    
    actionType := parts[1]
    actionData := parts[2]
    
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
            Click actionData
    }
}
```

**验收标准**：
- [ ] 各种动作类型都能正确执行
- [ ] 错误处理正常

### Day 2-3：Hook管理系统

#### 任务1.4：动态Hook注册
```autohotkey
; Hook管理
global RegisteredHooks := Map()

RegisterHook(key, mode) {
    ; mode: "monitor", "intercept", "block"
    if (RegisteredHooks.Has(key)) {
        return  ; 已注册
    }
    
    RegisteredHooks[key] := mode
    
    ; 动态创建Hotkey
    if (mode = "intercept") {
        Hotkey("$" key, (*) => HandleInterceptKey(key))
        Hotkey("$" key " up", (*) => HandleInterceptKeyUp(key))
    } else if (mode = "monitor") {
        Hotkey("~" key, (*) => HandleMonitorKey(key))
        Hotkey("~" key " up", (*) => HandleMonitorKeyUp(key))
    } else if (mode = "block") {
        Hotkey("$" key, (*) => {})  ; 阻止
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
```

**验收标准**：
- [ ] 可以动态注册/取消Hook
- [ ] 三种模式都正常工作
- [ ] 无自拦截问题

#### 任务1.5：优先级按键处理
```autohotkey
HandleInterceptKey(key) {
    ; 1. 通知Python暂停
    SendEventToPython("priority_key_down:" key)
    
    ; 2. 设置暂停标志
    IsPaused := true
    PriorityKeysActive[key] := true
    
    ; 3. 延迟后发送到游戏
    Sleep 50  ; 前置延迟
    Send "{" key "}"
}

HandleInterceptKeyUp(key) {
    ; 1. 通知Python
    SendEventToPython("priority_key_up:" key)
    
    ; 2. 清除标志
    PriorityKeysActive.Delete(key)
    
    ; 3. 如果没有其他优先级按键，恢复
    if (PriorityKeysActive.Count = 0) {
        IsPaused := false
    }
    
    ; 4. 发送释放到游戏
    Send "{" key " up}"
}
```

**验收标准**：
- [ ] 优先级按键能正确拦截
- [ ] 暂停/恢复逻辑正确
- [ ] 前置延迟生效

### Day 3-4：通信系统

#### 任务1.6：TCP服务器（AHK → Python）
```autohotkey
; 使用AHK的Socket库或简单的文件通信
SendEventToPython(event) {
    ; 方案1: TCP Socket
    ; 方案2: 命名管道
    ; 方案3: 文件通信（简单但延迟高）
    
    ; 这里使用简单的UDP发送
    try {
        ; 发送UDP数据包到Python
        ; Python监听localhost:9999
    }
}
```

**验收标准**：
- [ ] 能发送事件到Python
- [ ] 延迟<5ms
- [ ] 可靠性>99%

#### 任务1.7：WM_COPYDATA接收器增强
```autohotkey
WM_COPYDATA(wParam, lParam, msg, hwnd) {
    str := StrGet(NumGet(lParam + A_PtrSize*2, "UPtr"), "UTF-8")
    if !str {
        return 0
    }

    parts := StrSplit(str, ":", 3)
    if parts.Length < 2 {
        return 0
    }
    
    cmd := parts[1]
    
    switch cmd {
        case "enqueue":
            ; enqueue:priority:action
            priority := Integer(parts[2])
            action := parts[3]
            EnqueueAction(priority, action)
            return 1
            
        case "press":
            SendPress(parts[2])
            return 1
            
        case "sequence":
            ExecuteSequence(parts[2])
            return 1
            
        case "pause":
            IsPaused := true
            return 1
            
        case "resume":
            IsPaused := false
            return 1
            
        case "hook_register":
            ; hook_register:key:mode
            RegisterHook(parts[2], parts[3])
            return 1
            
        case "hook_unregister":
            UnregisterHook(parts[2])
            return 1
            
        case "clear_queue":
            priority := Integer(parts[2])
            ClearQueue(priority)
            return 1
    }
    
    return 0
}
```

**验收标准**：
- [ ] 所有命令都能正确处理
- [ ] 错误处理完善
- [ ] 返回值正确

---

## 🎯 阶段2：Python侧改造（3-4天）

### Day 5-6：命令发送和事件接收

#### 任务2.1：AHKCommandSender
```python
# 文件: torchlight_assistant/core/ahk_command_sender.py

class AHKCommandSender:
    """AHK命令发送器"""
    
    def __init__(self, window_title: str):
        self.window_title = window_title
        self.command_cache = []
        self.batch_mode = False
    
    def enqueue(self, action: str, priority: int = 2):
        """将动作加入AHK队列"""
        cmd = f"enqueue:{priority}:{action}"
        return send_ahk_cmd(self.window_title, cmd)
    
    def send_key(self, key: str, priority: int = 2):
        """发送按键"""
        return self.enqueue(f"press:{key}", priority)
    
    def send_sequence(self, sequence: str, priority: int = 2):
        """发送按键序列"""
        return self.enqueue(f"sequence:{sequence}", priority)
    
    def pause(self):
        """暂停队列处理"""
        return send_ahk_cmd(self.window_title, "pause")
    
    def resume(self):
        """恢复队列处理"""
        return send_ahk_cmd(self.window_title, "resume")
    
    def register_hook(self, key: str, mode: str = "monitor"):
        """注册Hook"""
        return send_ahk_cmd(self.window_title, f"hook_register:{key}:{mode}")
    
    def unregister_hook(self, key: str):
        """取消Hook"""
        return send_ahk_cmd(self.window_title, f"hook_unregister:{key}")
```

**验收标准**：
- [ ] 所有方法都能正常工作
- [ ] 错误处理完善
- [ ] 单元测试通过

#### 任务2.2：AHKEventReceiver
```python
# 文件: torchlight_assistant/core/ahk_event_receiver.py

import socket
import threading

class AHKEventReceiver:
    """接收AHK发送的事件"""
    
    def __init__(self, event_bus, port: int = 9999):
        self.event_bus = event_bus
        self.port = port
        self.running = False
        self.server_thread = None
        self.sock = None
    
    def start(self):
        """启动接收器"""
        self.running = True
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.server_thread.start()
    
    def _run_server(self):
        """运行UDP服务器"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('localhost', self.port))
        self.sock.settimeout(1.0)
        
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                event = data.decode('utf-8')
                self._handle_event(event)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[AHK事件] 接收错误: {e}")
    
    def _handle_event(self, event: str):
        """处理AHK事件"""
        parts = event.split(":", 1)
        if len(parts) < 2:
            return
        
        event_type = parts[0]
        data = parts[1]
        
        if event_type == "priority_key_down":
            self.event_bus.publish("scheduler_pause_requested")
        elif event_type == "priority_key_up":
            self.event_bus.publish("scheduler_resume_requested")
    
    def stop(self):
        """停止接收器"""
        self.running = False
        if self.sock:
            self.sock.close()
        if self.server_thread:
            self.server_thread.join(timeout=2)
```

**验收标准**：
- [ ] 能接收AHK事件
- [ ] 事件处理正确
- [ ] 线程安全

### Day 6-7：重构InputHandler

#### 任务2.3：AHKInputHandler
```python
# 文件: torchlight_assistant/core/ahk_input_handler.py

class AHKInputHandler:
    """基于AHK的完整输入处理器"""
    
    def __init__(self, event_bus=None, debug_display_manager=None):
        self.event_bus = event_bus
        self.debug_display_manager = debug_display_manager
        
        # AHK组件
        self.command_sender = AHKCommandSender(AHK_CONFIG["window_title"])
        self.event_receiver = AHKEventReceiver(event_bus)
        
        # 启动
        self._init_ahk_server()
        self.event_receiver.start()
        
        # 注册优先级按键Hook
        self._register_priority_hooks()
    
    def _register_priority_hooks(self):
        """注册优先级按键Hook"""
        priority_keys = ["space", "right_mouse", "e"]
        for key in priority_keys:
            self.command_sender.register_hook(key, "intercept")
    
    # 公共API
    def send_key(self, key: str, priority: int = 2):
        """发送按键"""
        return self.command_sender.send_key(key, priority)
    
    def execute_hp_potion(self, key: str):
        """执行HP药剂（紧急优先级）"""
        return self.command_sender.send_key(key, priority=0)
    
    # ... 其他方法
```

**验收标准**：
- [ ] API与原InputHandler兼容
- [ ] 优先级按键正常工作
- [ ] 事件接收正常

### Day 7-8：移除旧代码

#### 任务2.4：清理Python侧Hook和队列
- [ ] 移除CtypesHotkeyManager
- [ ] 移除MultiPriorityQueue
- [ ] 移除Python侧的按键队列逻辑
- [ ] 更新所有引用

**验收标准**：
- [ ] 代码编译通过
- [ ] 无未使用的导入
- [ ] 测试通过

---

## 🎯 阶段3：集成测试（2-3天）

### Day 9-10：功能测试

#### 测试清单
- [ ] Hook拦截测试
  - [ ] 优先级按键能正确拦截
  - [ ] 无自拦截问题
  - [ ] 前置延迟生效
  
- [ ] 队列测试
  - [ ] 四级优先级正确
  - [ ] 暂停时只处理紧急队列
  - [ ] 队列溢出处理
  
- [ ] 按键序列测试
  - [ ] 简单序列：`delay50,q`
  - [ ] 复杂序列：`q,delay100,w,delay100,e`
  - [ ] 组合键序列：`shift+q,delay100,w`
  
- [ ] 通信测试
  - [ ] Python → AHK延迟
  - [ ] AHK → Python延迟
  - [ ] 丢包率
  
- [ ] 稳定性测试
  - [ ] 长时间运行（8小时）
  - [ ] 高频发送（100次/秒）
  - [ ] 异常恢复

### Day 10-11：性能测试

#### 性能指标
- [ ] 端到端延迟：检测→决策→AHK→游戏
- [ ] 队列处理延迟：<10ms
- [ ] 通信延迟：<1ms
- [ ] CPU占用：<5%
- [ ] 内存占用：<50MB

---

## 🎯 阶段4：文档和优化（1-2天）

### Day 12：文档更新

- [ ] 更新WIKI/02-架构与核心概念.md
- [ ] 更新WIKI/03-功能模块与API.md
- [ ] 编写迁移指南
- [ ] 更新README.md

### Day 13：代码审查和优化

- [ ] 代码审查
- [ ] 性能优化
- [ ] 错误处理完善
- [ ] 日志输出优化

---

## 📊 进度跟踪

| 阶段 | 任务 | 状态 | 完成日期 |
|------|------|------|----------|
| 1 | 四级优先队列 | ⏳ 待开始 | - |
| 1 | 队列处理器 | ⏳ 待开始 | - |
| 1 | 动作执行器 | ⏳ 待开始 | - |
| 1 | Hook管理系统 | ⏳ 待开始 | - |
| 1 | 通信系统 | ⏳ 待开始 | - |
| 2 | AHKCommandSender | ⏳ 待开始 | - |
| 2 | AHKEventReceiver | ⏳ 待开始 | - |
| 2 | AHKInputHandler | ⏳ 待开始 | - |
| 2 | 清理旧代码 | ⏳ 待开始 | - |
| 3 | 功能测试 | ⏳ 待开始 | - |
| 3 | 性能测试 | ⏳ 待开始 | - |
| 4 | 文档更新 | ⏳ 待开始 | - |
| 4 | 代码审查 | ⏳ 待开始 | - |

---

## 🎯 下一步行动

1. **立即开始**：阶段1任务1.1 - 四级优先队列
2. **准备工作**：
   - 备份当前代码
   - 创建测试环境
   - 准备测试数据

3. **每日检查点**：
   - 每天结束时提交代码
   - 更新进度跟踪表
   - 记录遇到的问题

---

**创建时间**：2025-10-15
**分支**：feature/ahk-input-system
**状态**：计划完成，准备实施
