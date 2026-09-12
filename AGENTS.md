# AGENTS.md — pyahk 协作入口
本文件保持 64 行以内；CLAUDE.md / GEMINI.md 引用它，详细设计与配置以 wiki 为准。

## 项目与环境
- Python 决策 + AutoHotkey v2 执行；PySide6 GUI，自研 C++ DXGI 捕获。
- 目标游戏：D4 / PoE2 / Torchlight Infinite；WM_COPYDATA 双向通信。
- 工作目录：E:\repogit\pyahk；使用 PowerShell，搜索优先 rg。
- 当前分支 feature/ahk-input-system；master 仍为旧 pynput 架构，不作为输入实现参考。

## 提交与编辑纪律
- 只有用户对当前批次明确要求，才允许 git commit / git push；历史授权不延续。
- 修改后完成相关测试、git diff --check 并汇报；保留用户已有改动。
- 用 apply_patch 编辑；所有文本保持 LF，避免整文件 CRLF 噪声。
- 不使用 git reset --hard、git checkout -- 等破坏性命令恢复工作区。
- docs/AHK_COMPLETE_ARCHITECTURE.md 是历史档案，不需要随当前实现更新。

## 状态与安全边界
- STOPPED → READY → RUNNING ↔ PAUSED；后三态均可直接 STOPPED。
- F8 准备 combat / 停止；F9 准备或停止 pathfinding；Z 开始或暂停/恢复。
- F7 启停独立洗练；三模式互斥。F8/F7/F9 永久监听，Z 仅 READY 后注册。
- READY 两阶段：AHK 初始关闸，arm_main + owner/epoch → Hook/捕获/OCR 准备 → 开闸。
- STOPPED 使用单条 reset_runtime 原子关闸、清队、停宏、释放持键和注销动态 Hook。
- 物理 F8 本地先止血，再可靠通知 Python；迟到回调不得停止或污染新的运行世代。
- Python→AHK 超时为 500ms；不重放超时业务命令，安全清理可强制发送。
- 熔断冷却 2 秒后，F8/F7/F9 启动尝试以串行 PING 探测恢复；停止不受限制。
- AHK→Python 可靠 FIFO 上限 64，普通边沿软上限 60，F8 保留 4 槽；接收后 Qt 排队处理。
- direct 显式目标失焦时拒发并释放持键；control 必须有目标，up 沿原 down 路径配对。
- 畸形目标不能退化成前台窗口；坐标点击必须在明确目标的前台客户区内。
- DXGI 帧为 BGRA；检测坐标为虚拟桌面物理像素，可为零或负数，按帧原点换算。
- BorderFrameManager 返回锁内复制的只读快照；旧 worker 不得使用新世代状态发键。

## 设计特性，勿误报
- 强制移动期间，非白名单/非紧急自动按键替换为 f（可配置）；HP/MP 救命药剂保留。
- 管理键清非紧急队列并独占执行；特殊键透传并抑制普通输入，仍放行药剂与释放动作。
- special_key_resume_delay_ms 只延迟自动输入恢复，物理 key-up 始终立即透传。
- PAUSED 完全停止 HP/MP；F8/F7/F9 不属于可清理的 RegisteredHooks。
- 滚轮无可靠 up，禁止 special/monitor；intercept 滚轮逐刻度发送，不参与自动重复去重。
- 队列 QUEUE_TICK_MS=15；Windows timer 量化后约 63 动作/秒，不能按请求周期直接估算。
- 全局普通预算 MAX_PENDING_ATOMS=16；过载按 low→normal→high 丢最旧可丢项。
- ≥500ms 的旧普通动作过期丢弃；release/cleanup/delay_clear/seqrun、紧急与在飞动作受保护。
- sequence 是单个决策，开打后不可裁断；普通 delay 只挡本队列，delay_clear 才是全局独占。
- 宏 5ms 是轮询预算，步骤节奏靠显式 delay；key_press_duration 控制 press 的 down→up。
- 按键存储与协议统一 AHK 标准名（LButton/RButton/MButton）；GUI 别名保存前归一化。
- AHK 函数只要赋值全局变量，顶部必须声明 global，避免意外创建 local。
- main_special.py、旧 InputHandler 已删除；pynput 仅用于 GUI 录键便捷功能。

## 文档与关键路径
- [wiki/01](wiki/01-项目概述与快速入门.md)：安装、启动、PowerShell 命令。
- [wiki/02](wiki/02-架构与核心概念.md)：状态机、通信、队列与 Hook；改核心前必读。
- [wiki/03](wiki/03-功能模块与API.md)：模块职责、API 与调用边界。
- [wiki/04](wiki/04-配置与条件系统.md)：JSON Schema、默认值与检测语义；改配置前必读。
- [wiki/05](wiki/05-图像捕获与性能.md)：DXGI、DPI、HSV、快照与性能数据。
- [wiki/06](wiki/06-调试、部署与故障排查.md)：日志、测试、部署和故障排查。
- 入口 main.py；传输 hold_client.py / core/ahk_command_sender.py / core/ahk_event_filter.py。
- 状态调度在 torchlight_assistant/core/；GUI 在 gui/；捕获与检测工具在 utils/。
- AHK 执行在 hold_server_extended.ahk；命令 ID 与 config/ahk_commands.py、ahk_commands.ahk 同步。
- native_capture/ 保存 C++ 源码、wrapper 和 DLL；改 ABI 后三者须一致。
- 验证：python -m pytest -q；python tests/test_ahk_global_scope.py；git diff --check HEAD。
