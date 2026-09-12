# 03 — 模块与 API

> 写新功能或加新接口前查这里。每个模块只列**核心方法签名 + 一段职责说明**,详细行为看源码。

## 模块依赖关系

```
                              MacroEngine
                          (状态机协调中枢)
            ┌────────┬────────┴────────┬────────┐
            ▼        ▼                 ▼        ▼
    SkillManager  ResourceManager  PathfindingMgr  AffixRerollMgr
            │        │                 │            │
            │        │                 │            │
            └────┬───┴─────────────────┘            │
                 ▼                                  │
        AHKInputHandler ◄─────── BorderFrameManager │
                 │                       │          │
                 ▼                       ▼          │
         AHKCommandSender         NativeCapture(C++)│
                 │                                  │
                 ▼ WM_COPYDATA                      │
            [AHK 子进程]                            │
                                                    │
              SignalBridge / EventBus(单例,所有模块共享)
                              ▲
                              │
                  AHKEventFilter(全局原生事件)
```

---

## torchlight_assistant/core/

### MacroEngine `core/macro_engine.py`

状态机协调中枢。持有所有 manager,响应热键事件,在状态转换时启停子系统。

```python
class MacroEngine:
    VALID_TRANSITIONS = {
        MacroState.STOPPED: [MacroState.READY],
        MacroState.READY:   [MacroState.RUNNING, MacroState.STOPPED],
        MacroState.RUNNING: [MacroState.PAUSED, MacroState.STOPPED],
        MacroState.PAUSED:  [MacroState.RUNNING, MacroState.STOPPED],
    }

    def __init__(self, sound_manager=None, config_file: str = "default.json"): ...
    def get_current_state(self) -> MacroState: ...
    def prepare_border_only(self) -> bool: ...           # → READY
    def stop_macro(self) -> bool: ...                    # → STOPPED
    def toggle_pause_resume(self) -> bool: ...           # RUNNING ↔ PAUSED
    def load_config(self, config_file: str) -> bool: ...
    def save_full_config(self, file_path: str, full_config: dict) -> bool: ...
    def set_debug_mode(self, enabled: bool) -> bool: ...
    def cleanup(self): ...                               # 退出时分层清理
```

**事件订阅**(在 `_setup_primary_hotkey()` 与 `_setup_event_subscriptions()`):
- `intercept_key_down` → `_handle_ahk_intercept_key`(STOPPED 下的 F8 先转给 GUI 采集当前配置;停止路径直接处理;其余分发 F7/F9/Z/原地模式键/BOSS 模式键)
- `ui:sync_and_toggle_state_requested` → `_handle_f8_press(full_config)`
- `ui:load_config_requested` → JSON 解析 + 顶层/`skills`/`global` 最小结构校验后提交;发布 `engine:config_load_result`
- `ui:save_full_config_requested` → 原子保存;成功后发布 `engine:config_updated` 与 `engine:config_save_result`
- `special_key_pause` → 启用/关闭 `set_drop_non_emergency`
- `managed_key_down` → `clear_non_emergency_queue`(不暂停调度器,避免 HP/MP 检测停摆)
- `managed_key_complete` → 记录完成;AHK 自行解除抑制并补齐持键
- `monitor_key_down/up` → 强制移动状态切换

---

### SkillManager `core/skill_manager.py`

技能调度。订阅 `engine:config_updated`,根据配置往 `UnifiedScheduler` 注册 3 类任务:
- 定时技能(`TriggerMode=0`)→ `execute_timed_skill(skill_name)`
- 冷却检查(`TriggerMode=1`)→ `check_cooldowns()`(定时一次性扫所有技能)
- 通用宏(`sequence_enabled=true`)→ Python 下发 `macro_steps` 给 AHK 端宏解释器,不再注册 Python 步进任务
- 资源管理 → `check_resources()`(宏/技能模式都注册,2025 修复 B1)

```python
class SkillManager:
    def __init__(self, input_handler, macro_engine_ref, border_manager,
                 resource_manager=None, debug_display_manager=None): ...

    def start(self): ...
    def stop(self, *, cleanup_input: bool = True): ...
    def pause(self): ...
    def resume(self): ...

    def update_all_configs(self, skills_config: dict): ...
    def update_global_config(self, global_config: dict): ...
    def _apply_config_update(self, skills_config: dict,
                             global_config: dict): ...    # 双配置最终状态事务

    # 调度器回调
    def execute_timed_skill(self, skill_name: str): ...
    def check_cooldowns(self): ...                       # 一帧检测所有技能
    def check_resources(self): ...                       # 委派给 resource_manager

    # AHK 端通用宏控制
    def _set_ahk_macro_steps(self): ...
    def _start_ahk_macro(self, sync_steps: bool = True): ...
    def _stop_ahk_macro(self): ...
    def set_boss_mode_active(self, active: bool): ...
```

**性能优化**:`_prepare_frame_detection_cache()` 一次取帧给所有技能用,避免重复 `get_current_frame()`。

**管理键执行**:不暂停 `UnifiedScheduler`。输入独占由 AHK 端清理非紧急队列、`delay_clear` 和宏/持键抑制保证,HP/MP 检测继续运行。

**hold 模式技能**(`TriggerMode=2`):Python 用 `set_skill_hold_keys()` 下发完整期望集合,AHK 独占维护实际持键账本并做差量同步。`start/resume` 声明完整集合,`pause/stop` 声明空集合;按下顺序稳定为鼠标键优先,同类内部按配置顺序。

**配置热更新事务**:`engine:config_updated` 携带的 skills/global 会作为一个最终状态一起应用。事务期间旧调度回调在实际发键前退出；AHK 只收到最终模式需要的持键或宏状态，调度任务在输入提交成功后才变更。例如“技能模式 A 持键 → 宏模式且新配置含 B 持键”只会释放旧集合并启动宏，不会短暂按下 B。任何输入同步失败都会先关闭 AHK 运行时闸门，再等待 Python 调度线程退出并请求状态机回退 STOPPED。

技能检测携带配置世代令牌：即使图像检测阻塞到热更新完成、事务门重新开放后才返回，
旧结果仍不得发送旧 Key/AltKey 或写入新配置的连续性历史。检测在锁外执行，
令牌核对与历史追加/有界发送在短临界区完成；不能只靠布尔“正在更新”标志。

上层已成功执行 AHK 单条原子 `reset_runtime` 时，调用 `stop(cleanup_input=False)` 只停止并等待 Python 调度生产者，不再重复发送空持键和 `stop_macro`；独立调用 `stop()` 默认仍执行完整输入清理。

**BOSS 模式**:`BossOnly=true` 的定时/冷却技能在 BOSS 模式关闭时跳过;按住型不参与 BOSS 模式,GUI 和配置归一化会禁用该组合。

**通用宏模式**:`sequence_enabled=true` 时,定时/冷却任务不注册,AHK 端 `MacroTick` 循环执行 `macro_steps`;Python 调度器只保留资源检测任务。

---

### ResourceManager `core/resource_manager.py`

HP/MP 自动药剂。被动调用,不持有线程,被 `SkillManager.check_resources()` 触发。

```python
class ResourceManager:
    def __init__(self, border_manager, input_handler, debug_display_manager=None): ...

    def update_config(self, resource_config: dict,
                      tesseract_config: Optional[dict] = None): ...
    def check_and_execute_resources(self, cached_frame=None) -> bool:
        """检查并按阈值触发药剂(被动调用)"""

    def capture_template_hsv(self, frame): ...           # F8 进 READY 时截模板
    def auto_detect_orbs(self, orb_type: str) -> dict:   # 自动检测圆形血/魔球
    def start(self) / stop() / pause() / resume(): ...
```

**3 种检测模式**(`hp_config["detection_mode"]`):
- `rectangle`(默认): HSV 模板匹配,**0.3ms**,推荐
- `circle`: 圆形蒙版 + HSV 匹配；历史运行估计 ~5ms，当前基准脚本未覆盖
- `text_ocr`: 数字 OCR,4 种引擎(`paddle`/`template`/`keras`/`tesseract`)；
  当前 478 次历史基准只覆盖后三种，Paddle 的 CPU/GPU 数值是运行估计

所有矩形区域均采用虚拟桌面绝对物理像素的半开区间
`(x1, y1, x2, y2)`。原点 `0` 与左/上副屏的负坐标都合法；缺字段、零面积，
或换算到当前捕获 output 后越界的区域视为本轮检测无效，不会触发药剂。

**冷却保护**:`_flask_cooldowns` 用 `time.monotonic()`(2025 修复,避免系统校时影响)。
只有 HP/MP 紧急命令成功提交给 AHK 后才记录内部冷却；传输失败或业务拒绝不会制造“实际没喝药、却等待完整冷却”的假状态。

Tesseract 配置以当前 `global.tesseract_ocr` 为唯一来源。配置签名改变时会先构造新实例、
成功后再原子替换；在途识别可安全使用旧的不可变实例，不会混用两份配置。

---

### AHKInputHandler `core/ahk_input_handler.py`

AHK 子进程封装。持有 `AHKCommandSender`,提供"语义化"输入接口。

```python
class AHKInputHandler:
    def __init__(self, event_bus=None, debug_display_manager=None):
        # 启动 hold_server_extended.ahk 子进程, 等 1.5s, PING 验证
        # 连接 ahk_signal_bridge.ahk_event 信号

    # 语义化执行(自动选 priority)
    def execute_skill_normal(self, key: str): ...        # priority=2
    def execute_skill_high(self, key: str): ...          # priority=1
    def execute_utility(self, key: str): ...             # priority=3
    def execute_hp_potion(self, key: str): ...           # priority=0(emergency)
    def execute_mp_potion(self, key: str): ...           # priority=0

    # 通用按键
    def send_key(self, key_str: str) -> bool:
        """支持单键 'q' 或序列 'delay50,q,delay100,w'"""
    def click_mouse(self, button: str = "left") -> bool: ...
    def set_skill_hold_keys(self, keys) -> bool: ...          # 声明 TriggerMode=2 的完整期望持键集合

    # 队列管理
    def clear_queue(self): ...                           # 清全部(包括 emergency)
    def clear_non_emergency_queue(self): ...             # 仅清 high/normal/low

    # Hook 管理
    def register_hook(self, key: str, mode: str = "intercept"): ...
    def unregister_hook(self, key: str): ...
    def clear_all_configurable_hooks(self) -> bool: ...  # 保留 F8/F7/F9 永久根热键

    # 状态切换
    def set_python_window_state(self, state: str) -> bool:  # "main"/"osd"
    def set_force_move_state(self, active: bool) -> bool: ...
    def set_force_move_key(self, key: str) -> bool: ...
    def set_force_move_replacement_key(self, key: str) -> bool: ...
    def set_force_move_passthrough_keys(self, keys) -> bool: ...
    def set_drop_non_emergency(self, enabled: bool): ...
    def set_dry_run_mode(self, enabled: bool): ...

    # AHK 端通用宏
    def set_macro_steps(self, steps) -> bool: ...
    def start_macro(self) -> bool: ...
    def stop_macro(self) -> bool: ...

    # 坐标点击复用 AHK 动作队列；hold_time>0 时非阻塞按住并保证清理时补 key-up
    def click_mouse_at(self, x: int, y: int, hold_time=None) -> bool: ...

    # 运行时闸门/所有权
    def set_accepting_actions(self, enabled: bool, owner=None, epoch=None) -> bool: ...
    def set_runtime_owner(self, owner: str, epoch: int) -> bool: ...
    def reset_runtime(self) -> bool: ...

    def cleanup(self) / stop(): ...                      # 断开事件入口并终止 AHK 进程
```

事件桥使用 `Qt.QueuedConnection`，因此 `disconnect()` 本身不会撤销已经排队的 slot。`stop/cleanup` 会先设置 disposed 门禁，迟到的旧实例事件在进入 EventBus 前被丢弃。

`direct` 模式写入系统全局输入流：配置了目标选择器后，每个新 down/press/坐标点击
都会复核目标仍在前台，切走时还会释放已在飞的全局键；目标留空才保留“向当前前台”
的兼容语义。`control` 模式的键盘输入使用 `ControlSend`，鼠标按钮使用 `ControlClick`，
均沿 down 时的目标与发送方式配对释放，可在后台投递；**屏幕坐标点击始终是全局输入**，
即使配置了 `control` 也要求明确目标且目标当前在前台，坐标必须位于
该前台 HWND 当前客户区的半开边界内。动作入队后若窗口移动或缩放，执行时会重新校验。

`dry_run_mode=True` 时，普通动作、非空持键声明和宏启动只记录到 `debug_display_manager`；空持键声明、停宏、关闸、清队、重置运行时等**安全清理命令仍会真实下发**，否则从真实运行切到干跑时可能留下卡键或后台宏。

**TriggerMode=2 声明式持键语义**:
- Python 不镜像“实际按住了什么”,只发送完整期望集合;AHK 端按 LIFO 释放多余键、按配置顺序补按缺失键
- 非空声明在特殊键/管理键抑制期只更新期望,暂不产生 down;抑制解除时 `ReconcileSkillHoldKeys()` 自动补齐
- 空集合、运行时关闸和 `ClearQueue(-1)` 都是安全清理路径,直接释放 AHK 账本中的持键,不依赖普通动作队列
- 管理键临时 `hold:target/release:target` 若与持久持键同名,执行器会同步修正账本并重新对齐,避免物理状态与账本分叉

---

### AHKCommandSender `core/ahk_command_sender.py`

把动作翻译成 AHK 命令并通过 WM_COPYDATA 发送。所有 `CMD_*` 的对外接口在这里。

```python
class AHKCommandSender:
    def __init__(self, window_title: str = "HoldServer_Window_UniqueName_12345"):
        # 构造时自动 PING, 失败抛 ConnectionError

    # 队列入队(底层)
    def enqueue(self, action: str, priority: int = 2) -> bool:
        """action 形如 'press:q' / 'delay:50' / 'sequence:q,delay100,w'"""
    def send_key(self, key: str, priority: int = 2) -> bool: ...
    def send_sequence(self, sequence: str, priority: int = 2) -> bool: ...
    def send_mouse_click(self, button: str = "left", priority: int = 2) -> bool: ...
    def send_mouse_click_at(self, x: int, y: int, hold_ms: int = 0,
                            priority: int = 2) -> bool: ...
    def set_skill_hold_keys(self, keys) -> bool: ...

    # 语义化便捷方法
    def send_emergency(self, key: str): ...              # priority=0
    def send_high_priority(self, key: str): ...          # priority=1
    def send_normal(self, key: str): ...                 # priority=2
    def send_low_priority(self, key: str): ...           # priority=3

    # Hook 管理
    def register_hook(self, key: str, mode: str = "intercept") -> bool: ...
    def unregister_hook(self, key: str) -> bool: ...
    def clear_all_configurable_hooks(self) -> bool: ...

    # 队列控制
    def pause(self) -> bool / resume(self) -> bool: ...   # 旧 CMD_PAUSE/RESUME 兼容接口
    def clear_queue(self, priority: int = -1) -> bool:
        """-1=全部, -2=非紧急, 0-3=单优先级"""

    # 配置
    def set_target_window(self, target: str) -> bool: ...     # "ahk_exe game.exe"
    def activate_window(self) -> bool: ...
    def set_send_mode(self, mode: str) -> bool: ...           # "direct"/"control"
    def set_stationary_mode(self, active: bool, mode_type: str) -> bool: ...  # 仅接受 shift_modifier/block_mouse 激活
    def set_force_move_key(self, key: str): ...
    def set_force_move_state(self, active: bool): ...
    def set_force_move_replacement_key(self, key: str): ...
    def set_force_move_passthrough_keys(self, keys) -> bool: ...
    def set_managed_key_config(self, key: str, target: str, delay: int, hold_ms: int = 0): ...
    def set_python_window_state(self, state: str) -> bool: ...
    def batch_update_config(self, config_dict: dict) -> bool: ...

    # AHK 端通用宏
    def serialize_macro_steps(steps) -> str: ...         # "type:data" 行协议
    def set_macro_steps(self, steps) -> bool: ...
    def start_macro(self) -> bool: ...
    def stop_macro(self) -> bool: ...

    # 原子 STOPPED 屏障与跨模式所有权
    def set_accepting_actions(self, enabled: bool, *, owner=None, epoch=None) -> bool: ...
    def set_runtime_owner(self, owner: str, epoch: int) -> bool: ...
    def reset_runtime(self) -> bool: ...
```

---

### AHKEventFilter `core/ahk_event_filter.py`

全局 `QAbstractNativeEventFilter`,从 WM_COPYDATA 解析 AHK 事件转发到 SignalBridge。

```python
WM_COPYDATA = 0x004A
AHK_EVENT_DWDATA = 9999    # AHK 端约定的事件标识

class AHKEventFilter(QAbstractNativeEventFilter):
    def nativeEventFilter(self, eventType, message):
        # 解析 COPYDATASTRUCT, dwData==9999 才视为 AHK 事件
        # 解码 UTF-8 后通过 ahk_signal_bridge.ahk_event.emit 发出
        # return (False, 0) 不消费消息,允许窗口默认过程继续
```

`main.py` 启动时安装:
```python
app = QApplication(sys.argv)
app._ahk_event_filter = AHKEventFilter()  # attach 防 GC
app.installNativeEventFilter(app._ahk_event_filter)
```

---

### SignalBridge `core/signal_bridge.py`

Qt Signal 跨线程桥。`ahk_signal_bridge` 是模块级单例,任何线程都可以 `emit`,handler 在主线程跑。

```python
class SignalBridge(QObject):
    ahk_event = Signal(str)

ahk_signal_bridge = SignalBridge()
```

---

### EventBus `core/event_bus.py`

模块级单例。同步 `publish` 有递归保护(threading.local 调用栈),`publish_async` 有并发上限保护(每事件最多 8 个在飞,防递归炸线程池)。

```python
event_bus = EventBus()  # 全局单例

class EventBus:
    def subscribe(self, event_name: str, handler: Callable): ...
    def unsubscribe(self, event_name: str, handler: Callable): ...
    def publish(self, event_name: str, *args, **kwargs):
        """同步发布, 阻塞调用各 handler, 有递归检测"""
    def publish_async(self, event_name: str, *args, **kwargs):
        """异步发布(线程池), 同事件并发上限 8 个"""
    def cleanup(self): ...
```

**常用事件名约定**:
- `engine:state_changed` / `engine:status_updated` / `engine:config_updated`
- `engine:macro_running` / `engine:macro_paused` / 等 4 个状态对应事件
- `intercept_key_down` / `special_key_*` / `managed_key_*` / `monitor_key_*`
- `queue_drop` / `stats` / `ahk_process_died` / `ahk_transport_failed`
- `ui:*` / `hotkey:*` / `affix_reroll:*` / `debug_osd_*`

---

### UnifiedScheduler `core/unified_scheduler.py`

单线程 heapq 调度器,基于 `time.monotonic()`(2025 选型,避免系统时钟回拨)。

```python
class UnifiedScheduler:
    def add_task(self, task_id: str, interval: float, callback: Callable,
                 args=(), kwargs=None, start_immediately=False) -> bool: ...
    def remove_task(self, task_id: str) -> bool: ...
    def update_task_interval(self, task_id: str, new_interval: float) -> bool: ...
    def pause_task(self, task_id: str) / resume_task(self, task_id: str): ...

    def start(self) / stop(): ...
    def pause(self) / resume(self):
        """暂停所有任务. resume 时重新计算所有任务的 next_run 避免雪崩"""
    def clear_all_tasks(): ...
    def get_status(self) -> dict: ...
```

`SkillManager` 注册的任务:`timed_skill_{name}` / `cooldown_checker` / `resource_checker`。宏模式不注册 `sequence_scheduler`;步骤循环在 AHK 端 `MacroTick` 内执行。

每次 `start()` 都创建私有 generation/stop token。`stop()` 等待 callback 最多 2 秒;
若第三方 callback 尚未返回,调度器会 fail-closed 拒绝新一代启动。旧 callback 返回后
worker 才清理世代并允许重启,且不能借下一代的运行状态继续取任务。任务出队后、调用
callback 前还会再次验证世代。

### PathfindingManager `core/pathfinding_manager.py`

固定的永久根热键 F9（不是配置字段）启动该模式的独立寻路 worker。每次运行拥有私有 stop/pause token;捕获、OpenCV 和 A*
等可能阻塞的步骤返回后都会复核 generation,实际点击也与最后一次世代检查线性化。

移动坐标不假设 1920×1080:每次从 `BorderFrameManager` 的目标 HWND 解析客户区,
由客户区中心沿移动方向最多偏移 150px。目标不是当前前台窗口、HWND/客户区无效或
计算点越界时直接跳过点击,避免物理鼠标落到其他应用。寻路必须配置
`window_activation.ahk_class` / `ahk_exe` 至少一个明确目标；`enabled`
仍只决定是否主动激活窗口。未配置目标时 F9 的 READY 事务直接失败；
运行期目标离开前台则 fail-closed 跳过点击。

---

### ConfigManager `core/config_manager.py`

无状态的 JSON IO 工具。MacroEngine 持有一份实例,加载 `default.json` 等。

```python
class ConfigManager:
    def load_config(self, file_path: str) -> dict: ...   # 失败或顶层非对象:记录日志并抛出
    def save_config(self, data: dict, file_path: str): ...  # JSON 缩进 4, ensure_ascii=False
```

`load_config` 不再用空字典表示失败,因为 `{}` 本身是合法 JSON 对象。上层必须显式
处理 `FileNotFoundError` / `JSONDecodeError` / `OSError` / `ValueError`。

---

### DebugDisplayManager `core/debug_display_manager.py`

OSD 调试窗口数据源。SkillManager/ResourceManager 通过它上报当前帧的检测区域、技能匹配度、资源百分比。

```python
class DebugDisplayManager:
    def start(self) / stop(): ...                        # 数据采集开关
    def update_skill_status(self, skill_name, match_percentage, is_ready): ...
    def update_health(self, percentage) / update_mana(percentage): ...
    def update_detection_region(self, region_id, region_data: dict): ...
    def add_action(self, action_text: str): ...          # dry_run 模式记录虚拟按键
```

---

## torchlight_assistant/utils/

### BorderFrameManager `utils/border_frame_manager.py`

帧管理 + HSV 模板匹配。SkillManager/ResourceManager 都通过它取帧和算匹配度。

```python
class BorderFrameManager:
    def prepare_border(self, skills_config, resource_config=None): ...
    def start_capture_loop(self, interval_ms: int = 40, capture_region=None): ...
    def stop() / pause_capture() / resume_capture(): ...
    def cleanup(): ...                                  # stop + 解除配置订阅

    def get_current_frame(self) -> Optional[np.ndarray]:
        """返回只读 BGRA numpy 快照. None 表示捕获未就绪"""
    def get_region_from_frame(self, frame, x, y, w, h) -> Optional[np.ndarray]: ...
    def capture_target_window_frame(self) -> Optional[np.ndarray]: ...

    # HSV 检测
    def compare_cooldown_image(self, frame, x, y, skill_name, size,
                               threshold=0.7) -> Optional[float]:
        """技能冷却匹配度 0-100, None 表示检测失败/无模板"""
    def compare_resource_circle(self, frame, cx, cy, r, resource_type,
                                threshold=0.0, color_config=None) -> Optional[float]: ...
    def _compare_resource_hsv(self, frame, x, y, w, h, resource_name,
                              threshold) -> Optional[float]: ...

    # 模板缓存
    def has_template_cache(self, template_name: str) -> bool: ...
    def set_template_cache(self, template_name: str, template_data: dict): ...

    # 兼容旧 RGB 接口(给 SkillManager 的 BUFF 限制条件用)
    def is_resource_sufficient(self, frame, x, y, color_range_threshold=100) -> bool: ...
    def is_hp_sufficient(self, frame, x, y) -> bool: ...
    def rgb_similarity(self, frame, x, y, target_color, tolerance) -> bool: ...
```

---

### NativeGraphicsCaptureManager `utils/native_graphics_capture_manager.py`

C++ DXGI Desktop Duplication 库的 Python 包装。BorderFrameManager 通过它真正拉帧。

```python
class NativeGraphicsCaptureManager:
    def __init__(self, config: CaptureConfig): ...
    def initialize(self) -> bool: ...
    def start_capture(self) -> bool / stop_capture(): ...
    def pause_capture() / resume_capture(): ...
    def get_latest_frame(self) -> Optional[np.ndarray]: ...
    def get_latest_frame_rect(self) -> Optional[dict]: ...  # 虚拟桌面绝对 x/y/w/h
    def capture_single_frame(self) -> Optional[np.ndarray]: ...
    def set_capture_config(self, config_dict: dict) -> bool: ...
    def cleanup(): ...

@dataclass
class CaptureConfig:
    target_window_title: str = ""
    target_window_handle: Optional[int] = None
    capture_monitor: bool = False
    monitor_index: int = 0
    frame_callback: Optional[Callable] = None
    capture_interval_ms: int = 60
    enable_region: bool = False
    region_x/y/width/height: int = 0
```

`region_x/y` 与检测配置均为虚拟桌面物理像素绝对坐标；`get_latest_frame_rect()` 给出最新帧的同一坐标系矩形。窗口捕获会随 HWND 跨 output 重建 DXGI duplication，`monitor_index` 则是全部 DXGI outputs 的零基序号。

---

### 其他 utils

| 文件 | 用途 |
|------|------|
| `paddle_ocr_manager.py` | PaddleOCR 引擎(HP/MP 数字 OCR、区域框自动校准;也可给洗练扩展复用) |
| `tesseract_ocr_manager.py` | Tesseract 引擎(给资源 text_ocr 用) |
| `cnn_digit_recognizer.py` | 自训 Keras CNN 数字识别 |
| `window_utils.py` | Win32 窗口枚举/类名/进程名查询 |
| `sound_manager.py` | 音效播放(状态切换提示音) |
| `debug_log.py` | LOG / LOG_INFO / LOG_ERROR(DEBUG 环境变量控制) |
| `a_star.py` | A* 寻路算法(给 PathfindingManager) |

---

## 主要 GUI Widget(`torchlight_assistant/gui/`)

只列文件名,UI 细节看源码:

| 文件 | 主要 Widget |
|------|-------------|
| `main_window.py` | `GameSkillConfigUI` 主窗口 |
| `status_window.py` | `OSDStatusWindow` 状态 OSD |
| `debug_osd_window.py` | `DebugOsdWindow` 调试 OSD |
| `skill_config_widget.py` | 技能配置 |
| `resource_widgets.py` | HP/MP 配置(含三种检测模式) |
| `resource_config_manager.py` | Resource 配置中央化(2025.10 重构提取) |
| `priority_keys_widget.py` | 优先级按键(special/managed/mapping,含 Qt 本地按键捕获兜底) |
| `macro_steps_widget.py` | 通用宏步骤编辑器(down/up/press/delay) |
| `key_capture.py` | GUI 按键捕获 mixin(支持 XButton1/XButton2) |
| `region_selection_dialog.py` | 区域选择对话框 |
| `color_picker_dialog.py` | HSV 颜色选择 |
| `color_analysis_tools.py` | 可视化 HSV 调参工具 |
| `feature_widgets.py` | 杂项功能 widget |
| `config_widgets.py` | 通用配置 widget |
| `basic_widgets.py` | 基础 widget |
| `custom_widgets.py` | 自定义控件 |
| `ui_components.py` | 顶部控件组合 |
| `styles.py` | QSS 样式 |

---

## 跨模块约定

1. **事件广播与生命周期调用分层**:状态/配置通知通过 `event_bus.publish/subscribe`
   解耦；`MacroEngine` 作为所有者可直接调 manager 的 start/stop/pause/resume，
   `SkillManager` 也会同步调用其 `ResourceManager`。
2. **配置变更**统一发 `engine:config_updated`(skills_config, global_config)
3. **状态变更**发 `engine:state_changed`(new_state, old_state)
4. **Qt UI 更新**必须从主线程,跨线程要走 `QTimer.singleShot(0, ...)` 或 SignalBridge
5. **AHK 命令幂等性**:`register_hook` 用 `Hotkey(..., "On")` 选项,重复注册无害
