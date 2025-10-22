# 03 功能模块与 API

本章覆盖 pyahk 的核心功能模块（技能系统、智能药剂、装备洗练、自动寻路、优先级输入）及其常用 API，既可独立运行，也可协同工作。

## 🎮 优先级按键系统（高精度手动操作）

### 系统概述

优先级按键系统基于 **AHK + WM_COPYDATA 架构**，实现了**选择性系统事件拦截**和**异步非阻塞处理**。允许部分按键（如空格键）保持游戏原生响应，而其他按键（如 E 键、右键）由程序完全接管。这种混合模式在提供程序精确控制的同时，确保关键操作（如闪避）的游戏体验不受影响。

**🔥 全面重构 (2025.10)**: 完全重写为AHK独立进程 + WM_COPYDATA双向通信，替代所有Python Hook，实现异步非阻塞和智能缓存。

### 核心架构：AHK 进程分离 + WM_COPYDATA 通信

#### AHK 按键模式

|| 模式 | AHK符号 | 拦截 | 释放事件 | 用途 | 示例 |
||------|---------|------|---------|------|------|
|| **intercept** | `$` | ✅ | ❌ | 技能键、系统热键 | Q/W/E/R、F8/Z |
|| **priority** | `$` | ✅ | ❌ | 管理按键（延迟+映射） | E→Shift |
|| **special** | `~` | ❌ | ✅ | 特殊按键（暂停系统） | Space |
|| **monitor** | `~` | ❌ | ✅ | 监控按键（状态检测） | A键（强制移动） |
|| **block** | `$` | ✅ | ❌ | 禁用按键 | - |

#### 双向通信流程

```
Python → AHK（命令发送）:
AHKCommandSender.register_hook("e", "priority")
  → hold_client.send_ahk_cmd(CMD_HOOK_REGISTER, "e:priority")
  → WM_COPYDATA → AHK窗口 → RegisterHook()

AHK → Python（事件发送）:
用户按E键 → HandleManagedKey("e")
  → SendEventToPython("managed_key_down:e")
  → WM_COPYDATA → Python窗口 → SignalBridge → EventBus
```

### 多级优先级队列系统

| 优先级    | 名称 | 数值     | 典型用例             | 执行策略               |
| --------- | ---- | -------- | -------------------- | ---------------------- |
| emergency | 紧急 | 0 (最高) | 前置延迟、HP/MP 药剂 | 永不被暂停，立即执行   |
| high      | 高   | 1        | 优先级按键本身       | 仅在非优先级模式下执行 |
| normal    | 普通 | 2        | 常规技能、Buff       | 被优先级按键状态暂停   |
| low       | 低   | 3        | 辅助/UI/拾取         | 被优先级按键状态暂停   |

### 异步非阻塞延迟机制

#### 设计目标

解决传统 Sleep() 导致的阻塞问题，使用 DelayUntil 状态机实现完全异步的前置延迟，确保技能后摇时间内不会有其他按键干扰。

#### AHK 端实现流程

```ahk
// 管理按键处理（E键示例）
HandleManagedKey(key) {
    // 1. 拦截原始按键，不传递给游戏
    // 2. 将延迟和按键放入Emergency队列
    EmergencyQueue.Push("delay:50")
    EmergencyQueue.Push("press:shift")
    // 3. 通知Python（仅日志）
    SendEventToPython("managed_key_down:e")
}

// 队列处理器（每10ms调用）
ProcessQueue() {
    // 检查异步延迟
    if (DelayUntil > 0) {
        if (A_TickCount < DelayUntil) {
            return  // 还在延迟中，所有队列冻结
        } else {
            DelayUntil := 0  // 延迟结束，自动重置
        }
    }
    
    // 处理Emergency队列
    if (EmergencyQueue.Length > 0) {
        action := EmergencyQueue.RemoveAt(1)
        if (action = "delay:50") {
            DelayUntil := A_TickCount + 50  // 设置延迟结束时间
        } else {
            ExecuteAction(action)  // 执行按键
        }
        return
    }
    
    // 处理其他队列...
}
```

#### 配置示例 (2025.10新格式)

```json
{
  "priority_keys": {
    "enabled": true,
    "special_keys": ["space"],
    "managed_keys": {
      "e": {
        "target": "shift",
        "delay": 50
      },
      "right_mouse": {
        "target": "right_mouse",
        "delay": 30
      }
    }
  }
}
```

## 🎮 AHK 四级优先队列系统（核心调度引擎）

### 系统概述

这是基于 AHK 的全新输入调度系统，通过**独立进程**和**四级优先队列**实现精细化的按键管理。采用 **DelayUntil 状态机** 实现异步非阻塞延迟，完全消除传统 Sleep() 导致的性能问题。

### AHK 端四级队列实现

```ahk
// 全局队列定义
global EmergencyQueue := []  // 紧急队列（优先级 0）
global HighQueue := []       // 高优先级队列（优先级 1）
global NormalQueue := []     // 普通队列（优先级 2）
global LowQueue := []        // 低优先级队列（优先级 3）

// 异步延迟状态
global DelayUntil := 0       // 0=无延迟，>0=延迟结束时间

// 每10ms调用的队列处理器
ProcessQueue() {
    // 1. 检查异步延迟
    if (DelayUntil > 0) {
        if (A_TickCount < DelayUntil) {
            return  // 还在延迟中，所有队列冻结
        } else {
            DelayUntil := 0  // 延迟结束，自动重置
        }
    }
    
    // 2. Emergency队列永远最先执行（即使在延迟期间）
    if (EmergencyQueue.Length > 0) {
        action := EmergencyQueue.RemoveAt(1)
        ExecuteAction(action)
        return
    }
    
    // 3. 检查暂停标志
    if (IsPaused || SpecialKeysPaused) {
        return
    }
    
    // 4. 按优先级处理其他队列
    if (HighQueue.Length > 0) {
        action := HighQueue.RemoveAt(1)
        ExecuteAction(action)
    } else if (NormalQueue.Length > 0) {
        action := NormalQueue.RemoveAt(1)
        ExecuteAction(action)
    } else if (LowQueue.Length > 0) {
        action := LowQueue.RemoveAt(1)
        ExecuteAction(action)
    }
}

SetTimer(ProcessQueue, 10)  // 每10ms调用一次
```

### Python 端 API 调用

```python
# 发送不同优先级的按键
self.input_handler.execute_hp_potion("c")     # Emergency (0) - 新的HP按键
self.input_handler.execute_mp_potion("2")     # Emergency (0) 
self.input_handler.execute_skill_high("q")    # High (1)
self.input_handler.execute_skill_normal("2")  # Normal (2)
self.input_handler.execute_utility("tab")     # Low (3)

# 发送序列（支持delay指令与去重）
self.input_handler.send_key("delay50,q,delay100,w")

# 批量更新配置 (2025.10新增)
self.input_handler.command_sender.batch_update_config({
    "emergency_hp_key": "c",
    "emergency_mp_key": "2",
    "special_keys_config": ["space"],
    "managed_keys_config": {"e": {"target": "shift", "delay": 50}}
})
```

## 🚑 索急按键系统 API (2025.10.17新增)

### 概述

絒急按键系统提供了高可靠的生存技能支持，确保 HP/MP 等关键操作在任何情况下都能优先执行。基于 **智能缓存、序列去重、批量配置更新** 三大核心特性实现。

### 紂急按键 API

```python
class AHKInputHandler:
    def execute_hp_potion(self, key: str):
        """执行HP药剂，自动添加到Emergency队列(0)"""
        return self.command_sender.send_emergency(key)
    
    def execute_mp_potion(self, key: str):
        """执行MP药剂，自动添加到Emergency队列(0)"""
        return self.command_sender.send_emergency(key)
    
    def send_emergency_sequence(self, sequence: str):
        """发送索急序列，包含去重逻辑"""
        return self.command_sender.send_sequence(sequence, priority=0)
        
    def update_emergency_keys_cache(self, hp_key: str, mp_key: str):
        """更新索急按键缓存，用于 AHK 端快速识别"""
        return self.command_sender.batch_update_config({
            "emergency_hp_key": hp_key,
            "emergency_mp_key": mp_key
        })
```

### 批量配置更新 API

```python
class AHKCommandSender:
    def batch_update_config(self, config_updates: dict) -> bool:
        """
        批量更新 AHK 配置，减少通信开销
        
        Args:
            config_updates: 配置更新字典
                - emergency_hp_key: HP 按键
                - emergency_mp_key: MP 按键  
                - special_keys_config: 特殊按键列表
                - managed_keys_config: 管理按键配置
                - 等等...
        
        Returns:
            bool: 是否成功更新
        """
        data = json.dumps(config_updates)
        return self.send_ahk_cmd(CMD_BATCH_UPDATE_CONFIG, data)
        
    def send_emergency(self, key: str) -> bool:
        """发送索急按键到 Emergency 队列 (0)"""
        return self.send_ahk_cmd(CMD_ENQUEUE, f"0|press:{key}")
```

### 序列去重 API

```python
class MacroEngine:
    def execute_skill_with_deduplication(self, skill_config: dict):
        """
        执行技能并自动去重
        如果同一序列正在执行，则跳过本次执行
        """
        sequence = skill_config.get('key', '')
        alt_sequence = skill_config.get('alt_key', '')
        
        # 优先使用 alt_key 序列
        target_sequence = alt_sequence if alt_sequence else sequence
        
        if target_sequence:
            # AHK 端会自动进行去重检查
            priority = 1 if skill_config.get('priority', False) else 2
            return self.input_handler.send_key(target_sequence, priority)
        
        return False
```

### 使用示例

#### 1. 初始化索急按键缓存

```python
# 在配置加载时自动更新缓存
def load_config(self, config_file: str):
    config = self.config_manager.load_config(config_file)
    
    # 提取索急按键配置
    hp_config = config.get('global', {}).get('resource_management', {}).get('hp_config', {})
    mp_config = config.get('global', {}).get('resource_management', {}).get('mp_config', {})
    
    hp_key = hp_config.get('key', 'c')  # 默认为 'c' (新版本)
    mp_key = mp_config.get('key', '2')
    
    # 批量更新配置
    self.input_handler.command_sender.batch_update_config({
        "emergency_hp_key": hp_key,
        "emergency_mp_key": mp_key,
        "hp_threshold": hp_config.get('threshold', 60),  # 新默认阈值 60%
        "mp_threshold": mp_config.get('threshold', 40)
    })
```

#### 2. 索急情况下的药剂使用

```python
# 在资源管理器中使用
def check_and_use_hp_potion(self):
    if self.current_hp_percentage < self.hp_threshold:
        # 使用新的索急 API，确保优先执行
        self.input_handler.execute_hp_potion(self.hp_key)  # 默认为 'c'
        
        # 更新冷却时间
        self.last_hp_use_time = time.time()
        
        # 可选：发送复合序列
        # self.input_handler.send_emergency_sequence("delay30,c")
```

#### 3. 游戏配置切换

```python
# 为不同游戏切换配置
def switch_to_torchlight_infinite(self):
    """Torchlight Infinite 专用配置"""
    self.input_handler.command_sender.batch_update_config({
        # 絒急按键新配置
        "emergency_hp_key": "c",      # HP按键从 '1' 改为 'c'
        "emergency_mp_key": "2",
        
        # 窗口激活配置
        "window_class": "UnrealWindow",
        "window_exe": "torchlight_infinite.exe",
        
        # HP 检测区域新坐标
        "hp_region": [747, 978, 753, 1049],  # 新的检测区域
        "hp_threshold": 60,                  # 阈值从 50% 提升到 60%
        
        # 技能按键映射
        "skill_mappings": {
            "Skill1": {"key": "LButton", "coords": [815, 1000]},
            "Skill3": {"key": "q", "coords": [925, 1000]},
            "Skill4": {"key": "e", "coords": [980, 1000]}
        }
    })

def switch_to_poe2(self):
    """Path of Exile 2 专用配置"""
    self.input_handler.command_sender.batch_update_config({
        "emergency_hp_key": "1",      # 保持原有配置
        "emergency_mp_key": "2",
        "window_class": "POEWindowClass",
        "window_exe": "PathOfExile_x64.exe",
        "hp_region": [113, 896, 124, 1051],
        "hp_threshold": 50
    })
```

### 性能优化特性

1. **智能缓存**: AHK 端直接缓存絒急按键，无需重复解析配置
2. **序列去重**: 防止快速重复按键导致的性能问题和误触发
3. **批量更新**: 一次通信更新多个配置项，减少 WM_COPYDATA 开销
4. **絒急优先**: Emergency 队列在任何情况下都不被暂停，确保生存技能可靠执行

---

## 🎯 技能自动化系统

### 概述

技能系统是 pyahk 的核心功能，提供自动化的技能释放、冷却检测和优先级管理。支持多种触发模式和执行---

## 💊 智能药剂系统 (v2025.01 全面升级)

### 概述

智能药剂系统提供自动化的 HP/MP 检测和药剂使用功能。**v2025.01版本引入ResourceConfigManager中央化配置管理和ColorAnalysisTools可视化工具**，大幅简化配置流程和提升用户体验。支持**三大类检测方式**，每类都有其独特的优势和适用场景。

### 三大类检测方式总览 (实测478次基准数据)

|| 类别         | 子类型     | 技术         | 性能   | 准确率  | 成功率 | 速度优势 | 适用场景                   |
|| ------------ | ---------- | ------------ | ------ | ------- | ------ | -------- | -------------------------- |
|| **矩形检测** ⭐ | -          | HSV模板匹配 | **0.3ms** | **96%误差<5%** | **100%** | **基准** | **推荐**，最快最准，条形资源条 |
|| **圆形检测** | -          | HSV 颜色匹配 | ~5ms   | 95%+    | 99%+ | 慢17倍 | 球形/圆形资源条            |
|| **Text OCR** | 模板匹配   | 模板匹配     | ~25ms   | 90-95%  | 98.3% | 慢82倍 | 数字显示，无额外依赖 |
|| **Text OCR** | Keras 模型 | 深度学习     | ~99ms  | >99%    | 98.5% | 慢330倍 | 最高准确率                 |
|| **Text OCR** | Tesseract  | OCR 引擎     | ~241ms | 95-100% | 99.0% | 慢803倍 | 通用性强，无需训练         |

**性能对比**：矩形检测比最快的OCR快82倍，比最慢的OCR快803倍！所有数据来自478次真实测试。

### v2025.01 架构升级亮点

#### ResourceConfigManager 中央化管理
```python
class ResourceConfigManager:
    """统一的资源配置管理器 - v2025.01新增"""
    
    def update_widget_from_config(self, widget, config):
        """统一的从配置更新Widget，消除重复代码"""
        
    def generate_config_from_widget(self, widget):
        """统一的从Widget生成配置，支持所有检测模式"""
        
    def parse_colors_from_config(self, config):
        """统一的颜色解析，支持多种格式"""
```

#### ColorAnalysisTools 可视化配置
```python
class ColorAnalysisTools:
    """颜色分析工具 - 提供直观的配置体验"""
    
    def start_region_selection(self, callback, detection_mode="rectangle"):
        """启动区域选择模式，支持矩形和圆形"""
        
    def start_color_picking_for_region(self, region, callback):
        """对指定区域进行颜色采样和HSV优化"""
        
    def optimize_hsv_parameters(self, region, sample_colors):
        """基于颜色样本优化HSV容差参数"""
        
    def preview_detection_result(self, widget_config):
        """实时预览检测效果"""
```

### 一、矩形检测模式 ⭐ 推荐 (v2025.01算法优化)

#### 工作原理

基于 **HSV模板匹配** 技术，通过以下流程实现高精度检测：

1. **智能模板捕获**：
   - 传统方式：F8键在满血满蓝状态下自动截取
   - **新增方式**：ColorAnalysisTools可视化选择区域和采样颜色
2. **实时对比**：运行时将当前HP/MP条与模板进行逐像素HSV容差匹配
3. **算法优化**：从"最长连续段"算法优化为"总填充行数"算法，准确率提升至96%
4. **百分比计算**：统计总填充行数，计算资源百分比

**核心优势**：
- **自适应**：每个用户在自己的游戏环境中建立模板，自动适应不同光照和分辨率
- **鲁棒性强**：对中间遮挡（debuff图标、伤害数字）有很强的抵抗力
- **性能卓越**：0.3ms检测速度，比OCR快82-803倍
- **算法升级**：v2025.01优化算法，准确率从90%+提升到96%+
- **可视化配置**：ColorAnalysisTools提供直观的区域选择和参数调优界面

#### v2025.01 API 升级

```python
# 传统API（仍然支持）
resource_manager.initialize_templates_from_capture()

# 新增可视化API
color_tools = ColorAnalysisTools()

# 1. 区域选择
def on_region_selected(region):
    # 自动更新配置
    self.resource_config_manager.update_region_config(widget, region)
    # 启动颜色分析
    color_tools.start_color_picking_for_region(region, on_colors_analyzed)

color_tools.start_region_selection(on_region_selected, "rectangle")

# 2. 颜色分析和HSV优化
def on_colors_analyzed(hsv_params):
    # 自动应用最优HSV参数
    self.resource_config_manager.apply_hsv_params(widget, hsv_params)
    # 预览检测效果
    color_tools.preview_detection_result(widget.get_config())

# 3. 配置生成
optimized_config = self.resource_config_manager.generate_config_from_widget(hp_widget)
```

---

## 🎨 ColorAnalysisTools API 详解 (v2025.01 新增)

### 概述

ColorAnalysisTools 是 v2025.01 版本引入的可视化颜色分析工具，旨在简化 HP/MP 检测的配置过程，提供直观的用户体验。通过可视化的区域选择、颜色采样和实时预览，用户可以轻松完成复杂的HSV参数调优。

### 核心 API

#### 1. 区域选择 API

```python
def start_region_selection(self, callback, detection_mode="rectangle", 
                          title="选择检测区域"):
    """
    启动可视化区域选择模式
    
    Args:
        callback: 选择完成的回调函数，接收 (x1, y1, x2, y2) 参数
        detection_mode: 检测模式，"rectangle" 或 "circle"
        title: 选择窗口的标题
    
    Returns:
        bool: 是否成功启动选择模式
    """
    
def start_circle_selection(self, callback, title="选择圆形区域"):
    """
    启动圆形区域选择模式
    
    Args:
        callback: 选择完成的回调函数，接收 (center_x, center_y, radius) 参数
        title: 选择窗口的标题
    
    Returns:
        bool: 是否成功启动选择模式
    """
```

#### 2. 颜色分析 API

```python
def start_color_picking_for_region(self, region, callback, 
                                  sample_method="auto"):
    """
    对指定区域进行颜色分析和采样
    
    Args:
        region: 区域坐标 (x1, y1, x2, y2) 或 (center_x, center_y, radius)
        callback: 分析完成的回调函数，接收 hsv_params 参数
        sample_method: 采样方法，"auto", "grid", "edge", "manual"
    
    Returns:
        bool: 是否成功启动颜色分析
    """
    
def analyze_region_colors(self, region, target_colors=None, 
                         detection_mode="rectangle"):
    """
    分析区域颜色分布和推荐HSV参数
    
    Args:
        region: 区域坐标
        target_colors: 目标颜色列表，用于匹配和优化
        detection_mode: 检测模式
    
    Returns:
        dict: 包含 HSV 参数的分析结果
            {
                'hsv_params': {
                    'target_h': int, 'target_s': int, 'target_v': int,
                    'tolerance_h': int, 'tolerance_s': int, 'tolerance_v': int
                },
                'color_distribution': {...},
                'confidence_score': float
            }
    """
```

#### 3. HSV 参数优化 API

```python
def optimize_hsv_parameters(self, region, sample_colors, 
                           optimization_method="balanced"):
    """
    基于颜色样本优化HSV容差参数
    
    Args:
        region: 检测区域
        sample_colors: 颜色样本列表 [(h, s, v), ...]
        optimization_method: 优化方法
            - "strict": 严格模式，容差值较小，准确率高
            - "balanced": 平衡模式，容差与准确率平衡
            - "tolerant": 宽松模式，容差值较大，适应性强
    
    Returns:
        dict: 优化后的 HSV 参数
    """
    
def calculate_optimal_tolerance(self, sample_colors, target_accuracy=0.95):
    """
    计算最优容差参数
    
    Args:
        sample_colors: 颜色样本列表
        target_accuracy: 目标准确率 (0.0-1.0)
    
    Returns:
        dict: 包含容差参数和预期准确率
    """
```

#### 4. 实时预览 API

```python
def preview_detection_result(self, widget_config, live_update=True):
    """
    实时预览检测效果
    
    Args:
        widget_config: Widget 配置字典
        live_update: 是否实时更新
    
    Returns:
        dict: 预览结果
            {
                'detection_percentage': float,
                'match_confidence': float,
                'preview_image': np.ndarray,
                'status': str
            }
    """
    
def start_live_preview(self, widget_config, update_callback):
    """
    启动实时预览模式
    
    Args:
        widget_config: Widget 配置
        update_callback: 更新回调函数，接收预览结果
    
    Returns:
        bool: 是否成功启动
    """
    
def stop_live_preview(self):
    """停止实时预览"""
```

### 使用示例

#### 完整的配置流程

```python
class HPConfigurationFlow:
    def __init__(self):
        self.color_tools = ColorAnalysisTools()
        self.config_manager = ResourceConfigManager()
        self.hp_widget = None  # HP Widget 实例
    
    def start_hp_configuration(self):
        """启动HP检测的完整配置流程"""
        # 第一步：选择检测区域
        self.color_tools.start_region_selection(
            callback=self.on_region_selected,
            detection_mode="rectangle",
            title="选择 HP 检测区域"
        )
    
    def on_region_selected(self, x1, y1, x2, y2):
        """区域选择完成回调"""
        print(f"HP区域已选择: ({x1}, {y1}) 到 ({x2}, {y2})")
        
        # 更新配置
        self.config_manager.update_region_config(
            self.hp_widget, 
            {"region_x1": x1, "region_y1": y1, "region_x2": x2, "region_y2": y2}
        )
        
        # 第二步：分析颜色和优化HSV参数
        self.color_tools.start_color_picking_for_region(
            region=(x1, y1, x2, y2),
            callback=self.on_colors_analyzed,
            sample_method="auto"
        )
    
    def on_colors_analyzed(self, hsv_params):
        """颜色分析完成回调"""
        print(f"HSV参数优化完成: {hsv_params}")
        
        # 应用HSV参数
        self.config_manager.apply_hsv_params(self.hp_widget, hsv_params)
        
        # 第三步：实时预览检测效果
        self.start_live_preview()
    
    def start_live_preview(self):
        """启动实时预览"""
        widget_config = self.config_manager.generate_config_from_widget(self.hp_widget)
        
        self.color_tools.start_live_preview(
            widget_config=widget_config,
            update_callback=self.on_preview_update
        )
    
    def on_preview_update(self, preview_result):
        """预览更新回调"""
        percentage = preview_result['detection_percentage']
        confidence = preview_result['match_confidence']
        
        print(f"检测结果: {percentage:.1f}%, 置信度: {confidence:.2f}")
        
        # 更新UI显示
        self.update_preview_display(preview_result)
    
    def finalize_configuration(self):
        """完成配置设置"""
        # 停止预览
        self.color_tools.stop_live_preview()
        
        # 生成最终配置
        final_config = self.config_manager.generate_config_from_widget(self.hp_widget)
        
        # 保存配置
        self.save_config(final_config)
        
        print("配置已完成并保存")
```

#### 配置方法

```json
{
  "resource_management": {
    "hp_config": {
      "enabled": true,
      "key": "1",
      "threshold": 50,
      "cooldown": 1000,
      "detection_mode": "rectangle",
      "region_x1": 113,
      "region_y1": 896,
      "region_x2": 124,
      "region_y2": 1051,
      "tolerance_h": 10,
      "tolerance_s": 30,
      "tolerance_v": 50
    }
  }
}
```

**配置参数说明**：
- `region_x1/y1/x2/y2`: HP/MP条的矩形区域坐标
- `tolerance_h`: H值（色调）容差，范围0-179，默认10
- `tolerance_s`: S值（饱和度）容差，范围0-255，默认30
- `tolerance_v`: V值（明度）容差，范围0-255，默认50
- `threshold`: 触发药剂的百分比阈值

#### 使用流程

1. **配置区域**：在配置文件中设置HP/MP条的矩形坐标
2. **启动程序**：运行程序并进入游戏
3. **捕获模板**：在满血满蓝状态下按 **F8** 键
4. **开始检测**：按F9启动，程序自动检测并在低于阈值时使用药剂

#### 性能数据（实测 - 478次测试）

| 指标 | HP检测 | MP检测 | 综合 |
|------|--------|--------|------|
| 检测速度 | 0.3ms | 0.3ms | 0.3ms |
| 成功率 | 100% | 100% | 100% |
| 平均误差 | 3.5% | 2.9% | 3.2% |
| 误差<5%比例 | 95.0% | 97.0% | 96.0% |
| 误差<10%比例 | 98.0% | 99.0% | 98.5% |
| 最大误差 | 15.2% | 14.8% | 15.2% |
| 抗干扰能力 | 优秀（对遮挡鲁棒） | 优秀 | 优秀 |

#### 优缺点

- ✅ **性能最佳**：0.3ms，比OCR快82-803倍（实测478次）
- ✅ **准确率高**：96%的案例误差<5%，98.5%误差<10%
- ✅ **成功率100%**：无识别失败情况，OCR有8个错误案例
- ✅ **自适应**：按F8自动适应不同环境、光照和分辨率
- ✅ **鲁棒性强**：对UI遮挡、debuff图标、伤害数字有很强抵抗力
- ✅ **配置简单**：只需设置区域坐标和HSV容差
- ✅ **可靠性高**：在OCR识别错误时仍能给出合理值
- ⚠️ 需要按F8初始化模板（满血满蓝状态）
- ⚠️ 分辨率或UI缩放变化后需重新按F8

### 二、圆形检测模式

#### 工作原理

基于 HSV 颜色空间匹配，检测圆形区域内的资源条颜色变化。适用于球形或圆形的 HP/MP 显示。

#### 配置方法

```json
{
  "resource_management": {
    "hp_config": {
      "enabled": true,
      "key": "1",
      "threshold": 50,
      "detection_mode": "circle",
      "center_x": 200,
      "center_y": 920,
      "radius": 50
    }
  }
}
```

#### 优缺点

- ✅ 性能极佳（~5ms）
- ✅ 更精确的圆形区域检测
- ✅ 适合球形资源条
- ❌ 依赖颜色，光照变化可能影响准确率

### 三、Text OCR 模式

#### 工作原理

直接识别游戏中显示的 HP/MP 数字文本（如"540/540"），无需依赖颜色变化。支持三种 OCR 引擎，满足不同需求。

**注意**：虽然OCR准确率高，但性能比矩形检测慢82-803倍，且存在识别失败的情况。建议优先使用矩形检测。

#### 三种 OCR 引擎对比（实测478次）

| 引擎           | 速度   | 成功率  | 准确率  | 依赖       | 需要训练 | 推荐场景                       |
| -------------- | ------ | ------- | ------- | ---------- | -------- | ------------------------------ |
| **模板匹配**   | ~25ms  | 98.3%   | 90-95%  | 无         | ✅ 是    | 最快的OCR，无额外依赖 |
| **Keras 模型** | ~99ms  | 98.5%   | >99%    | TensorFlow | ✅ 是    | 追求最高准确率                 |
| **Tesseract**  | ~241ms | 99.0%   | 95-100% | Tesseract  | ❌ 否    | 无需训练，通用性强             |

#### 配置方法

```json
{
  "resource_management": {
    "hp_config": {
      "enabled": true,
      "key": "1",
      "threshold": 50,
      "detection_mode": "text_ocr",
      "ocr_engine": "template",
      "text_x1": 97,
      "text_y1": 814,
      "text_x2": 218,
      "text_y2": 835
    }
  }
}
```

#### GUI 配置步骤

1. **选择检测模式**: 在"检测模式"下拉框选择"数字匹配 (Text OCR)"
2. **选择 OCR 引擎**: 在"OCR 引擎"下拉框选择：
   - **模板匹配（推荐）** - 默认选项，最快速度
   - **Keras 模型** - 最高准确率
   - **Tesseract** - 无需训练
3. **设置区域**:
   - 手动输入：在坐标框输入 `x1,y1,x2,y2`（如 `97,814,218,835`）
   - 或使用"选择区域"按钮框选游戏中的 HP/MP 数字区域
4. **测试识别**: 点击"🧪 测试识别"按钮
   - 选择游戏截图文件
   - 系统会自动识别并显示结果
   - 验证配置是否正确

#### 🧪 测试功能

Text OCR 模式提供专门的测试按钮，用于验证配置是否正确：

**使用方法**:

1. 在"检测模式"下拉框选择"数字匹配 (Text OCR)"
2. 点击"🧪 测试识别"按钮
3. 在文件对话框中选择游戏截图（.png/.jpg 格式）
4. 系统将显示识别结果：
   - ✅ 识别成功：显示识别的数字和百分比
   - ❌ 识别失败：提供故障排查建议

**注意事项**:

- 测试时使用静态截图，实际运行时使用 capture 库的实时画面
- 确保测试图片的分辨率与实际游戏一致
- 如果测试成功但实际运行失败，可能是游戏分辨率或 UI 缩放改变

#### 引擎 1：模板匹配（推荐）

**技术特性**:

- **无额外依赖**: 不需要安装 TensorFlow 或 Tesseract
- **极致性能**: 单个字符识别~1.3ms，完整 HP/MP 识别~7ms
- **高准确率**: 90-95%，满足大多数游戏场景
- **自动生成**: 通过训练流程自动生成最佳模板

**使用前提**:

- 需要运行一次训练流程生成模板文件
- 模板文件位于 `game_char_templates/` 目录

**配置**:

```json
{
  "ocr_engine": "template"
}
```

#### 引擎 2：Keras 模型

**技术特性**:

- **最高准确率**: >99%，适合对精度要求极高的场景
- **深度学习**: 基于 CNN 神经网络，识别能力强
- **批量优化**: 支持批量识别，5 个字符一次处理

**使用前提**:

- 需要安装 TensorFlow: `pip install tensorflow`
- 需要运行训练流程生成模型文件
- 模型文件位于 `deepai/models/digit_cnn.keras`

**配置**:

```json
{
  "ocr_engine": "keras"
}
```

#### 引擎 3：Tesseract

**技术特性**:

- **无需训练**: 开箱即用，不需要训练流程
- **通用性强**: 可识别各种字体和样式
- **稳定可靠**: 成熟的开源 OCR 引擎

**使用前提**:

- 需要安装 Tesseract OCR
- 配置 Tesseract 路径

**配置**:

```json
{
  "ocr_engine": "tesseract"
},
"global": {
  "tesseract_ocr": {
    "tesseract_cmd": "D:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    "lang": "eng",
    "psm_mode": 7,
    "char_whitelist": "0123456789/"
  }
}
```

#### 故障排查

如果识别失败：

1. **检查引擎选择**: 确认选择的引擎已正确安装和配置
2. **验证模板/模型**: 模板匹配和 Keras 需要先运行训练流程
3. **使用测试按钮**: 在界面点击"🧪 测试识别"验证
4. **检查坐标**: 确保文本区域坐标准确框选了数字
5. **查看详细文档**: `WIKI/08-故障排查手册.md`

### 检测模式选择建议

| 场景             | 推荐模式             | 理由                 |
| ---------------- | -------------------- | -------------------- |
| **条形资源条** ⭐ | **矩形检测**         | **0.3ms，100%成功率，96%误差<5%** |
| 球形资源条       | 圆形检测             | 5ms，更精确的圆形区域检测 |
| 数字显示的 HP/MP | Text OCR (模板匹配)  | 25ms，98.3%成功率，无额外依赖 |
| 追求极致准确率   | Text OCR (Keras)     | 99ms，>99%准确率，但慢330倍 |
| 无法训练模板     | Text OCR (Tesseract) | 241ms，无需训练，但慢803倍 |

**性能建议**：除非游戏UI只显示数字文本，否则强烈推荐使用矩形检测或圆形检测，性能提升巨大。

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
  - 资源优化：优先级按键激活时完全停止 CPU 密集型操作，降低 70-90% CPU 占用。

### 三种触发模式

#### 1. 定时模式 (TriggerMode = 0)

```json
{
  "name": "持续技能",
  "key": "1",
  "trigger_mode": 0,
  "interval": 1000, // 每1秒执行一次
  "priority": "normal"
}
```

- **功能**：基于固定时间间隔执行技能。
- **使用场景**：持续 BUFF、光环类技能。

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
- **按键序列支持**: `key`字段现在支持逗号分隔的序列，如 `delay50,q` 表示"延迟 50 毫秒后按 q"。这对于需要"技能前摇保护"或精确时序的连招非常有用。
- **检测原理**：采用统一的**HSV 模板匹配**算法，能有效抵抗游戏内光照和技能图标动画效果的干扰。

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

#### Buff 限制模式 (ExecuteCondition = 1)

- **逻辑**：Buff 存在时不执行，Buff 消失时执行。
- **场景**：避免重复上 BUFF。

#### 资源条件模式 (ExecuteCondition = 2)

- **逻辑**：资源充足执行主按键，不足执行备用按键。
- **场景**：根据 MP/怒气状态切换技能。

---

## 🎮 多级优先级与序列系统（核心调度引擎）

### 系统概述

这是技能自动化的“输入调度大脑”，提供精细化的按键优先级管理和智能的执行控制。通过**四级优先队列**、**按键序列**和**前置延迟**等机制，实现游戏操作的精确控制和“前后摇”冲突的智能避免。

### 四级优先队列架构

pyahk 采用多级优先队列系统，确保最关键的操作能被最先执行。

| 优先级    | 名称 | 数值     | 典型用例                               | 执行策略               |
| --------- | ---- | -------- | -------------------------------------- | ---------------------- |
| emergency | 紧急 | 0 (最高) | HP/MP 药剂、优先级按键的**前置延迟**   | 永不被拦截，立即执行   |
| high      | 高   | 1        | 优先级按键本身（如闪避）、核心输出技能 | 仅在非优先级模式下执行 |
| normal    | 普通 | 2        | 常规技能、Buff                         | 被优先级按键状态暂停   |
| low       | 低   | 3        | 辅助 / UI / 拾取                       | 被优先级按键状态暂停   |

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

通过这个短暂的、高优先级的延迟，确保了游戏有足够的时间进入“玩家正在手动操作”的状态，从而 100%响应后续的闪避按键。

**配置方法**:
此延迟可在 UI 的“优先级按键”标签页中配置，或直接在`json`中设置。

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

- `delay50,q`: 延迟 50ms 后按 q。用于等待技能前摇结束。
- `q,delay100,w`: 按 q，等待 100ms 后按 w。用于精确控制连招时序。

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
2. **前置延迟**: 向 emergency 队列推入延迟指令确保游戏状态同步
3. **优先执行**: 向 high 队列推入按键指令保证最高优先级
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

- 模块基类：实现 start/stop/\_process_iteration，订阅事件即可接入
- 输入处理器：can_process/process_action 增强输入语义
- 条件检查器：check_condition(frame, skill_config) 拓展执行条件

更多高级示例（插件、AI 决策、游戏 Profile 工厂）可参考原 09 文档内容，现已折叠至本章附录或源代码注释。
