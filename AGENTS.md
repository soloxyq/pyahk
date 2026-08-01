# AGENTS.md — pyahk 项目协作入口

> 这是各 AI 助手(Claude Code / Gemini CLI / Codex 等)与 pyahk 协作时**第一份要读的文档**。
> CLAUDE.md 和 GEMINI.md 都通过 `@AGENTS.md` 引用本文件,所以只需维护这一份。

---

## 1. 项目一句话定位

**pyahk** = Python(决策) + AutoHotkey v2(执行) 的 ARPG 游戏自动化辅助工具,
目标游戏:暗黑破坏神 4 / 流放之路 2 / Torchlight Infinite。
GUI 用 PySide6,屏幕捕获用自研 C++ DXGI 库。

## 2. 心智模型(必读)

```
┌────────── Python 进程 (PySide6 GUI) ──────────┐    ┌── AHK 子进程 ──┐
│                                                  │    │                │
│  C++ DXGI 捕获(零拷贝) ──► BorderFrameManager   │    │ hold_server_   │
│         │                       │                │    │ extended.ahk   │
│         ▼                       ▼                │    │                │
│  HSV 模板匹配 ──► SkillManager / ResourceManager │    │ 5 种 Hook 模式 │
│                              │                  │    │ 4 级优先队列   │
│                              ▼                  │    │ 异步 DelayUntil│
│                        MacroEngine (状态机)     │    │ SendInput      │
│                              │                  │    │                │
│         ┌───── EventBus(单例,递归保护) ─┐       │    │                │
│         ▼                              ▼        │    │                │
│  AHKInputHandler ◄─── ahk_event_filter (全局)   │    │                │
│         │                              ▲        │    │                │
│         └─Cmd──► AHKCommandSender ─────┼─WM────►│    │                │
│                                         │ COPY  │◄───┤                │
│                  Event ◄────────────────┘ DATA  │    │                │
└─────────────────────────────────────────────────┘    └────────────────┘
                                                       (独立 PID, 启动失败 = 拒绝运行)
```

**记忆口诀**:Python 看屏幕、做决策、发命令; AHK 拦截热键、排队、按键盘。
两边通过 WM_COPYDATA 双向通信(实测 0.069ms)。

## 3. 状态机 + 全局热键(速查)

### 状态机
`STOPPED ←→ READY ←→ RUNNING ←→ PAUSED` (`STOPPED → STOPPED` 为终止;严格的转换检查)

| 状态 | 含义 | 谁触发 |
|------|------|--------|
| STOPPED | 未启动,只有 F8/F7/F9 永久根热键监听 | 启动时 / F8 退出 |
| READY | 已注册业务热键,准备就绪 | F8 |
| RUNNING | 调度器跑技能/资源检测 | Z (从 READY) / Z (从 PAUSED) |
| PAUSED | 完全暂停(含 HP/MP) | Z (从 RUNNING) |

### 全局热键(F8/F7/F9 永久注册,Z 在 READY 时注册)

| 键 | 功能 | 注册时机 |
|----|------|---------|
| **F8** | STOPPED ↔ READY | **永久**,启动即注册 |
| **F7** | 启停装备词缀洗练(独立模式) | **永久**,handler 要求 STOPPED 状态才生效 |
| **F9** | 准备/停止自动寻路(独立模式;Z 才启动执行) | **永久**,handler 要求 STOPPED 状态才进入 pathfinding 准备态 |
| **Z** | RUNNING ↔ PAUSED 或 READY → RUNNING | 仅 READY 时注册,STOPPED 进入时清理 |

模式互斥:combat / pathfinding 通过 `MacroEngine._prepared_mode` (`none`/`combat`/`pathfinding`) 控制;洗练独立运行,通过 `affix_reroll_manager.status.is_running` 跟踪。F8/F7/F9 各自的 handler 之间互相检查这两个状态实现三模式硬互斥。

## 4. AI 协作约定(避免误判 / 踩坑)

### 4.1 这些"看似 BUG"实际是设计特性 ⚠️

| 行为 | 真相 |
|------|------|
| 强制移动键(默认 A)按住时,队列按键默认替换为 `f`,但 `force_move_passthrough_keys` 白名单键与 HP/MP 紧急药剂正常发送 | **特性**:边跑边互动(D4/PoE2 拾取/对话技巧),同时不阻止位移和救命药剂 |
| 管理键(如 E)按下时清空非紧急队列 | **特性**:保证管理键独占执行(E 通常映射闪避/强力技) |
| 特殊键(如 Space)激活时丢弃非紧急入队,但 HP/MP 紧急药剂与 `release:*` 释放动作仍然放行 | **特性**:闪避期间不发新技能,但救命药剂照常,且 TriggerMode=2 按住模式的 release 不会被 Space/managed delay 卡死 |
| PAUSED 状态完全停 HP/MP 检测 + 清所有队列 | **特性**:用户主动 Z 暂停 = 完全停下 |
| F8/F7/F9 不在 RegisteredHooks 记录中 | **特性**:三个永久根热键,清理动态 Hook 时不碰它们 |
| 同一个 key 不能同时出现在 special_keys / managed_keys | **特性**:跨类冲突会让后注册的 Hotkey 覆盖前者,Python 注册时检测重复并 LOG_ERROR 跳过后者 |
| 过载时丢弃**最旧**的待发动作(`queue_overload` 上报) | **特性**:执行上限实测约 63 动作/秒(`QUEUE_TICK_MS=15` → 实际 15.8ms × 每 tick 1 个动作),生产侧无背压。不丢的代价是延迟无限增长(实测 10 秒过载 → 打出去的是 10 秒前的决策)。深度上限 16 ≈ 延迟上限 250ms。`release:`/`cleanup:`/`delay_clear:`、紧急队列、**最新到达的动作**和 `sequence:` 展开**永不丢弃**。见 wiki/02 "吞吐预算" |
| `QUEUE_TICK_MS` 是 15 而不是 20 | **特性**:Windows 消息定时器粒度 ~15.6ms,`SetTimer` 向上凑整 —— 请求 20ms 实际是 31.6ms(吞吐腰斩到 31.7/s),请求 15ms 才是 15.8ms(63/s)。改回 20 会让 `last.json`/`d4灵巫.json` 等现成配置永久过载。实测表见 `hold_server_extended.ahk` 中 `QUEUE_TICK_MS` 处 |

**反模式**:看到这些不要急着报 BUG,先读 `wiki/02-架构与通信.md` 的"设计意图"段。

### 4.2 WSL 编辑陷阱 ⚠️

**症状**:你在 WSL 里用 Edit 工具改 `/mnt/e/repogit/pyahk/` 下的文件,git diff 显示 99 个文件 3.6 万行变化。

**原因**:WSL 在 NTFS 上保存会自动把 LF 改成 CRLF,与 HEAD 的 LF 不一致。

**防护**:仓库已加 `.gitattributes` 强制 `* text=auto eol=lf`。但你**编辑后仍要主动验证**:
```bash
file <path>                        # 应输出 LF, 不是 CRLF
git diff --check HEAD -- <path>    # 应无 trailing whitespace 警告
```
若发现 CRLF,用 Python 转换最稳(sed 在 WSL 偶尔静默失败):
```python
with open(f, 'rb') as fp: data = fp.read()
with open(f, 'wb') as fp: fp.write(data.replace(b'\r\n', b'\n'))
```

### 4.3 AHK v2 作用域陷阱 ⚠️

**规则**:AHK v2 函数内**对一个变量赋值**(任意位置)就会自动定为 local,**除非函数顶部 `global` 声明**。

**踩坑案例**:`ClearQueue` 内 `EmergencyQueue := []` 没声明 global → 创建空局部数组 → 全局队列里旧动作残留 → PAUSED 看似清了实则没清。

**修复方法**:任何函数对全局变量赋值时,函数顶部必须列出 global 声明:
```ahk
ProcessQueue() {
    global DelayUntil, DelayClearOthers, TotalQueueCount, QueueCounts
    global EmergencyQueue, HighQueue, NormalQueue, LowQueue
    global QueueStats, IsPaused, SpecialKeysPaused
    ; ...
}
```
**验证**:`python tests/test_ahk_global_scope.py` —— 全量扫描"赋值了全局却没声明 global"的函数(不是硬编码名单)。

### 4.4 不存在的文件 ⚠️

- `main_special.py` —— **已删除**(2025.10.16 输入系统重构)。文档曾引用,已全部清理。如需"特定游戏简化版",基于 AHK 架构重写,不要试图找原文件。
- `InputHandler` (Python 类) —— **已删除**,被 `AHKInputHandler` 替代。
- `pynput` —— 核心输入执行**不再依赖** pynput(已迁移到 AHK 子进程)。但 `requirements.txt` 仍含 `pynput>=1.7.6`,因为 GUI 的 `priority_keys_widget.py` 用它做"按住录制键名"的便捷输入(失败时回退到手动输入框)。pynput 缺失不影响主功能。

### 4.5 按键命名约定 ⚠️

内部存储、JSON 配置、Python→AHK 协议统一使用 **AHK 标准按键名**。鼠标键必须写 `LButton` / `RButton` / `MButton`,不要保存成 `left_mouse` / `right_mouse` / `middle_mouse`。

GUI 可以接受 `right_mouse`、`leftclick`、`mouse_right` 这类别名作为输入兼容,但保存配置前必须归一化成 AHK 标准名。看到 `priority_keys` 里的 `target` 或 key 被改成下划线别名时,应按 BUG 处理。

## 5. wiki 索引

| 文档 | 主题 | 何时读 |
|------|------|--------|
| [wiki/01-快速入门](wiki/01-项目概述与快速入门.md) | clone → install → 启动 | 第一次接触项目 |
| [wiki/02-架构与通信](wiki/02-架构与核心概念.md) | 状态机 / WM_COPYDATA / 队列 / Hook 模式 | 改核心模块前 |
| [wiki/03-模块与API](wiki/03-功能模块与API.md) | 各模块的核心方法签名与职责 | 写新功能前 |
| [wiki/04-配置JSON Schema](wiki/04-配置与条件系统.md) | 配置文件字段 + 枚举值 | 改配置 UI / 加新功能开关 |
| [wiki/05-图像与性能](wiki/05-图像捕获与性能.md) | DXGI 捕获 / HSV 匹配 / 性能数据 | 优化检测精度/速度 |
| [wiki/06-调试与排查](wiki/06-调试、部署与故障排查.md) | 日志在哪 / 常见 BUG 定位路径 | 出问题时 |

补充:`docs/AHK_COMPLETE_ARCHITECTURE.md` 是 2025.10 输入系统重构的设计文档(历史档案),不需要修改但可作为深度参考。

## 6. 关键文件路径速查

```
pyahk/
├── main.py                                  # 唯一入口 (PySide6 GUI)
├── hold_server_extended.ahk                 # AHK 服务器(所有键盘逻辑)
├── hold_client.py                           # WM_COPYDATA 客户端(Python→AHK)
├── ahk_commands.ahk                         # AHK 端命令 ID 定义
├── default.json / d4.json / poe2_*.json     # 游戏配置
├── .gitattributes                           # 强制 LF 行尾
│
├── torchlight_assistant/
│   ├── core/
│   │   ├── macro_engine.py                  # 状态机协调中枢
│   │   ├── skill_manager.py                 # 技能调度
│   │   ├── resource_manager.py              # HP/MP 检测
│   │   ├── ahk_input_handler.py             # AHK 子进程封装
│   │   ├── ahk_command_sender.py            # 命令发送
│   │   ├── ahk_event_filter.py              # 全局 WM_COPYDATA 接收(QAbstractNativeEventFilter)
│   │   ├── signal_bridge.py                 # Qt Signal 跨线程桥
│   │   ├── event_bus.py                     # 单例事件总线
│   │   ├── unified_scheduler.py             # heapq + monotonic 调度
│   │   ├── pathfinding_manager.py           # 自动寻路 (初步实现)
│   │   ├── simple_affix_reroll_manager.py   # 装备洗练 (初步实现)
│   │   └── states.py                        # MacroState 枚举
│   ├── config/
│   │   ├── ahk_commands.py                  # CMD_* ID 与 AHK 端同步(20 个)
│   │   └── ahk_config.py                    # AHK 启动配置
│   ├── gui/                                 # PySide6 界面
│   └── utils/
│       ├── border_frame_manager.py          # 帧管理 + HSV 匹配
│       ├── native_graphics_capture_manager.py  # DXGI 捕获 Python 包装
│       └── ...
│
└── native_capture/                          # C++ DXGI 库源码
```

## 7. 当前分支与上下文

- 当前分支:`feature/ahk-input-system`(2025.10 输入系统重构后,尚未合回 master)
- master 分支较旧(仍是 pynput 时代),改输入相关代码时**绝不要参考 master**
- 提交风格:中英混合 `feat(scope): 描述`, 常带 emoji
- 用户活跃地用中文写 commit/对话
