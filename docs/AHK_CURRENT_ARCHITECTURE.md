# AHK 输入系统当前实现参考

> 本文以仓库当前源码和测试为准。改动输入协议、状态机或队列时，先更新实现，再同步这里和 `wiki/02`、`wiki/03`、`wiki/04` 的对应章节。命令 ID 必须同时存在于 `torchlight_assistant/config/ahk_commands.py` 和 `ahk_commands.ahk`。

## 1. source of truth

| 主题 | 实现文件 | 关键入口 |
|------|----------|----------|
| AHK 执行端 | `hold_server_extended.ahk` | `WM_COPYDATA`、`ProcessQueue`、`ExecuteAction`、`RegisterHook` |
| 命令 ID | `torchlight_assistant/config/ahk_commands.py`、`ahk_commands.ahk` | `CMD_*` |
| Python 命令边界 | `torchlight_assistant/core/ahk_command_sender.py` | `set_accepting_actions`、`reset_runtime`、`set_runtime_owner` |
| Python 输入 API | `torchlight_assistant/core/ahk_input_handler.py` | `set_skill_hold_keys`、`set_stationary_mode`、`set_force_move_state` |
| 状态机 | `torchlight_assistant/core/macro_engine.py` | `_on_state_enter`、`_rollback_failed_state_entry` |
| AHK 事件接收 | `torchlight_assistant/core/ahk_event_filter.py`、`signal_bridge.py`、`ahk_input_handler.py` | 全局 `QAbstractNativeEventFilter` + `Qt.QueuedConnection`；`_unwrap_ahk_event()` 使用 `RECENT_AHK_EVENT_LIMIT` |
| 行为契约测试 | `tests/test_ahk_event_transport.py`、`test_ahk_queue_throughput.py`、`test_ahk_transient_press.py`、`test_macro_engine_state_safety.py` | 真实 AHK harness 与 Python 回归测试 |

`docs/AHK_COMPLETE_ARCHITECTURE.md` 是 2025 年的历史设计稿，不是当前规范；当前实现以本文和 `wiki/` 为准。

## 2. 进程和状态

Python 负责捕获、检测、条件判断和决策；AHK 负责 Hook、队列、按键边沿、持键账本和 WM_COPYDATA 事件。主状态只有四个：

```text
STOPPED → READY → RUNNING ↔ PAUSED
   ↑          └──────────────┘
   └──────── F8 从任意非 STOPPED 停止
```

- F8、F7、F9 是 AHK 永久根热键；动态 Hook 清理不会注销它们。Z 只在 READY 后注册。
- F8 主模式进入 READY 时先发送 `arm_main`，AHK 保持关闸并清场；Hook、目标窗口、捕获和 OCR 准备成功后才开闸。
- F7 洗练和 F9 寻路拥有独立的 `owner:epoch`。三种运行模式不能同时持有输入闸门。
- PAUSED 和 STOPPED 都先关闭运行时闸门。PAUSED 会关闭 AHK 原地模式并保留 Python 的期望值，恢复 RUNNING 时重新同步；恢复入口失败时回滚也会再次关闭原地模式。

## 3. 运行时闸门和世代

`RuntimeAcceptingActions` 是 AHK 端唯一的输入生产闸门。`CMD_SET_ACCEPTING_ACTIONS=false` 在同一条 WM_COPYDATA 消息内执行 `ClearQueue(-1)`、`StopMacro()`、释放临时/持久/技能/管理持键，并拒绝新的队列、宏、管理键和非空持键声明。关闸期间 `CMD_ENQUEUE`（包括 release/up）仍被拒绝；只有清场、停宏、空持键集合和 reset 在 AHK 内部生成释放动作并作为安全清理路径执行。

`RuntimeOwner`、`RuntimeOwnerEpoch` 和各 owner 的 tombstone 防止旧的 F8/F7/F9 回调重新打开新运行。`RuntimeInputGeneration` 由完整清场递增；`StartTransientPress` 在每个 down 边沿、账本登记前后都复核闸门和世代。若 WM_COPYDATA 重入恰好发生在 down 与登记之间，它按逆序补发 up，不会把旧 press 带入下一轮运行。

## 4. WM_COPYDATA 协议

Python → AHK 使用 `hold_client.send_ahk_cmd_ex()` 和 `SendMessageTimeoutW`，单条命令预算为 500ms。返回值为 `1=成功`、`2=业务拒绝`、`0=未处理/窗口过程默认值`。超时进入传输熔断；冷却 2 秒后启动尝试只串行发送 PING，不重放超时业务命令。STOPPED 的 `reset_runtime` 和关闭类命令使用 force 路径。

当前命令 ID：

| ID | 命令 | 语义 |
|----|------|------|
| 1–9 | PING、SET_TARGET、ACTIVATE、ENQUEUE、CLEAR_QUEUE、PAUSE、RESUME、HOOK_REGISTER、HOOK_UNREGISTER | 基础通信、队列和 Hook |
| 10–11 | SEND_KEY、SEND_SEQUENCE | 协议墓碑；不得复用，实际统一走 ENQUEUE |
| 12–21 | SET_STATIONARY、SET_FORCE_MOVE_KEY/STATE、SET_MANAGED_KEY_CONFIG、CLEAR_HOOKS、替换键、窗口状态、批量配置、发送模式、强制移动白名单 | 输入配置 |
| 22–25 | SET_MACRO_STEPS、START_MACRO、STOP_MACRO、SET_SKILL_HOLD_KEYS | AHK 宏和 TriggerMode=2 持键 |
| 26–29 | SET_ACCEPTING_ACTIONS、SHUTDOWN、RESET_RUNTIME、SET_RUNTIME_OWNER | 闸门、退出、原子清场和 owner 世代 |

AHK → Python 同样使用 WM_COPYDATA，但响应预算为 50ms。状态通道（monitor、special pause）合并为 latest-wins；可靠边沿进入上限 64 的 FIFO，普通边沿软上限 60，保留 4 个 F8 槽。`evt:<session>:<seq>:<event>` 信封让 Python 在最近 512 个 `(session, seq)` 窗口内去重，窗口外不再承诺去重。AHK 只入队并立即返回，Python 由全局 `AHKEventFilter` 解码后经 Qt 队列处理。

## 5. Hook、队列和动作

AHK 支持五种 Hook：`intercept`（`$` 拦截，F8/F7/F9/Z 等）、`priority`（`$` 拦截并映射管理键）、`special`（`~` 透传并抑制自动输入）、`monitor`（`~` 透传并跟踪强制移动）和 `block`（`$` 完全屏蔽）。滚轮没有可靠 up 边沿，不能注册为 special/monitor；intercept 滚轮按刻度发送，不参加自动重复去重。

四级队列为 emergency/high/normal/low。`ProcessQueue` 请求周期 15ms，Windows 上实测约 15.8ms，每 tick 最多执行一个动作，约 63 动作/秒。非紧急队列共享 `MAX_PENDING_ATOMS=16` 的原子预算；超过预算按 low→normal→high 丢最旧可丢项。普通动作等待 500ms 后过期丢弃； release、cleanup、notify、delay_clear、seqrun、紧急动作和在飞账本受保护。emergency 不设上限。

`sequence:` 是一个队列决策，不展开成多个队列项。每个 tick 推进一个原子；普通 `delay` 变成同队列 `notBefore`，只阻塞本队列。`delay_clear` 仅用于管理键，是全局独占窗口，并在窗口内清空非紧急队列但放行 HP/MP。

AHK 宏解释器独立于四级队列：5ms 轮询只推进步骤，步骤节奏必须写在显式 `delay` 中。`TriggerMode=2` 不经动作队列，Python 发送完整期望集合，AHK 用声明式账本差量同步，按配置顺序补按、按 LIFO 释放。

## 6. 输入路由和安全边界

- `direct` 使用 `SendInput` 写系统全局输入流。配置显式目标后，每个新 down/press 都要求目标仍在前台；切走时释放在飞全局键。目标为空才保留当前前台兼容语义。
- `control` 使用 `ControlSend`/`ControlClick` 沿 down 时捕获的目标配对 up，可后台投递。坐标点击始终要求显式目标、目标前台、坐标位于当前客户区半开边界内，并在实际执行前再次复核。
- 普通 press 使用非阻塞账本，`key_press_duration` 默认 10ms、合法范围 1–1000ms；坐标长按上限 5000ms。关闸清场直接释放账本；队列中的 release 只在开闸或特殊保护路径按规则执行。
- `block_mouse` 原地模式吞掉自动鼠标 down/click 但放行 up；`shift_modifier` 为自动按键增加 Shift。闸门关闭时拒绝迟到的激活命令，关闭原地模式的命令始终允许。
- 强制移动键按住时，非白名单、非 HP/MP 的自动按键替换为互动键（空配置回退 `f`）。AHK 的 `ForceMoveInteractionTick` 由 `FORCE_MOVE_INTERACTION_INTERVAL_MS=100` 驱动补发互动键；物理松开、暂停、管理键独占、特殊键保护期和 emergency 动作会停止或让路。停止/注销 Hook 会关闭该定时器。

## 7. 修改后的最小验证

```powershell
python -m pytest -q --basetemp .pytest-tmp
python tests/test_ahk_global_scope.py
git diff --check HEAD
```

如果系统临时目录不可枚举，使用仓库内临时目录运行 pytest，并在结束后删除它。修改 AHK 全局赋值、命令 ID、运行时闸门、队列或 Hook 时，必须同时运行对应的 AHK harness；不要只依赖 Python mock。
