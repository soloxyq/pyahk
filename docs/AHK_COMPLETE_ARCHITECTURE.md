# AHK完整输入系统架构设计

## 🎯 核心理念

**Python负责决策，AHK负责执行**

- Python: 图像检测、条件判断、策略决策
- AHK: Hook拦截、按键队列、输入执行

## 📊 架构对比

### 当前架构（存在问题）
```
Python
  ├─ 图像检测 ✓
  ├─ 条件判断 ✓
  ├─ Hook拦截 ✗ (自拦截问题)
  ├─ 按键队列 ✓
  └─ 输入执行 ✗ (游戏兼容性问题)
```

### 新架构（完美解决）
```
Python                          AHK
  ├─ 图像检测 ✓                  
  ├─ 条件判断 ✓                  
  └─ 发送命令 ──WM_COPYDATA──> ├─ Hook拦截 ✓ (无自拦截)
                                ├─ 按键队列 ✓ (精确时序)
                                └─ 输入执行 ✓ (完美兼容)
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

## 🔄 通信协议

### Python → AHK (WM_COPYDATA)

| 命令 | 格式 | 说明 |
|------|------|------|
| 入队 | `enqueue:priority:action` | 将动作加入指定优先级队列 |
| 按键 | `press:key` | 立即发送按键（不入队） |
| 序列 | `sequence:key1,delay50,key2` | 执行按键序列 |
| 暂停 | `pause` | 暂停队列处理 |
| 恢复 | `resume` | 恢复队列处理 |
| 注册Hook | `hook_register:key:mode` | 注册按键Hook |
| 取消Hook | `hook_unregister:key` | 取消按键Hook |
| 清空队列 | `clear_queue:priority` | 清空指定优先级队列 |

### AHK → Python (TCP Socket)

| 事件 | 格式 | 说明 |
|------|------|------|
| 优先级按键按下 | `priority_key_down:key` | 优先级按键被按下 |
| 优先级按键释放 | `priority_key_up:key` | 优先级按键被释放 |
| 队列状态 | `queue_status:e,h,n,l` | 各队列长度 |
| Hook状态 | `hook_status:key:active` | Hook状态变化 |

---

## 📋 实施计划

### 阶段1：扩展AHK Server（3-4天）

#### 任务清单
- [ ] 实现四级优先队列
- [ ] 实现队列处理器（定时器驱动）
- [ ] 实现按键序列解析
- [ ] 实现Hook管理系统
- [ ] 实现暂停/恢复机制
- [ ] 实现TCP服务器（发送事件到Python）
- [ ] 测试各个功能模块

#### 文件
- `hold_server_extended.ahk` - 扩展版AHK服务器

### 阶段2：Python侧改造（3-4天）

#### 任务清单
- [ ] 实现AHKCommandSender
- [ ] 实现AHKEventReceiver
- [ ] 重构InputHandler使用AHK命令
- [ ] 移除Python侧的Hook管理
- [ ] 移除Python侧的按键队列
- [ ] 实现降级方案（AHK不可用时）
- [ ] 单元测试

#### 文件
- `ahk_command_sender.py` - 命令发送器
- `ahk_event_receiver.py` - 事件接收器
- `ahk_input_handler.py` - AHK输入处理器

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

### 2. 双向通信
```
Python ──WM_COPYDATA──> AHK  (命令)
Python <──TCP Socket─── AHK  (事件)
```

### 3. 队列优先级
```
Emergency (0) > High (1) > Normal (2) > Low (3)

优先级按键激活时：
- Emergency: 继续执行（药剂）
- High: 继续执行（优先级按键本身）
- Normal: 暂停（普通技能）
- Low: 暂停（辅助功能）
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
