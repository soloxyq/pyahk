# 09 API参考与扩展

## 概述

本章提供pyahk的完整API参考文档，包括核心接口、扩展点、插件开发指南和二次开发示例。为高级开发者提供深度定制和功能扩展的技术指导。

---

## 🔌 核心API接口

### EventBus API

#### 基础事件操作
```python
from torchlight_assistant.core.event_bus import event_bus

# 订阅事件
def my_event_handler(data):
    print(f"接收到事件数据: {data}")

event_bus.subscribe("custom:event", my_event_handler)

# 发布事件
event_bus.publish("custom:event", data={"message": "Hello World"})

# 取消订阅
event_bus.unsubscribe("custom:event", my_event_handler)
```

#### 内置事件类型
```python
# 系统状态事件
"engine:state_changed"          # 状态变化: (old_state, new_state)
"engine:config_updated"         # 配置更新: (config_dict)
"engine:shutdown"               # 系统关闭: ()

# UI交互事件  
"ui:load_config_requested"      # 加载配置请求: (config_file)
"ui:save_full_config_requested" # 保存配置请求: (config_dict, config_file)
"ui:sync_and_toggle_state_requested" # 状态切换请求: ()

# 热键事件
"hotkey:f8_press"              # F8按键: ()
"hotkey:f7_press"              # F7按键: ()
"hotkey:f9_press"              # F9按键: ()
"hotkey:z_press"               # Z按键: ()

# 功能模块事件
"skill:executed"               # 技能执行: (skill_name, success)
"resource:potion_used"         # 药剂使用: (potion_type, current_percentage)
"pathfinding:target_found"     # 寻路目标发现: (target_pos)
"reroll:affix_matched"         # 词缀匹配: (matched_affixes)
```

### UnifiedScheduler API

#### 任务管理
```python
from torchlight_assistant.core.unified_scheduler import UnifiedScheduler

scheduler = UnifiedScheduler()

# 添加定时任务
def my_periodic_task():
    print("定期执行的任务")

success = scheduler.add_task(
    task_id="my_task",
    interval=1.0,              # 1秒间隔
    callback=my_periodic_task,
    args=(),                   # 回调参数
    kwargs={},                 # 回调关键字参数
    start_immediately=False    # 是否立即开始
)

# 移除任务
scheduler.remove_task("my_task")

# 更新任务间隔
scheduler.update_task("my_task", new_interval=2.0)

# 启动调度器
scheduler.start()

# 停止调度器
scheduler.stop()

# 获取任务状态
status = scheduler.get_task_status("my_task")
print(f"任务状态: {status}")
```

#### 高级调度功能
```python
# 条件任务 - 仅在满足条件时执行
def conditional_task():
    if some_condition():
        actual_work()

scheduler.add_task(
    task_id="conditional_task",
    interval=0.5,
    callback=conditional_task
)

# 一次性任务 - 延迟执行
def delayed_task():
    print("延迟执行的任务")

scheduler.add_one_time_task(
    task_id="delayed_task",
    delay=5.0,  # 5秒后执行
    callback=delayed_task
)
```

### InputHandler API

#### 基础输入操作
```python
from torchlight_assistant.core.input_handler import InputHandler

input_handler = InputHandler()

# 单个按键
input_handler.send_key("1")

# 组合键
input_handler.send_key("ctrl+c")

# 鼠标操作
input_handler.click_mouse("left")   # 左键点击
input_handler.click_mouse("right")  # 右键点击
```

#### 🚀 技能执行API - 支持序列
```python
# 普通技能（支持序列）
input_handler.execute_skill_normal("q")           # 单个按键
input_handler.execute_skill_normal("delay50,q")   # 延迟后按键
input_handler.execute_skill_normal("q,delay100,w") # 复杂序列

# 高优先级技能（支持序列）
input_handler.execute_skill_high("delay50,escape")
input_handler.execute_skill_high("1,delay200,2,delay100,3")

# 辅助功能（支持序列）
input_handler.execute_utility("delay100,tab")
input_handler.execute_utility("i,delay50,escape")

# 紧急操作（HP/MP药剂）
input_handler.execute_hp_potion("1")    # 单个按键，高优先级
input_handler.execute_mp_potion("2")    # 单个按键，高优先级
```

#### 序列语法支持
```python
# 延迟指令
"delay50"              # 延迟50毫秒
"delay100"             # 延迟100毫秒
"delay1000"            # 延迟1秒

# 单个按键
"q"                    # 普通按键
"shift+q"              # 组合按键
"ctrl+alt+tab"         # 多键组合

# 按键序列
"delay50,q"            # 延迟50ms后按q
"q,delay100,w"         # 按q，等待100ms，按w
"delay50,1,delay200,2" # 多技能时机控制
"shift+1,delay100,2"   # 组合键+延迟+普通键
```

#### 队列管理API
```python
# 队列状态查询
queue_length = input_handler.get_queue_length()
queue_stats = input_handler.get_queue_stats()

# 清空队列
input_handler.clear_queue()

# 队列优先级
# emergency > high > normal > low
```

#### 优先级按键控制
```python
# 检查优先级模式状态
is_active = input_handler.is_priority_mode_active()

# 获取当前活跃的优先级按键
active_keys = input_handler.get_active_priority_keys()

# 手动设置优先级模式（用于测试）
input_handler.set_priority_mode_override(True)
```

### BorderFrameManager API

#### 图像捕获与缓存
```python
from torchlight_assistant.utils.border_frame_manager import BorderFrameManager

border_manager = BorderFrameManager()

# 获取当前帧
frame = border_manager.get_current_frame()
if frame is not None:
    height, width, channels = frame.shape
    print(f"帧尺寸: {width}x{height}")

# 获取指定区域
region_frame = border_manager.get_region_frame(x=100, y=50, w=200, h=100)

# 模板匹配
similarity = border_manager.compare_template(
    frame=frame,
    template_coords=[100, 50, 20, 20],
    target_color=[255, 0, 0],  # RGB
    tolerance=30
)

# HSV检测
hsv_match = border_manager.compare_hsv_template(
    frame=frame, 
    region_coords=[100, 50, 200, 20],
    template_hsv=[180, 255, 255],
    tolerance=[10, 30, 30]
)
```

#### 缓存管理
```python
# 设置模板缓存
border_manager.set_template_cache("hp_bar", template_data)

# 获取缓存模板
template = border_manager.get_template_cache("hp_bar")

# 清理缓存
border_manager.clear_cache()

# 缓存统计
cache_info = border_manager.get_cache_info()
print(f"缓存项数: {cache_info['count']}")
print(f"缓存大小: {cache_info['size_mb']:.2f}MB")
```

---

## 🔧 扩展开发指南

### 自定义功能模块

#### 模块基类
```python
from torchlight_assistant.core.event_bus import event_bus
from torchlight_assistant.core.states import MacroState
import threading

class CustomModule:
    """自定义功能模块基类"""
    
    def __init__(self, name: str):
        self.name = name
        self.enabled = False
        self.state = MacroState.STOPPED
        self._thread = None
        self._stop_event = threading.Event()
        
        # 订阅核心事件
        self._setup_event_subscriptions()
    
    def _setup_event_subscriptions(self):
        """设置事件订阅"""
        event_bus.subscribe("engine:state_changed", self._on_state_changed)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)
        event_bus.subscribe("engine:shutdown", self._on_shutdown)
    
    def _on_state_changed(self, old_state, new_state):
        """状态变化处理"""
        self.state = new_state
        
        if new_state == MacroState.RUNNING:
            self.start()
        elif new_state in [MacroState.STOPPED, MacroState.PAUSED]:
            self.stop()
    
    def _on_config_updated(self, config):
        """配置更新处理"""
        module_config = config.get(self.name, {})
        self.enabled = module_config.get("enabled", False)
        self._update_config(module_config)
    
    def _update_config(self, config):
        """更新模块配置 - 子类实现"""
        pass
    
    def _on_shutdown(self):
        """系统关闭处理"""
        self.stop()
        self._cleanup()
    
    def start(self):
        """启动模块"""
        if not self.enabled or self._thread is not None:
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        event_bus.publish(f"{self.name}:started")
    
    def stop(self):
        """停止模块"""
        if self._thread is None:
            return
        
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        
        event_bus.publish(f"{self.name}:stopped")
    
    def _run_loop(self):
        """主循环 - 子类实现"""
        while not self._stop_event.is_set():
            try:
                self._process_iteration()
                self._stop_event.wait(0.1)  # 100ms间隔
            except Exception as e:
                print(f"{self.name} 模块错误: {e}")
                break
    
    def _process_iteration(self):
        """处理一次迭代 - 子类实现"""
        pass
    
    def _cleanup(self):
        """清理资源 - 子类实现"""
        pass
```

#### 示例：自定义血量监控模块
```python
class HealthMonitor(CustomModule):
    """血量监控模块示例"""
    
    def __init__(self):
        super().__init__("health_monitor")
        self.hp_threshold = 30
        self.emergency_key = "5"
        self.check_interval = 0.5
        self.border_manager = None
        self.input_handler = None
    
    def _update_config(self, config):
        """更新配置"""
        self.hp_threshold = config.get("hp_threshold", 30)
        self.emergency_key = config.get("emergency_key", "5")
        self.check_interval = config.get("check_interval", 0.5)
    
    def set_dependencies(self, border_manager, input_handler):
        """设置依赖"""
        self.border_manager = border_manager
        self.input_handler = input_handler
    
    def _process_iteration(self):
        """处理一次血量检查"""
        if not self.border_manager or not self.input_handler:
            return
        
        # 获取当前帧
        frame = self.border_manager.get_current_frame()
        if frame is None:
            return
        
        # 检测血量百分比
        hp_percentage = self._detect_hp_percentage(frame)
        if hp_percentage is None:
            return
        
        # 发布血量事件
        event_bus.publish("health_monitor:hp_detected", percentage=hp_percentage)
        
        # 血量过低时使用紧急药剂
        if hp_percentage < self.hp_threshold:
            self._use_emergency_potion()
    
    def _detect_hp_percentage(self, frame):
        """检测血量百分比"""
        # 实现血量检测逻辑
        # 这里是示例实现
        hp_region = [136, 910, 213, 1004]  # HP条区域
        return self.border_manager.detect_resource_percentage(frame, hp_region)
    
    def _use_emergency_potion(self):
        """使用紧急药剂"""
        self.input_handler.enqueue_action({
            "type": "key_press",
            "key": self.emergency_key,
            "priority": True  # 高优先级紧急处理
        })
        
        event_bus.publish("health_monitor:emergency_potion_used")

# 模块注册和使用
def register_custom_module(macro_engine):
    """注册自定义模块"""
    health_monitor = HealthMonitor()
    health_monitor.set_dependencies(
        macro_engine.border_manager,
        macro_engine.input_handler
    )
    
    # 将模块添加到引擎中
    macro_engine.custom_modules = getattr(macro_engine, 'custom_modules', {})
    macro_engine.custom_modules['health_monitor'] = health_monitor
    
    return health_monitor
```

### 自定义输入处理器

#### 输入处理器接口
```python
class InputProcessor:
    """输入处理器基类"""

    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    def can_process(self, action: dict) -> bool:
        """判断是否可以处理此动作"""
        return False

    def process_action(self, action: dict) -> bool:
        """处理动作，返回是否成功"""
        return False

    def get_priority(self) -> int:
        """获取处理器优先级，数字越小优先级越高"""
        return 100
```

#### 实际实现的输入处理器
```python
class DefaultInputProcessor(InputProcessor):
    """默认输入处理器 - 处理标准按键和鼠标操作"""

    def can_process(self, action: dict) -> bool:
        """可以处理所有标准输入动作"""
        action_type = action.get("type", "")
        return action_type in ["key_press", "key_release", "mouse_click", "delay"]

    def process_action(self, action: dict) -> bool:
        """处理标准输入动作"""
        action_type = action.get("type", "")

        if action_type == "key_press":
            key = action.get("key", "")
            return self._send_key(key)
        elif action_type == "mouse_click":
            button = action.get("button", "left")
            return self._click_mouse(button)
        elif action_type == "delay":
            delay_ms = action.get("delay", 50)
            time.sleep(delay_ms / 1000.0)
            return True

        return False
```

#### 示例：语音输入处理器
```python
class VoiceInputProcessor(InputProcessor):
    """语音输入处理器"""
    
    def __init__(self):
        super().__init__("voice_input")
        self.voice_commands = {
            "attack": "1",
            "heal": "2", 
            "escape": "escape"
        }
    
    def can_process(self, action: dict) -> bool:
        """检查是否为语音动作"""
        return action.get("type") == "voice_command"
    
    def process_action(self, action: dict) -> bool:
        """处理语音命令"""
        command = action.get("command", "").lower()
        
        if command in self.voice_commands:
            key = self.voice_commands[command]
            
            # 转换为按键动作
            key_action = {
                "type": "key_press",
                "key": key,
                "priority": action.get("priority", False)
            }
            
            # 委托给默认处理器
            from torchlight_assistant.core.input_handler import DefaultInputProcessor
            default_processor = DefaultInputProcessor()
            return default_processor.process_action(key_action)
        
        return False
    
    def get_priority(self) -> int:
        return 10  # 较高优先级

# 注册处理器
def register_voice_processor(input_handler):
    """注册语音处理器"""
    voice_processor = VoiceInputProcessor()
    input_handler.register_processor(voice_processor)
    
    # 使用示例
    input_handler.enqueue_action({
        "type": "voice_command",
        "command": "heal",
        "priority": True
    })
```

### 自定义条件检查器

#### 条件检查器接口
```python
class ConditionChecker:
    """条件检查器基类"""
    
    def __init__(self, name: str):
        self.name = name
    
    def check_condition(self, skill_config: dict, frame, **kwargs) -> bool:
        """检查条件是否满足"""
        return True
    
    def get_condition_id(self) -> int:
        """获取条件ID"""
        return -1
```

#### 示例：敌人存在检查器
```python
class EnemyPresenceChecker(ConditionChecker):
    """敌人存在检查器"""
    
    def __init__(self):
        super().__init__("enemy_presence")
        self.enemy_colors = [
            [255, 0, 0],    # 红色敌人
            [255, 255, 0],  # 黄色敌人
        ]
    
    def check_condition(self, skill_config: dict, frame, **kwargs) -> bool:
        """检查屏幕上是否有敌人"""
        if frame is None:
            return False
        
        # 获取检测区域
        detection_area = skill_config.get("enemy_detection_area", [0, 0, frame.shape[1], frame.shape[0]])
        x, y, w, h = detection_area
        roi = frame[y:y+h, x:x+w]
        
        # 检查是否存在敌人颜色
        for enemy_color in self.enemy_colors:
            if self._detect_color_presence(roi, enemy_color, tolerance=30):
                return True
        
        return False
    
    def _detect_color_presence(self, image, target_color, tolerance=30):
        """检测颜色是否存在"""
        import numpy as np
        
        # 计算颜色差异
        diff = np.abs(image - target_color)
        matches = np.all(diff < tolerance, axis=2)
        
        # 如果匹配像素超过阈值则认为存在
        match_ratio = np.sum(matches) / matches.size
        return match_ratio > 0.01  # 1%的像素匹配即认为存在
    
    def get_condition_id(self) -> int:
        return 3  # 自定义条件ID

# 注册条件检查器
def register_enemy_checker(skill_manager):
    """注册敌人检查器"""
    enemy_checker = EnemyPresenceChecker()
    skill_manager.register_condition_checker(enemy_checker)
    
    # 配置示例
    skill_config = {
        "name": "攻击技能",
        "key": "1",
        "execute_condition": 3,  # 使用敌人存在检查器
        "enemy_detection_area": [200, 200, 400, 300]
    }
```

---

## 🔨 插件开发

### 插件架构

#### 插件基类
```python
import abc
from typing import Dict, Any, Optional

class Plugin(abc.ABC):
    """插件基类"""
    
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self.enabled = False
        self.config = {}
    
    @abc.abstractmethod
    def initialize(self, context: Dict[str, Any]) -> bool:
        """初始化插件"""
        pass
    
    @abc.abstractmethod
    def start(self) -> bool:
        """启动插件"""
        pass
    
    @abc.abstractmethod
    def stop(self) -> bool:
        """停止插件"""
        pass
    
    @abc.abstractmethod
    def cleanup(self):
        """清理插件资源"""
        pass
    
    def configure(self, config: Dict[str, Any]):
        """配置插件"""
        self.config = config
        self.enabled = config.get("enabled", False)
    
    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
            "description": self.__doc__ or ""
        }
```

#### 插件管理器
```python
class PluginManager:
    """插件管理器"""
    
    def __init__(self):
        self.plugins = {}
        self.plugin_configs = {}
        self.context = {}
    
    def set_context(self, context: Dict[str, Any]):
        """设置插件上下文"""
        self.context = context
    
    def load_plugin(self, plugin_class, config: Dict[str, Any] = None):
        """加载插件"""
        try:
            plugin_instance = plugin_class()
            plugin_name = plugin_instance.name
            
            # 配置插件
            if config:
                plugin_instance.configure(config)
            
            # 初始化插件
            if plugin_instance.initialize(self.context):
                self.plugins[plugin_name] = plugin_instance
                self.plugin_configs[plugin_name] = config or {}
                print(f"插件加载成功: {plugin_name}")
                return True
            else:
                print(f"插件初始化失败: {plugin_name}")
                return False
                
        except Exception as e:
            print(f"插件加载错误: {e}")
            return False
    
    def start_plugin(self, plugin_name: str) -> bool:
        """启动插件"""
        plugin = self.plugins.get(plugin_name)
        if plugin and plugin.enabled:
            return plugin.start()
        return False
    
    def stop_plugin(self, plugin_name: str) -> bool:
        """停止插件"""
        plugin = self.plugins.get(plugin_name)
        if plugin:
            return plugin.stop()
        return False
    
    def start_all_plugins(self):
        """启动所有启用的插件"""
        for plugin_name, plugin in self.plugins.items():
            if plugin.enabled:
                self.start_plugin(plugin_name)
    
    def stop_all_plugins(self):
        """停止所有插件"""
        for plugin_name in self.plugins:
            self.stop_plugin(plugin_name)
    
    def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """获取插件信息"""
        plugin = self.plugins.get(plugin_name)
        return plugin.get_info() if plugin else None
    
    def list_plugins(self) -> Dict[str, Dict[str, Any]]:
        """列出所有插件"""
        return {name: plugin.get_info() for name, plugin in self.plugins.items()}
```

### 示例插件

#### 自动截图插件
```python
class AutoScreenshotPlugin(Plugin):
    """自动截图插件"""
    
    def __init__(self):
        super().__init__("auto_screenshot", "1.0.0")
        self.screenshot_interval = 60  # 60秒
        self.save_path = "screenshots"
        self.scheduler = None
        self.border_manager = None
    
    def initialize(self, context: Dict[str, Any]) -> bool:
        """初始化插件"""
        try:
            self.scheduler = context.get("scheduler")
            self.border_manager = context.get("border_manager")
            
            if not self.scheduler or not self.border_manager:
                return False
            
            # 创建截图目录
            import os
            os.makedirs(self.save_path, exist_ok=True)
            
            return True
        except Exception as e:
            print(f"自动截图插件初始化失败: {e}")
            return False
    
    def start(self) -> bool:
        """启动插件"""
        if not self.scheduler:
            return False
        
        # 添加定时截图任务
        success = self.scheduler.add_task(
            task_id="auto_screenshot",
            interval=self.screenshot_interval,
            callback=self._take_screenshot
        )
        
        if success:
            print("自动截图插件已启动")
        
        return success
    
    def stop(self) -> bool:
        """停止插件"""
        if self.scheduler:
            self.scheduler.remove_task("auto_screenshot")
            print("自动截图插件已停止")
        return True
    
    def cleanup(self):
        """清理插件资源"""
        self.stop()
    
    def configure(self, config: Dict[str, Any]):
        """配置插件"""
        super().configure(config)
        self.screenshot_interval = config.get("interval", 60)
        self.save_path = config.get("save_path", "screenshots")
    
    def _take_screenshot(self):
        """执行截图"""
        if not self.border_manager:
            return
        
        frame = self.border_manager.get_current_frame()
        if frame is not None:
            import cv2
            from datetime import datetime
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.save_path}/screenshot_{timestamp}.png"
            
            cv2.imwrite(filename, frame)
            print(f"截图已保存: {filename}")

# 使用插件
def setup_plugins(macro_engine):
    """设置插件系统"""
    plugin_manager = PluginManager()
    
    # 设置插件上下文
    context = {
        "scheduler": macro_engine.unified_scheduler,
        "border_manager": macro_engine.border_manager,
        "input_handler": macro_engine.input_handler,
        "event_bus": event_bus
    }
    plugin_manager.set_context(context)
    
    # 加载自动截图插件
    screenshot_config = {
        "enabled": True,
        "interval": 30,
        "save_path": "auto_screenshots"
    }
    plugin_manager.load_plugin(AutoScreenshotPlugin, screenshot_config)
    
    # 启动所有插件
    plugin_manager.start_all_plugins()
    
    return plugin_manager
```

---

## 📚 高级扩展示例

### 自定义游戏支持

#### 游戏配置模板
```python
class GameProfile:
    """游戏配置模板"""
    
    def __init__(self, name: str):
        self.name = name
        self.window_title = ""
        self.resolution = (1920, 1080)
        self.ui_elements = {}
        self.skill_layouts = {}
        self.color_themes = {}
    
    def setup_ui_elements(self):
        """设置UI元素位置"""
        # 子类实现具体游戏的UI布局
        pass
    
    def get_skill_coordinates(self, skill_slot: int):
        """获取技能位置"""
        # 子类实现技能位置映射
        pass
    
    def detect_game_state(self, frame):
        """检测游戏状态"""
        # 子类实现游戏状态检测
        pass

class Diablo4Profile(GameProfile):
    """暗黑破坏神4游戏配置"""
    
    def __init__(self):
        super().__init__("Diablo 4")
        self.window_title = "Diablo IV"
        self.setup_ui_elements()
    
    def setup_ui_elements(self):
        """设置D4 UI元素"""
        self.ui_elements = {
            "hp_bar": [136, 910, 213, 1004],
            "mp_bar": [136, 870, 213, 894],
            "skill_bar": [760, 1010, 400, 50],
            "minimap": [1670, 50, 200, 200]
        }
        
        self.skill_layouts = {
            "1": [778, 1018, 34, 34],   # 技能槽1
            "2": [826, 1018, 34, 34],   # 技能槽2  
            "3": [874, 1018, 34, 34],   # 技能槽3
            "4": [922, 1018, 34, 34],   # 技能槽4
            "q": [970, 1018, 34, 34],   # Q技能
            "w": [1018, 1018, 34, 34],  # W技能
            "e": [1066, 1018, 34, 34],  # E技能
            "r": [1114, 1018, 34, 34],  # R技能
        }
    
    def get_skill_coordinates(self, skill_slot: str):
        """获取技能坐标"""
        return self.skill_layouts.get(skill_slot, [0, 0, 20, 20])
    
    def detect_game_state(self, frame):
        """检测D4游戏状态"""
        # 检测是否在主界面、战斗中、商店等
        states = {
            "in_game": self._detect_in_game(frame),
            "in_menu": self._detect_in_menu(frame),
            "in_inventory": self._detect_in_inventory(frame)
        }
        return states
    
    def _detect_in_game(self, frame):
        """检测是否在游戏中"""
        # 通过HP条存在判断
        hp_region = self.ui_elements["hp_bar"]
        x, y, w, h = hp_region
        hp_roi = frame[y:y+h, x:x+w]
        
        # 检测红色HP条
        red_pixels = np.sum((hp_roi[:,:,2] > 150) & (hp_roi[:,:,0] < 100) & (hp_roi[:,:,1] < 100))
        return red_pixels > 100  # 足够的红色像素表示在游戏中

# 游戏配置工厂
class GameProfileFactory:
    """游戏配置工厂"""
    
    profiles = {
        "diablo4": Diablo4Profile,
        # 可以添加更多游戏支持
        # "poe2": PathOfExile2Profile,
        # "wow": WowProfile,
    }
    
    @classmethod
    def create_profile(cls, game_name: str) -> GameProfile:
        """创建游戏配置"""
        profile_class = cls.profiles.get(game_name.lower())
        if profile_class:
            return profile_class()
        else:
            raise ValueError(f"不支持的游戏: {game_name}")
    
    @classmethod
    def list_supported_games(cls):
        """列出支持的游戏"""
        return list(cls.profiles.keys())
```

### 机器学习集成

#### 智能技能使用AI
```python
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import joblib

class SkillAI:
    """技能使用AI决策"""
    
    def __init__(self):
        self.model = RandomForestClassifier(n_estimators=100)
        self.is_trained = False
        self.features = []
        self.labels = []
    
    def extract_features(self, game_state):
        """提取游戏状态特征"""
        features = [
            game_state.get("hp_percentage", 0),
            game_state.get("mp_percentage", 0),
            game_state.get("enemy_count", 0),
            game_state.get("enemy_distance", 1000),
            game_state.get("buff_count", 0),
            game_state.get("debuff_count", 0),
            int(game_state.get("in_combat", False)),
            int(game_state.get("has_target", False))
        ]
        return np.array(features).reshape(1, -1)
    
    def record_action(self, game_state, skill_used, effectiveness):
        """记录动作和效果"""
        features = self.extract_features(game_state).flatten()
        
        # 将技能转换为数值标签
        skill_labels = {"1": 0, "2": 1, "q": 2, "w": 3, "e": 4, "r": 5}
        skill_label = skill_labels.get(skill_used, -1)
        
        if skill_label >= 0:
            self.features.append(features)
            # 效果加权：效果好的动作权重更高
            weighted_label = skill_label if effectiveness > 0.7 else -1
            self.labels.append(weighted_label)
    
    def train_model(self):
        """训练模型"""
        if len(self.features) < 50:  # 至少需要50个样本
            return False
        
        X = np.array(self.features)
        y = np.array(self.labels)
        
        # 过滤掉负效果样本
        valid_indices = y >= 0
        X = X[valid_indices]
        y = y[valid_indices]
        
        if len(X) < 10:
            return False
        
        self.model.fit(X, y)
        self.is_trained = True
        return True
    
    def predict_best_skill(self, game_state):
        """预测最佳技能"""
        if not self.is_trained:
            return None
        
        features = self.extract_features(game_state)
        probabilities = self.model.predict_proba(features)[0]
        
        # 转换回技能按键
        skill_keys = ["1", "2", "q", "w", "e", "r"]
        best_skill_index = np.argmax(probabilities)
        confidence = probabilities[best_skill_index]
        
        if confidence > 0.6:  # 置信度阈值
            return skill_keys[best_skill_index]
        
        return None
    
    def save_model(self, filepath):
        """保存模型"""
        if self.is_trained:
            joblib.dump(self.model, filepath)
    
    def load_model(self, filepath):
        """加载模型"""
        try:
            self.model = joblib.load(filepath)
            self.is_trained = True
            return True
        except:
            return False

# AI增强的技能管理器
class AIEnhancedSkillManager(CustomModule):
    """AI增强的技能管理器"""
    
    def __init__(self):
        super().__init__("ai_skill_manager")
        self.skill_ai = SkillAI()
        self.game_state_analyzer = GameStateAnalyzer()
        self.last_action_time = 0
        self.action_cooldown = 1.0  # 1秒决策间隔
    
    def _process_iteration(self):
        """AI决策循环"""
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        
        # 分析当前游戏状态
        game_state = self.game_state_analyzer.analyze()
        
        # AI推荐技能
        recommended_skill = self.skill_ai.predict_best_skill(game_state)
        
        if recommended_skill:
            # 执行推荐技能
            self.input_handler.enqueue_action({
                "type": "key_press",
                "key": recommended_skill,
                "priority": False
            })
            
            self.last_action_time = current_time
            
            # 记录动作（需要后续评估效果）
            self._schedule_effectiveness_evaluation(game_state, recommended_skill)
    
    def _schedule_effectiveness_evaluation(self, game_state, skill_used):
        """安排效果评估"""
        def evaluate_effectiveness():
            # 2秒后评估效果
            new_state = self.game_state_analyzer.analyze()
            effectiveness = self._calculate_effectiveness(game_state, new_state)
            self.skill_ai.record_action(game_state, skill_used, effectiveness)
        
        # 延迟评估
        threading.Timer(2.0, evaluate_effectiveness).start()
    
    def _calculate_effectiveness(self, before_state, after_state):
        """计算动作效果"""
        # 简单的效果评估逻辑
        hp_change = after_state.get("hp_percentage", 0) - before_state.get("hp_percentage", 0)
        enemy_count_change = before_state.get("enemy_count", 0) - after_state.get("enemy_count", 0)
        
        effectiveness = 0.5  # 基础分
        
        # HP增加是好事
        if hp_change > 0:
            effectiveness += 0.3
        
        # 敌人减少是好事
        if enemy_count_change > 0:
            effectiveness += 0.2
        
        return min(1.0, effectiveness)
```

通过这套完整的API参考和扩展系统，开发者可以根据具体需求灵活扩展pyahk的功能，实现高度定制化的游戏自动化解决方案。