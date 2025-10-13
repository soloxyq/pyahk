# 03 功能模块与 API

本章覆盖 pyahk 的核心功能模块（技能系统、智能药剂、装备洗练、自动寻路、优先级输入）及其常用 API，既可独立运行，也可协同工作。

## 🎮 优先级按键系统（高精度手动操作）

### 系统概述
优先级按键系统基于**Windows Hook低级监听**实现了**选择性系统事件拦截**，允许部分按键（如空格键）保持游戏原生响应，而其他按键（如E键、右键）由程序完全接管。这种混合模式在提供程序精确控制的同时，确保关键操作（如闪避）的游戏体验不受影响。

**🔥 重大升级 (2025.01)**: 从pynput迁移到CtypesHotkeyManager，彻底解决了输入无响应问题。

### 核心架构：Windows Hook选择性事件拦截

#### 按键分类机制
系统自动将优先级按键分为两类：

| 分类 | 按键示例 | 处理方式 | 用途 |
|------|----------|----------|------|
| **特殊按键** | space | 游戏原生响应 + 程序监控 (suppress="never") | 闪避、格挡等需要即时响应的操作 |
| **管理按键** | E, right_mouse | 程序完全接管 (suppress="always") | 技能、攻击等可由程序精确控制的操作 |

#### 技术实现：Windows Hook事件拦截
```python
def _start_priority_listeners(self):
    """使用热键管理器注册优先级按键监听"""
    for key_name in self._priority_keys_config.keys():
        # 根据按键类型选择suppress模式
        suppress_mode = "never" if key_name in self._special_keys else "always"
        
        self.hotkey_manager.register_key_event(
            key_name,
            on_press=lambda k=key_name: self._on_priority_key_press(k),
            on_release=lambda k=key_name: self._on_priority_key_release(k),
            suppress=suppress_mode  # "never"=不拦截, "always"=完全拦截
        )
```

### 多级优先级队列系统

| 优先级 | 名称 | 数值 | 典型用例 | 执行策略 |
|--------|------|------|----------|----------|
| emergency | 紧急 | 0 (最高) | 前置延迟、HP/MP药剂 | 永不被暂停，立即执行 |
| high | 高 | 1 | 优先级按键本身 | 仅在非优先级模式下执行 |
| normal | 普通 | 2 | 常规技能、Buff | 被优先级按键状态暂停 |
| low | 低 | 3 | 辅助/UI/拾取 | 被优先级按键状态暂停 |

### 前置延迟机制

#### 设计目标
解决程序响应过快导致游戏来不及处理"暂停"状态的问题，确保优先级操作的绝对可靠性。

#### 执行流程
```
玩家按下优先级按键（如空格）
    ↓
1. 立即发布 scheduler_pause_requested 事件
    ↓
2. 向emergency队列推入前置延迟：delay50
    ↓
3. 向high队列推入按键本身：space
    ↓
4. 执行：先等待50ms，再发送按键
```

#### 配置示例
```json
{
  "priority_keys": {
    "enabled": true,
    "keys": ["space", "E", "right_mouse"],
    "delay_ms": 50,
    "special_keys": ["space"]  // 保持游戏原生响应
  }
}
```

## 🎮 多级优先级与序列系统（核心调度引擎）

### 系统概述
这是技能自动化的"输入调度大脑"，提供精细化的按键优先级管理和智能的执行控制。通过**四级优先队列**、**按键序列**和**前置延迟**等机制，实现游戏操作的精确控制和"前后摇"冲突的智能避免。

## 🎯 技能自动化系统

### 概述
技能系统是pyahk的核心功能，提供自动化的技能释放、冷却检测和优先级管理。支持多种触发模式和执---

## 💊 智能药剂系统

### 概述
智能药剂系统提供自动化的HP/MP检测和药剂使用功能。支持三种检测模式：矩形HSV检测、圆形HSV检测和**Text OCR数字识别**（推荐）。

### 三种检测模式对比

| 模式 | 技术 | 性能 | 准确率 | 适用场景 |
|------|------|------|--------|----------|
| **矩形检测** | HSV颜色匹配 | ~5ms | 90%+ | 传统模式，依赖颜色 |
| **圆形检测** | HSV颜色匹配 | ~5ms | 95%+ | 球形资源条，更精确 |
| **Text OCR** | 模板匹配 | <10ms | 99%+ | **推荐**，直接识别数字 |

### Text OCR模式（推荐）

#### 工作原理
直接识别游戏中显示的HP/MP数字文本（如"540/540"），无需依赖颜色变化。使用连通域分割和模板匹配技术，实现99%+的识别准确率。

#### 配置方法
```json
{
  "hp": {
    "detection_mode": "text_ocr",
    "text_x1": 97,
    "text_y1": 814,
    "text_x2": 218,
    "text_y2": 835,
    "match_threshold": 0.70,
    "threshold": 50  // 低于50%时触发
  }
}
```

#### GUI配置步骤
1. **选择模式**: 在"检测模式"下拉框选择"数字匹配 (Text OCR)"
2. **设置区域**: 
   - 手动输入：在坐标框输入 `x1,y1,x2,y2`（如 `97,814,218,835`）
   - 或使用"选择区域"按钮框选游戏中的HP/MP数字区域
3. **测试识别**: 点击"🧪 测试识别"按钮
   - 选择游戏截图文件
   - 系统会自动识别并显示结果
   - 验证配置是否正确

#### 🧪 测试功能
Text OCR模式提供专门的测试按钮，用于验证配置是否正确：

**使用方法**:
1. 在"检测模式"下拉框选择"数字匹配 (Text OCR)"
2. 点击"🧪 测试识别"按钮
3. 在文件对话框中选择游戏截图（.png/.jpg格式）
4. 系统将显示识别结果：
   - ✅ 识别成功：显示识别的数字和百分比
   - ❌ 识别失败：提供故障排查建议

**注意事项**:
- 测试时使用静态截图，实际运行时使用capture库的实时画面
- 确保测试图片的分辨率与实际游戏一致
- 如果测试成功但实际运行失败，可能是游戏分辨率或UI缩放改变

#### 技术特性
- **多尺度匹配**: 默认启用95%-105%范围，容忍字体大小变化
- **自适应阈值**: 可选功能，适应不同光照环境
- **性能优化**: 平均识别时间<8ms，不影响游戏性能

#### 故障排查
如果识别失败：
1. **首先检查**: 游戏分辨率/UI缩放是否改变
2. **重新生成模板**: 
   ```bash
   python torchlight_assistant/utils/digit_template_generator.py
   ```
3. **调整坐标**: 使用"选择区域"重新框选数字区域
4. **查看详细文档**: `wiki/08-故障排查手册.md`

### 矩形/圆形HSV检测模式

(此部分及之后内容保持不变)

---

🚀 **最新架构升级**：引入优先级按键暂停/恢复机制，当空格、右键等优先级按键激活时，系统智能暂停所有后台调度任务，实现"零资源浪费"的高效运行。

### 核心架构组件

#### UnifiedScheduler - 统一调度器
- **技术特点**：
  - 基于`heapq`的高效优先级队列，支持任务按执行时间排序。
  - `time.monotonic()`单调时间源，避免系统时钟调整和夏令时影响。
  - 支持任务的动态增删改、暂停/恢复和间隔更新。
  - 单线程调度避免竞态条件，提供亚毫秒级定时精度。

#### SkillManager - 智能技能管理器
- **核心功能**：
  - 自主调度：独立的统一调度器管理所有定时任务。
  - 事件驱动：通过事件总线响应优先级按键状态变化和配置更新。
  - 智能暂停：区分用户手动暂停和系统自动暂停。
  - 资源优化：优先级按键激活时完全停止CPU密集型操作，降低70-90% CPU占用。

### 三种触发模式

#### 1. 定时模式 (TriggerMode = 0)
```json
{
  "name": "持续技能",
  "key": "1",
  "trigger_mode": 0,
  "interval": 1000,  // 每1秒执行一次
  "priority": "normal"
}
```
- **功能**：基于固定时间间隔执行技能。
- **使用场景**：持续BUFF、光环类技能。

#### 2. 冷却模式 (TriggerMode = 1)
```json
{
  "name": "主要输出",
  "key": "delay50,q",
  "trigger_mode": 1,
  "cooldown_coords": [100, 50, 20, 20],
  "priority": "high"
}
```
- **功能**：基于技能图标冷却状态执行技能。
- **按键序列支持**: `key`字段现在支持逗号分隔的序列，如 `delay50,q` 表示"延迟50毫秒后按q"。这对于需要"技能前摇保护"或精确时序的连招非常有用。
- **检测原理**：采用统一的**HSV模板匹配**算法，能有效抵抗游戏内光照和技能图标动画效果的干扰。

#### 3. 按住模式 (TriggerMode = 2)
```json
{
  "name": "移动技能",
  "key": "shift",
  "trigger_mode": 2
}
```
- **功能**：进入运行状态时按下，停止时释放。
- **特点**：仅在状态切换时一次性执行，不参与循环或定时任务。
- **使用场景**：持续生效的光环、移动速度增强。

### 执行条件系统

#### 无条件模式 (ExecuteCondition = 0)
- 冷却就绪时直接执行主按键。

#### Buff限制模式 (ExecuteCondition = 1)
- **逻辑**：Buff存在时不执行，Buff消失时执行。
- **场景**：避免重复上BUFF。

#### 资源条件模式 (ExecuteCondition = 2)
- **逻辑**：资源充足执行主按键，不足执行备用按键。
- **场景**：根据MP/怒气状态切换技能。

---

## 🎮 多级优先级与序列系统（核心调度引擎）

### 系统概述
这是技能自动化的“输入调度大脑”，提供精细化的按键优先级管理和智能的执行控制。通过**四级优先队列**、**按键序列**和**前置延迟**等机制，实现游戏操作的精确控制和“前后摇”冲突的智能避免。

### 四级优先队列架构
pyahk采用多级优先队列系统，确保最关键的操作能被最先执行。

| 优先级 | 名称 | 数值 | 典型用例 | 执行策略 |
|--------|------|------|----------|----------|
| emergency | 紧急 | 0 (最高) | HP/MP 药剂、优先级按键的**前置延迟** | 永不被拦截，立即执行 |
| high | 高 | 1 | 优先级按键本身（如闪避）、核心输出技能 | 仅在非优先级模式下执行 |
| normal | 普通 | 2 | 常规技能、Buff | 被优先级按键状态暂停 |
| low | 低 | 3 | 辅助 / UI / 拾取 | 被优先级按键状态暂停 |

### 核心机制：前置延迟与按键序列

#### 1. 优先级按键：前置延迟机制 (技能前摇保护)
为确保闪避、格挡等高优先级操作的绝对可靠，系统引入了“前置延迟”机制。

**设计目标**:
- **绝对可靠**: 解决因程序响应过快，导致游戏客户端来不及处理“暂停”状态而“吞掉”闪避按键的问题。

**实现流程**:
```
玩家按下空格键 (优先级按键)
    ↓
1. InputHandler暂停其他技能调度 (发布 scheduler_pause_requested 事件)
    ↓
2.【关键】向"紧急(emergency)"队列推入一个延迟指令: `delay50`
    ↓
3. 向"高(high)"优先级队列推入空格键本身: `space`
    ↓
4. 输入处理线程按顺序执行：先等待50ms，再执行空格。
```
通过这个短暂的、高优先级的延迟，确保了游戏有足够的时间进入“玩家正在手动操作”的状态，从而100%响应后续的闪避按键。

**配置方法**:
此延迟可在UI的“优先级按键”标签页中配置，或直接在`json`中设置。
```json
{
  "priority_keys": {
    "enabled": true,
    "keys": ["space", "right_mouse"],
    "delay_ms": 50 
  }
}
```

#### 2. 技能按键：序列支持
所有技能的`key`和`alt_key`字段现在都支持逗号分隔的按键序列，以实现复杂的宏命令。

**语法示例**:
- `delay50,q`: 延迟50ms后按q。用于等待技能前摇结束。
- `q,delay100,w`: 按q，等待100ms后按w。用于精确控制连招时序。

**应用场景**:
- **连招控制**: `q,delay100,w,delay100,e`
- **规避动画**: 在长动画技能后加入延迟，再释放下一个技能。

#### 3. 架构实现：序列的可靠执行与去重
为了确保按键序列的可靠执行，`InputHandler`内部实现了智能的去重和清理机制。

- **整体去重**: 整个序列（如`"delay50,q"`）被视为一个单元。在它执行完毕前，重复的请求会被忽略。
- **智能清理**: 当序列的最后一个指令执行完毕后，系统会自动向队列中插入一个内部的`__cleanup_sequence__{序列名}`标记。处理线程识别到此标记后，会将该序列从"正在执行"的集合中移除，允许它被再次触发。
- **选择性去重**: `_should_deduplicate_key`方法确保了`delay`指令、药剂等关键操作永远不会被去重。

这个设计确保了即使用户配置了复杂的长序列，系统也能稳定、可预测地执行，避免了指令堆积或意外丢失。

### 与优先级按键系统集成

优先级按键系统与调度引擎深度集成，当检测到优先级按键操作时：

1. **即时暂停**: 通过事件总线发布 `scheduler_pause_requested` 事件
2. **前置延迟**: 向emergency队列推入延迟指令确保游戏状态同步
3. **优先执行**: 向high队列推入按键指令保证最高优先级
4. **自动恢复**: 释放按键后自动恢复正常调度

这种设计确保了手动操作的绝对优先级和游戏体验的流畅性。

---

## 🔌 常用 API 索引（精选）

以下列出项目内最常用、对二次开发最友好的 API（完整细节已在原 09 文档中，现合并精简于此）：

### EventBus
```python
from torchlight_assistant.core.event_bus import event_bus

def handler(data=None, **kw):
  ...

event_bus.subscribe("custom:event", handler)
event_bus.publish("custom:event", data={"msg": "hi"})
event_bus.unsubscribe("custom:event", handler)
```

内置事件要点：
- engine:state_changed, engine:config_updated, engine:shutdown
- ui:load_config_requested, ui:save_full_config_requested
- scheduler_pause_requested, scheduler_resume_requested

### UnifiedScheduler
```python
from torchlight_assistant.core.unified_scheduler import UnifiedScheduler

s = UnifiedScheduler()
s.add_task(task_id="tick", interval=0.5, callback=lambda: None)
s.pause(); s.resume(); s.remove_task("tick")
s.add_one_time_task(task_id="later", delay=2.0, callback=lambda: None)
```

### InputHandler（支持序列与优先级）
```python
from torchlight_assistant.core.input_handler import InputHandler

ih = InputHandler()
ih.execute_skill_normal("delay50,q")
ih.execute_skill_high("q,delay100,w")
ih.execute_utility("tab")
ih.execute_hp_potion("1")

# 队列与状态
ih.get_queue_length(); ih.get_queue_stats(); ih.clear_queue()
ih.is_priority_mode_active(); ih.get_active_priority_keys()
```

序列语法：delayX，组合键如 shift+q；逗号分隔；末尾自动清理标记防止重复堆积。

### BorderFrameManager（图像/模板缓存）
```python
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager

bm = BorderFrameManager()
frame = bm.get_current_frame()
roi = bm.get_region_frame(100, 50, 200, 100)
bm.set_template_cache("hp", {"hsv": [0,0,0]})
bm.get_template_cache("hp"); bm.clear_cache()
```

### 自定义扩展点（概览）
- 模块基类：实现 start/stop/_process_iteration，订阅事件即可接入
- 输入处理器：can_process/process_action 增强输入语义
- 条件检查器：check_condition(frame, skill_config) 拓展执行条件

更多高级示例（插件、AI 决策、游戏 Profile 工厂）可参考原 09 文档内容，现已折叠至本章附录或源代码注释。