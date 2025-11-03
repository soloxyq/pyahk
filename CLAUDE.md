# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**pyahk** 是一个现代化的桌面游戏自动化辅助工具，基于 Python + AutoHotkey 架构，专为 ARPG 游戏（如《暗黑破坏神 4》、《流放之路 2》）设计。核心特色是采用事件驱动架构和零拷贝捕获技术，提供毫秒级响应的游戏自动化功能。

### 技术架构核心

- **事件驱动架构**：所有组件通过中央事件总线 (EventBus) 进行通信，完全解耦
- **四状态机管理**：STOPPED → READY → RUNNING → PAUSED，严格的状态转换控制
- **多语言混合**：Python 决策层 + AHK 执行层 + C++ 底层捕获
- **模块化设计**：清晰的功能分离，易于维护和扩展

## 开发环境与依赖

### 核心依赖 (requirements.txt)
```
PySide6>=6.5.0        # GUI框架
psutil>=5.9.0         # 系统进程监控
pynput>=1.7.6         # 键盘鼠标输入控制
Pillow>=9.0.0         # 图像处理
mss>=9.0.1           # 高性能截图
numpy>=1.20.0         # 数值计算
opencv-python>=4.5.0  # 计算机视觉
pywin32>=306          # Windows API
paddleocr==2.7.3      # OCR文字识别（固定版本）
```

### 系统要求
- **操作系统**：Windows 10/11
- **Python版本**：3.7+
- **编译环境**：Visual Studio Build Tools（编译 C++ 原生库时需要）

## 项目结构

```
pyahk/
├── torchlight_assistant/           # 主包
│   ├── core/                       # 核心业务逻辑
│   │   ├── macro_engine.py        # 主控制器，四状态机管理
│   │   ├── event_bus.py           # 事件总线（单例模式）
│   │   ├── unified_scheduler.py   # 统一调度器（monotonic时间）
│   │   ├── ahk_input_handler.py   # AHK输入处理器
│   │   ├── ahk_command_sender.py  # AHK命令发送器
│   │   ├── skill_manager.py       # 技能管理
│   │   ├── resource_manager.py    # 资源检测（HP/MP）
│   │   ├── pathfinding_manager.py # 自动寻路
│   │   ├── simple_affix_reroll_manager.py # 装备洗练
│   │   └── states.py              # 状态枚举
│   ├── config/                     # 配置定义
│   │   ├── ahk_commands.py        # AHK命令协议（19个命令）
│   │   └── ahk_config.py          # AHK系统配置
│   ├── gui/                        # 用户界面
│   │   ├── main_window.py         # 主窗口
│   │   ├── debug_osd_window.py    # 调试覆盖窗口
│   │   ├── resource_widgets.py    # 资源配置界面
│   │   ├── skill_config_widget.py # 技能配置界面
│   │   ├── priority_keys_widget.py # 优先级按键配置
│   │   └── color_analysis_tools.py # 颜色分析工具
│   └── utils/                      # 工具类
│       ├── native_graphics_capture_manager.py # DXGI捕获
│       ├── border_frame_manager.py # 边框管理
│       ├── paddle_ocr_manager.py   # PaddleOCR封装
│       ├── tesseract_ocr_manager.py # Tesseract OCR
│       └── window_utils.py         # 窗口工具
├── native_capture/                 # C++原生捕获库
│   ├── capture_lib.cpp            # DXGI捕获实现
│   ├── capture_lib.h              # 头文件
│   ├── python_wrapper.py          # Python包装器
│   └── build.bat                  # 编译脚本
├── hold_server_extended.ahk       # AHK服务器（四级队列+Hook管理）
├── hold_client.py                 # WM_COPYDATA客户端
├── main.py                        # GUI版本入口
├── default.json                   # 默认配置
└── *.json                         # 各种游戏配置文件
```

## 核心架构组件

### 1. EventBus (事件总线)
- **位置**：`torchlight_assistant/core/event_bus.py`
- **功能**：全局单例、线程安全、递归检测防护
- **特点**：发布/订阅模式、线程池异步回调、性能监控

### 2. MacroEngine (主控制器)
- **位置**：`torchlight_assistant/core/macro_engine.py`
- **功能**：四状态机管理、模式互斥、依赖注入
- **状态转换**：STOPPED ↔ READY ↔ RUNNING ↔ PAUSED

### 3. UnifiedScheduler (统一调度器)
- **位置**：`torchlight_assistant/core/unified_scheduler.py`
- **功能**：基于heapq的时间优先队列、亚毫秒级定时精度
- **特点**：智能暂停/恢复、任务动态管理、优先级支持

### 4. AHK输入系统
- **Python端**：`torchlight_assistant/core/ahk_input_handler.py`
- **AHK端**：`hold_server_extended.ahk`
- **通信**：`hold_client.py` (WM_COPYDATA)
- **命令协议**：`torchlight_assistant/config/ahk_commands.py`

## 常用开发任务

### 启动程序
```bash
# 启动GUI版本（推荐）
python main.py

# 编译C++原生库（如需要）
cd native_capture && build.bat
```

### 配置管理
- **默认配置**：`default.json`
- **游戏配置**：`*.json`（如 `d4.json`, `tors10.json`）
- **配置加载**：通过 `ConfigManager` 统一管理，支持热重载

### 热键操作
- **F8**：主控键 - 在STOPPED和READY状态间切换
- **F7**：洗练键 - 独立控制装备词缀洗练模式
- **F9**：寻路键 - 独立控制自动寻路模式
- **Z**：执行/暂停键 - 在READY状态下启动执行

### 调试工具
- **调试OSD**：`torchlight_assistant/gui/debug_osd_window.py`
- **颜色分析**：`torchlight_assistant/gui/color_analysis_tools.py`
- **日志输出**：`torchlight_assistant/utils/debug_log.py`

## 关键技术特性

### 1. 零拷贝捕获技术
- **实现**：基于DXGI Desktop Duplication API
- **优势**：C++底层实现 + Python零拷贝映射，毫秒级响应
- **位置**：`native_capture/capture_lib.cpp`

### 2. 四级优先队列
| 优先级 | 名称 | 数值 | 用例 |
|--------|------|------|------|
| emergency | 紧急 | 0 | HP/MP药剂、前置延迟 |
| high | 高 | 1 | 优先级按键、核心输出 |
| normal | 普通 | 2 | 常规技能、Buff |
| low | 低 | 3 | UI/拾取/辅助 |

### 3. HSV颜色检测
- **性能**：0.3ms检测，比OCR快82-803倍
- **准确率**：96%误差<5%
- **格式**：容差值 + 多种颜色配置

### 4. 选择性事件拦截
- **special_keys**：仅监控，不拦截（如space键）
- **managed_keys**：完全接管（如e、right_mouse）
- **前置延迟**：确保状态同步和按键可靠性

## AHK命令协议

### 核心命令（19个）
- **CMD_PING**(1)：测试连接
- **CMD_SET_TARGET**(2)：设置目标窗口
- **CMD_ENQUEUE**(4)：添加按键到队列
- **CMD_SEND_KEY**(10)：发送单个按键
- **CMD_SEND_SEQUENCE**(11)：发送按键序列
- **CMD_BATCH_UPDATE_CONFIG**(19)：批量配置更新

### 通信机制
- **协议**：WM_COPYDATA
- **数据格式**：UTF-8编码
- **性能优化**：窗口句柄缓存

## 开发指南

### 状态机设计
```python
# 状态转换规则
VALID_TRANSITIONS = {
    MacroState.STOPPED: [MacroState.READY],
    MacroState.READY: [MacroState.RUNNING, MacroState.STOPPED],
    MacroState.RUNNING: [MacroState.PAUSED, MacroState.STOPPED],
    MacroState.PAUSED: [MacroState.RUNNING, MacroState.STOPPED],
}
```

### 事件驱动模式
```python
# 订阅事件
event_bus.subscribe("event_name", handler_function)

# 发布事件
event_bus.publish("event_name", *args, **kwargs)
```

### 任务调度
```python
# 添加周期任务
scheduler.add_task(func, interval_ms, priority=1)

# 智能暂停/恢复
scheduler.pause_all()  # 优先级按键激活时
scheduler.resume_all() # 优先级按键释放时
```

## 性能优化要点

### 1. 优先级按键优化
- 激活时自动暂停所有计算密集任务
- 零功效计算：完全停止帧捕获、像素分析
- 紧急操作（如HP药剂）永不被暂停

### 2. 图像检测优化
- **矩形检测**：0.3ms，适用于矩形资源条
- **圆形检测**：~5ms，适用于球形资源
- **OCR识别**：25-241ms，备用方案

### 3. 内存管理
- C++双缓冲 + 原子指针
- Python零拷贝映射
- 窗口句柄缓存

## 配置文件说明

### 技能配置 (skills)
```json
{
  "Skill1": {
    "Enabled": false,
    "Key": "LButton",
    "Priority": true,
    "Timer": 150,
    "TriggerMode": 1,
    "CooldownCoordX": 815,
    "CooldownCoordY": 1000,
    "CooldownSize": 12
  }
}
```

### 优先级按键配置
```json
{
  "priority_keys": {
    "enabled": true,
    "special_keys": ["space"],
    "managed_keys": {
      "right_mouse": {"target": "right_mouse", "delay": 50},
      "e": {"target": "0", "delay": 30}
    }
  }
}
```

### 资源检测配置
```json
{
  "hp_config": {
    "detection_method": "rect",
    "region": {"x": 100, "y": 950, "width": 200, "height": 30},
    "colors": [
      "10,20,20",      # 容差：H±10, S±20, V±20
      "157,75,29",     # 红色血量
      "40,84,48"       # 绿色血量（备用）
    ],
    "threshold": 60
  }
}
```

## 故障排查

### 常见问题
1. **AHK连接失败**：检查窗口标题是否正确
2. **权限不足**：以管理员权限运行
3. **捕获失败**：检查DXGI支持
4. **热键冲突**：修改配置中的按键设置

### 调试工具
- 启用DEBUG模式（.env中设置DEBUG=true）
- 使用调试OSD窗口实时查看状态
- 查看日志文件定位问题

## 项目文档导航

- **README.md**：项目介绍和快速入门
- **GEMINI.md**：AI协助指南和更新记录
- **WIKI/**：详细文档目录
  - **01-项目概述与快速入门.md**
  - **02-架构与核心概念.md**
  - **03-功能模块与API.md**
  - **04-配置与条件系统.md**
  - **05-图像捕获与性能.md**
  - **06-调试、部署与故障排查.md**
- **docs/AHK_COMPLETE_ARCHITECTURE.md**：AHK架构完整文档

## 注意事项

1. **线程安全**：核心组件使用线程锁保护，注意死锁风险
2. **内存管理**：C++库和Python之间注意引用计数
3. **状态一致性**：状态机转换时确保所有组件同步
4. **性能监控**：定期检查事件队列和处理延迟
5. **兼容性**：不同游戏需要不同的配置文件

