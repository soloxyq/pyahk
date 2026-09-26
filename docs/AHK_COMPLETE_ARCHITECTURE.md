# AHK完整输入系统架构设计（历史档案）

> **历史档案，不是当前实现规范。** 本文保留 2025 年迁移阶段的设计记录，其中的旧命令示例、10/20ms 队列周期、TCP 事件接收和“自动重启”等内容可能与当前源码不一致。当前实现请阅读 [AHK_CURRENT_ARCHITECTURE.md](AHK_CURRENT_ARCHITECTURE.md) 和 [wiki/02 — 架构与通信](../wiki/02-架构与核心概念.md)；命令 ID 以 `torchlight_assistant/config/ahk_commands.py` 与 `ahk_commands.ahk` 为准。

## 🎯 核心理念

**Python负责决策，AHK负责执行**

- Python: 图像检测、条件判断、策略决策
- AHK: Hook拦截、按键队列、输入执行

## 📊 架构对比

### 当前架构（已实现）
```
Python (PySide6主线程)              AHK (hold_server_extended.ahk)
  ├─ 图像检测 ✓                       
  ├─ 条件判断 ✓                       
  ├─ 决策逻辑 ✓                       
  │                                  
  ├─ WM_COPYDATA发送命令 ───────> ├─ Hook管理 ✓ ($ 前缀防自拦截)
  │                 (0.069ms)    ├─ 四级优先队列 ✓ (20ms处理)
  │                              ├─ 特殊按键过滤 ✓ (HP/MP优先)
  │                              └─ SendInput执行 ✓ (游戏兼容)
  │                                  │
  └─ Signal桥接收事件 <──WM_COPYDATA──── └─ 事件发送 ✓
     (主线程处理)            (intercept_key_down/special_key_down等)
```

---

## 🏗️ 详细设计

### 1. AHK Server职责

#### 1.1 Hook管理
```autohotkey
; 动态Hook注册
RegisterHook(key, mode) {
    ; mode: "monitor" - 仅监控，不拦截
    ;       "intercept" - 拦截并重发
    ;       "block" - 完全阻止
}

; 优先级按键Hook
$space::  ; $ 前缀防止自拦截
{
    ; 1. 通知Python暂停调度
    SendToPython("priority_key_down:space")
    
    ; 2. 延迟后发送到游戏
    Sleep 50
    Send "{space}"
}

$space up::
{
    SendToPython("priority_key_up:space")
}
```

#### 1.2 按键队列系统
```autohotkey
; 四级优先队列
global EmergencyQueue := []  ; 0 - 紧急（药剂）
global HighQueue := []        ; 1 - 高（优先级按键）
global NormalQueue := []      ; 2 - 普通（技能）
global LowQueue := []         ; 3 - 低（辅助）

; 队列处理器
ProcessQueue() {
    ; 按优先级处理
    if (EmergencyQueue.Length > 0) {
        ExecuteAction(EmergencyQueue.RemoveAt(1))
    } else if (HighQueue.Length > 0) {
        ExecuteAction(HighQueue.RemoveAt(1))
    } else if (NormalQueue.Length > 0) {
        ExecuteAction(NormalQueue.RemoveAt(1))
    } else if (LowQueue.Length > 0) {
        ExecuteAction(LowQueue.RemoveAt(1))
    }
}

; 定时器驱动
SetTimer(ProcessQueue, 10)  ; 每10ms处理一次
```

#### 1.3 按键序列支持
```autohotkey
; 解析并执行序列
ExecuteSequence(sequence) {
    ; sequence: "delay50,q,delay100,w"
    parts := StrSplit(sequence, ",")
    for index, part in parts {
        if (InStr(part, "delay")) {
            ms := SubStr(part, 6)
            Sleep ms
        } else {
            Send "{" part "}"
        }
    }
}
```

#### 1.4 状态管理
```autohotkey
; 全局状态
global IsPaused := false
global PriorityKeysActive := Map()

; 暂停/恢复
Pause() {
    IsPaused := true
}

Resume() {
    IsPaused := false
}

; 队列处理时检查状态
ProcessQueue() {
    if (IsPaused) {
        return  ; 暂停时不处理普通队列
    }
    ; ... 处理队列
}
```

### 2. Python侧职责

#### 2.1 命令发送器
```python
class AHKCommandSender:
    """AHK命令发送器"""
    
    def send_command(self, cmd: str, priority: int = 2):
        """
        发送命令到AHK
        
        Args:
            cmd: 命令字符串
            priority: 0=emergency, 1=high, 2=normal, 3=low
        """
        command = f"enqueue:{priority}:{cmd}"
        send_ahk_cmd(self.ahk_window, command)
    
    def send_key(self, key: str, priority: int = 2):
        """发送按键"""
        self.send_command(f"press:{key}", priority)
    
    def send_sequence(self, sequence: str, priority: int = 2):
        """发送按键序列"""
        self.send_command(f"sequence:{sequence}", priority)
    
    def pause_scheduler(self):
        """暂停调度器"""
        send_ahk_cmd(self.ahk_window, "pause")
    
    def resume_scheduler(self):
        """恢复调度器"""
        send_ahk_cmd(self.ahk_window, "resume")
    
    def register_hook(self, key: str, mode: str = "monitor"):
        """注册Hook"""
        send_ahk_cmd(self.ahk_window, f"hook_register:{key}:{mode}")
    
    def unregister_hook(self, key: str):
        """取消Hook"""
        send_ahk_cmd(self.ahk_window, f"hook_unregister:{key}")
```

#### 2.2 事件接收器
```python
class AHKEventReceiver:
    """接收AHK发送的事件"""
    
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.server_thread = None
        self._start_server()
    
    def _start_server(self):
        """启动TCP服务器接收AHK事件"""
        # AHK通过TCP发送事件到Python
        # 例如: "priority_key_down:space"
        pass
    
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
```

---

## 🔄 通信协议 (实际实现)

### Python → AHK (WM_COPYDATA - 发送命令)

使用 `hold_client.send_ahk_cmd(window_title, cmd_id, param)`

| 命令ID | 命令名 | 参数格式 | 说明 |
|--------|--------|------------|------|
| 1 | PING | - | 测试连接 |
| 2 | SET_TARGET | `"ahk_exe game.exe"` | 设置目标窗口 |
| 3 | ACTIVATE | - | 激活目标窗口 |
| 4 | ENQUEUE | `"priority:action"` | 入队动作 (0-3:级别, press:key) |
| 5 | CLEAR_QUEUE | `"priority"` | 清空队列 (-1=全部) |
| 6 | PAUSE | - | 暂停队列处理 |
| 7 | RESUME | - | 恢复队列处理 |
| 8 | HOOK_REGISTER | `"key:mode"` | 注册Hook (mode: intercept/monitor/special/priority) |
| 9 | HOOK_UNREGISTER | `"key"` | 取消Hook |
| 12 | SET_STATIONARY | `"active:type"` | 设置原地模式 (true:shift_modifier) |
| 13 | SET_FORCE_MOVE_KEY | `"key"` | 设置强制移动键 |
| 14 | SET_FORCE_MOVE_STATE | `"active"` | 设置强制移动状态 |
| 15 | SET_MANAGED_KEY_CONFIG | `"key:target:delay"` | 设置管理按键配置 (e:+:500) |
| 16 | CLEAR_HOOKS | - | 清空所有可配置Hook (保留F8) |
| 18 | SET_PYTHON_WINDOW_STATE | `"main"`/`"osd"` | 设置Python窗口状态 |
| 19 | BATCH_UPDATE_CONFIG | `"hp_key:1,mp_key:2"` | 批量配置更新 |

### AHK → Python (WM_COPYDATA - 发送事件)

AHK通过 `SendToPython(event_str)` 发送事件,Python通过 `signal_bridge` 在主线程处理

| 事件类型 | 格式 | 说明 |
|----------|------|------|
| intercept_key_down | `"intercept_key_down:F8"` | 系统热键拦截 (F8/F7/F9/z) |
| special_key_down | `"special_key_down:space"` | 特殊按键按下 (状态跟踪) |
| special_key_up | `"special_key_up:space"` | 特殊按键释放 |
| special_key_pause | `"special_key_pause:start"`/`"end"` | 特殊按键暂停/恢夏 |
| managed_key_down | `"managed_key_down:e"` | 管理按键按下 (延迟+映射) |
| managed_key_complete | `"managed_key_complete:e"` | 管理按键处理完成 |
| priority_key_down | `"priority_key_down:key"` | 优先级按键按下 (兼容旧版) |
| priority_key_up | `"priority_key_up:key"` | 优先级按键释放 (兼容旧版) |
| monitor_key_down | `"monitor_key_down:a"` | 监控按键按下 (不拦截) |
| monitor_key_up | `"monitor_key_up:a"` | 监控按键释放 |

---

## 💚 实施状态

### ✅ 已完成功能

#### AHK Server (`hold_server_extended.ahk`)
- ✅ 四级优先队列 (Emergency/High/Normal/Low)
- ✅ 20ms定时器驱动的队列处理器
- ✅ 按键序列支持 (delay,press,hold,release)
- ✅ 动态Hook管理 (intercept/monitor/special/priority)
- ✅ 特殊按键过滤机制 (只允许HP/MP通过)
- ✅ 暂停/恢复机制
- ✅ 性能优化 (队列计数器+字符串缓存+异步延迟)
- ✅ 原地模式支持 (shift_modifier/force_shift/toggle)
- ✅ WM_COPYDATA通信 (19个命令)

#### Python侧 (`torchlight_assistant/`)
- ✅ `AHKCommandSender` - WM_COPYDATA命令发送
- ✅ `signal_bridge.py` - Qt信号桥(主线程事件处理)
- ✅ `AHKInputHandler` - 兼容原API的输入处理器
- ✅ 移除Python侧的Hook管理 (全部由AHK负责)
- ✅ 命令协议 (`ahk_commands.py` + `ahk_commands.ahk`)
- ✅ 配置系统 (`ahk_config.py`)

### 🚧 已知问题

1. **没有降级方案**: AHK不可用时系统无法启动 (设计决策: 不需要降级)
2. **特殊按键延迟窗口期清空技能**: 延迟期间清空非紧急队列,防止技能积累 (已修复)

### 阶段3：集成测试（2-3天）

#### 测试项目
- [ ] Hook拦截测试（验证无自拦截）
- [ ] 优先级队列测试
- [ ] 按键序列测试
- [ ] 暂停/恢复测试
- [ ] 性能测试（端到端延迟）
- [ ] 稳定性测试（长时间运行）
- [ ] 游戏兼容性测试

### 阶段4：文档和优化（1-2天）

#### 任务清单
- [ ] 更新WIKI文档
- [ ] 编写迁移指南
- [ ] 性能优化
- [ ] 代码审查

---

## 🎯 预期收益

### 功能收益
1. ✅ **彻底解决Hook自拦截** - AHK的`$`前缀完美解决
2. ✅ **更精确的时序控制** - AHK队列处理器，10ms精度
3. ✅ **解决技能前后摇** - Hook层面控制，可以等待游戏响应
4. ✅ **更好的游戏兼容性** - AHK的SendInput更可靠
5. ✅ **简化Python代码** - 移除复杂的Hook和队列管理

### 性能影响
- 通信延迟：0.069ms（实测）
- 队列处理：10ms周期
- 总体影响：<1%

### 架构优势
- **职责清晰**：Python决策，AHK执行
- **易于维护**：各司其职，互不干扰
- **易于扩展**：AHK可以添加更多游戏相关功能

---

## 🔍 关键技术点

### 1. AHK Hook的`$`前缀
```autohotkey
; 没有$前缀 - 会自拦截
space::
{
    Send "{space}"  ; 这个会被自己拦截！
}

; 有$前缀 - 不会自拦截
$space::
{
    Send "{space}"  ; 这个不会被拦截
}
```

### 2. 通信机制 (实际实现)
```
Python ──WM_COPYDATA──> AHK  (命令, hold_client.py)
Python <──WM_COPYDATA─── AHK  (事件, SendToPython函数)
       └──Signal桥─────> 主线程处理 (signal_bridge.py)

性能数据:
- WM_COPYDATA通信延迟: 0.069ms (实测)
- AHK窗口句柄缓存: 首次查找后永久缓存
- Qt Signal桥: 自动转换到主线程,线程安全
```

### 3. 队列优先级与过滤机制 (实际实现)
```
Emergency (0) > High (1) > Normal (2) > Low (3)

正常模式 (IsPaused=false, SpecialKeysPaused=false):
- 按优先级依次处理: Emergency > High > Normal > Low

手动暂停 (IsPaused=true):
- 仅Emergency队列继续执行 (HP/MP药剂)
- 其他全部暂停

特殊按键激活 (SpecialKeysPaused=true):
- Emergency队列: 始终执行
- High/Normal/Low队列: 只执行紧急动作 (IsEmergencyAction判断)
  - 紧急动作: press:缓存的HP/MP按键 (CachedHpKey/CachedMpKey)
  - 非紧急动作: 过滤丢弃

延迟窗口期 (DelayUntil > 0):
- Emergency队列: 继续执行
- 其他队列: 被清空 (ClearNonEmergencyQueues)
```

---

## 📊 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| AHK进程崩溃 | 低 | 高 | 进程监控+自动重启 |
| 通信延迟过高 | 极低 | 中 | 已实测0.069ms |
| TCP通信不稳定 | 低 | 中 | 重连机制 |
| Hook冲突 | 低 | 中 | 动态Hook管理 |
| 队列溢出 | 低 | 低 | 队列大小限制+丢弃策略 |

**设计原则**：
- ✅ **全面使用AHK** - 不搞降级方案
- ✅ **遇到问题就解决** - 不绕路
- ✅ **保持代码简洁** - 不增加复杂度

---

## 💡 总结

这个完整的AHK输入系统架构：

1. **彻底解决了Hook自拦截问题** - 使用AHK的`$`前缀
2. **实现了精确的时序控制** - AHK队列处理器
3. **简化了Python代码** - 移除复杂的Hook和队列管理
4. **提升了游戏兼容性** - AHK的SendInput更可靠
5. **保持了高性能** - 通信延迟仅0.069ms

**这是一个更优雅、更可靠的解决方案！**

---

**创建时间**：2025-10-15
**分支**：feature/ahk-input-system
**状态**：设计完成，待实施
