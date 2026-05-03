"""重构后的MacroEngine - 专注于状态管理和事件协调"""

import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from .config_manager import ConfigManager
from .ahk_input_handler import AHKInputHandler
from .skill_manager import SkillManager
from .event_bus import event_bus
from .states import MacroState
from ..utils.border_frame_manager import BorderFrameManager
from ..utils.sound_manager import SoundManager
from .pathfinding_manager import PathfindingManager
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO


class MacroEngine:
    """重构后的宏引擎 - 专注于状态管理和事件协调"""

    DEFAULT_CONFIG_FILE = "default.json"
    APP_STATE_FILE = Path(__file__).resolve().parents[2] / ".pyahk_state.json"

    VALID_TRANSITIONS = {
        MacroState.STOPPED: [MacroState.READY],
        MacroState.READY: [MacroState.RUNNING, MacroState.STOPPED],
        MacroState.RUNNING: [MacroState.PAUSED, MacroState.STOPPED],
        MacroState.PAUSED: [MacroState.RUNNING, MacroState.STOPPED],
    }

    def __init__(self, sound_manager=None, config_file: str = DEFAULT_CONFIG_FILE):
        self._state = MacroState.STOPPED
        self._prepared_mode = "none"  # 'none', 'combat', 'pathfinding'
        self._state_lock = threading.RLock()
        self._transition_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_done = False
        self._skills_config: Dict[str, Any] = {}
        self._global_config: Dict[str, Any] = {}
        self.current_config_file = self._resolve_initial_config_file(config_file)
        self._is_debug_mode_active = (
            False  # 跟踪当前是否处于调试模式（由配置和状态决定）
        )

        # 原地模式状态（切换模式）
        self._stationary_mode_active = False
        # 强制移动状态（按住模式）
        self._force_move_active = False
        # 当前配置的按键
        self._current_stationary_key = ""
        self._current_force_move_key = ""

        self.config_manager = ConfigManager()

        # Initialize DebugDisplayManager first, as others depend on it
        from .debug_display_manager import DebugDisplayManager
        from .unified_scheduler import (
            UnifiedScheduler as DebugScheduler,
        )  # Alias to avoid conflict

        debug_scheduler = DebugScheduler()
        self.debug_display_manager = DebugDisplayManager(event_bus, debug_scheduler)
        # 不自动启动DebugScheduler，只在需要时启动
        self.debug_scheduler = debug_scheduler

        # 使用AHK输入处理器
        self.input_handler = AHKInputHandler(
            event_bus=event_bus, debug_display_manager=self.debug_display_manager
        )
        self.border_manager = BorderFrameManager()
        self.sound_manager = sound_manager or SoundManager()

        # 初始化ResourceManager
        from .resource_manager import ResourceManager

        self.resource_manager = ResourceManager(
            self.border_manager,
            self.input_handler,
            debug_display_manager=self.debug_display_manager,
        )

        # Pass debug_display_manager to SkillManager
        self.skill_manager = SkillManager(
            self.input_handler,
            self,
            self.border_manager,
            self.resource_manager,
            debug_display_manager=self.debug_display_manager,
        )

        from .simple_affix_reroll_manager import SimpleAffixRerollManager

        self.affix_reroll_manager = SimpleAffixRerollManager(
            self.border_manager, self.input_handler
        )
        self.pathfinding_manager = PathfindingManager(
            self.border_manager, self.input_handler
        )

        self._setup_event_subscriptions()
        self.load_config(self.current_config_file)  # 先加载配置

        # 注册 F8/F7/F9 永久根热键
        self._setup_primary_hotkey()

    def _resolve_initial_config_file(self, fallback_config_file: str) -> str:
        """启动时优先加载上次成功使用的配置文件。"""
        if fallback_config_file != self.DEFAULT_CONFIG_FILE:
            return fallback_config_file

        last_config_file = self._load_last_config_file()
        if not last_config_file:
            return fallback_config_file

        last_path = Path(last_config_file)
        try:
            if last_path.exists() and last_path.stat().st_size > 0:
                LOG_INFO(f"[配置加载] 使用上次配置文件: {last_config_file}")
                return last_config_file
        except OSError as e:
            LOG_ERROR(f"[配置加载] 检查上次配置文件失败: {last_config_file}, {e}")

        LOG_INFO(
            f"[配置加载] 上次配置文件不可用: {last_config_file}, 回退到 {fallback_config_file}"
        )
        return fallback_config_file

    def _load_last_config_file(self) -> str:
        """读取本机应用状态中的最后配置文件路径。"""
        if not self.APP_STATE_FILE.exists():
            return ""

        try:
            with open(self.APP_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            return str(state.get("last_config_file", "")).strip()
        except (OSError, json.JSONDecodeError) as e:
            LOG_ERROR(f"[配置加载] 读取应用状态失败: {e}")
            return ""

    def _remember_config_file(self, config_file: str):
        """记录最后成功加载/保存的配置文件,供下次启动恢复。"""
        if not config_file:
            return

        self.current_config_file = config_file
        try:
            with open(self.APP_STATE_FILE, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    {"last_config_file": config_file},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
                f.write("\n")
        except OSError as e:
            LOG_ERROR(f"[配置加载] 保存应用状态失败: {e}")

    def _setup_event_subscriptions(self):
        event_bus.subscribe("ui:load_config_requested", self.load_config)
        event_bus.subscribe("ui:save_full_config_requested", self.save_full_config)
        event_bus.subscribe(
            "ui:sync_and_toggle_state_requested", self._handle_f8_press
        )  # F8 UI button
        event_bus.subscribe(
            "ui:request_current_config", self._handle_ui_request_current_config
        )
        event_bus.subscribe("hotkey:z_press", self._handle_z_press)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

    def _setup_primary_hotkey(self):
        """设置永久根热键 (F8/F7/F9)

        F8: STOPPED ↔ READY 主控键
        F7: 装备词缀洗练 (要求 STOPPED 状态才能启动,故必须常驻)
        F9: 自动寻路准备/停止 (同上,handler 在 STOPPED 进入 pathfinding 模式)

        Z 不在此列 —— Z 仅在 RUNNING/PAUSED/READY 有意义,延迟到 _register_secondary_hotkeys 注册。
        """
        try:
            LOG_INFO("[热键管理] 注册永久根热键 (F8/F7/F9)...")

            for key in ("F8", "F7", "F9"):
                result = self.input_handler.register_root_hook(key)
                if result:
                    LOG_INFO(f"[热键管理] [OK] 永久根热键注册成功: {key}")
                else:
                    LOG_ERROR(f"[热键管理] [FAIL] 永久根热键注册失败: {key}")

            # 订阅AHK拦截事件（系统热键）
            event_bus.subscribe("intercept_key_down", self._handle_ahk_intercept_key)

            # 🎯 订阅新的按键事件系统
            # 特殊按键事件（如space）- 持续状态检测
            event_bus.subscribe("special_key_down", self._handle_ahk_special_key_down)
            event_bus.subscribe("special_key_up", self._handle_ahk_special_key_up)
            event_bus.subscribe("special_key_pause", self._handle_ahk_special_key_pause)

            # 管理按键事件（如RButton/e）- 拦截+延迟+映射
            event_bus.subscribe("managed_key_down", self._handle_ahk_managed_key_down)
            event_bus.subscribe("managed_key_complete", self._handle_ahk_managed_key_complete)

            # 兼容旧的优先级事件（逐步迁移）
            event_bus.subscribe("priority_key_down", self._handle_ahk_priority_key_down)
            event_bus.subscribe("priority_key_up", self._handle_ahk_priority_key_up)

            # 订阅AHK监控事件（交互键A等）
            event_bus.subscribe("monitor_key_down", self._handle_ahk_monitor_key_down)
            event_bus.subscribe("monitor_key_up", self._handle_ahk_monitor_key_up)

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册 F8/F7/F9 永久根热键时发生错误: {e}")

    def _register_secondary_hotkeys(self):
        """注册所有动态热键（在READY状态时调用）

        F7/F9 已改为永久根热键(_setup_primary_hotkey 处理),此处不再重复注册。
        """
        try:
            LOG_INFO("[热键管理] ========== 开始注册动态热键 ==========")

            # 注册执行/暂停键 (z) —— 仅在 READY/RUNNING/PAUSED 有意义,故是动态热键
            LOG_INFO("[热键管理] 准备注册执行/暂停键 (z)...")
            if self.input_handler.register_hook("z", "intercept"):
                LOG_INFO("[热键管理] [OK] 执行/暂停键 (z) 注册成功")
            else:
                LOG_ERROR("[热键管理] [FAIL] 执行/暂停键 (z) 注册失败")

            # 注册配置相关的动态热键（原地模式、交互模式等）
            LOG_INFO("[热键管理] 准备注册配置相关热键...")
            self._register_config_based_hotkeys()

            LOG_INFO("[热键管理] ========== 动态热键注册完成 ==========")

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册动态热键时发生错误: {e}")
            import traceback

            LOG_ERROR(f"[热键管理] 详细错误: {traceback.format_exc()}")

    def _register_config_based_hotkeys(self):
        """根据当前配置注册热键"""
        try:
            LOG_INFO("[热键管理] 开始注册配置相关热键...")

            # 获取当前配置
            stationary_config = self._global_config.get("stationary_mode_config", {})
            stationary_key = stationary_config.get("hotkey")
            LOG_INFO(f"[热键管理] 原地模式配置: {stationary_config}")
            LOG_INFO(f"[热键管理] 原地模式热键: {stationary_key}")

            # 注册原地模式热键
            if stationary_key:
                LOG_INFO(f"[热键管理] 准备注册原地模式热键: {stationary_key}")
                if self.input_handler.register_hook(stationary_key, "intercept"):
                    # 更新当前原地模式热键
                    self._current_stationary_key = stationary_key.lower()
                    LOG_INFO(f"[原地模式] 注册Hook成功: {stationary_key} (intercept)")
                    LOG_INFO(
                        f"[原地模式] 当前原地模式热键已设置为: {self._current_stationary_key}"
                    )
                else:
                    LOG_ERROR(f"[原地模式] 注册Hook失败: {stationary_key}")
            else:
                self._current_stationary_key = ""
                LOG_INFO("[热键管理] 未配置原地模式热键")

            # 注册强制移动键（monitor模式，不拦截但监控状态）
            force_move_key = stationary_config.get("force_move_hotkey")
            if force_move_key:
                LOG_INFO(f"[热键管理] 准备注册强制移动键: {force_move_key}")
                if self.input_handler.register_hook(force_move_key, "monitor"):
                    # 更新当前强制移动键
                    self._current_force_move_key = force_move_key.lower()
                    LOG_INFO(f"[强制移动键] 注册Hook成功: {force_move_key} (monitor)")
                    LOG_INFO(
                        f"[强制移动键] 当前强制移动键已设置为: {self._current_force_move_key}"
                    )
                else:
                    LOG_ERROR(f"[强制移动键] 注册Hook失败: {force_move_key}")
            else:
                self._current_force_move_key = ""
                LOG_INFO("[热键管理] 未配置强制移动键")

            # 注册管理按键配置
            priority_config = self._global_config.get("priority_keys", {})
            LOG_INFO(f"[热键管理] 优先级配置: {priority_config}")

            if priority_config.get("enabled", False):
                LOG_INFO("[热键管理] 优先级配置已启用")

                # 跟踪已注册的业务热键,防止 special/managed/protected 跨类冲突
                # 同一个 key 注册到多类会导致后注册的 Hotkey 覆盖前者,行为难排查
                registered_business_keys: set[str] = set()

                # 注册特殊按键（如space）- 使用AHK标准按键名
                special_keys = priority_config.get("special_keys", [])
                LOG_INFO(f"[热键管理] 特殊按键列表: {special_keys}")
                LOG_INFO(f"[热键管理] 特殊按键数量: {len(special_keys)}")
                for key in special_keys:
                    key_lower = (key or "").lower()
                    if key_lower in registered_business_keys:
                        LOG_ERROR(
                            f"[特殊按键] 跳过 '{key}': 已被其他类别注册,"
                            f"special/managed/protected 不应共用同一按键"
                        )
                        continue
                    LOG_INFO(f"[热键管理] 准备注册特殊按键: '{key}' (类型: {type(key).__name__})")
                    if self.input_handler.register_hook(key, "special"):
                        registered_business_keys.add(key_lower)
                        LOG_INFO(f"[特殊按键] 注册成功: {key} (special)")
                    else:
                        LOG_ERROR(f"[特殊按键] 注册失败: {key}")

                # 注册管理按键（如RButton/e键,程序代按）
                managed_keys = priority_config.get("managed_keys", {})
                LOG_INFO(f"[热键管理] 管理按键配置: {managed_keys}")
                for key, config in managed_keys.items():
                    key_lower = (key or "").lower()
                    if key_lower in registered_business_keys:
                        LOG_ERROR(
                            f"[管理按键] 跳过 '{key}': 已被其他类别注册,"
                            f"special/managed/protected 不应共用同一按键"
                        )
                        continue
                    LOG_INFO(f"[热键管理] 准备注册管理按键: {key}, 配置: {config}")
                    if self.input_handler.register_hook(key, "priority"):
                        registered_business_keys.add(key_lower)
                        target = config.get("target", key)
                        delay = config.get("delay", 0)
                        hold_ms = config.get("hold_ms", 0)
                        # 发送管理按键配置到AHK
                        self.input_handler.command_sender.set_managed_key_config(
                            key, target, delay, hold_ms
                        )
                        LOG_INFO(
                            f"[管理按键] 注册成功: {key} -> {target} "
                            f"(延迟: {delay}ms, 按住: {hold_ms}ms)"
                        )
                    else:
                        LOG_ERROR(f"[管理按键] 注册失败: {key}")

                # 注册保护按键（如c大招,真实按键直达游戏+程序让路）
                protected_keys = priority_config.get("protected_keys", {})
                LOG_INFO(f"[热键管理] 保护按键配置: {protected_keys}")
                for key, config in protected_keys.items():
                    key_lower = (key or "").lower()
                    if key_lower in registered_business_keys:
                        LOG_ERROR(
                            f"[保护按键] 跳过 '{key}': 已被其他类别注册,"
                            f"special/managed/protected 不应共用同一按键"
                        )
                        continue
                    LOG_INFO(f"[热键管理] 准备注册保护按键: {key}, 配置: {config}")
                    if self.input_handler.register_hook(key, "protected"):
                        registered_business_keys.add(key_lower)
                        release_delay = int(config.get("release_delay", 150))
                        self.input_handler.command_sender.set_protected_key_config(
                            key, release_delay
                        )
                        LOG_INFO(
                            f"[保护按键] 注册成功: {key} (释放延迟: {release_delay}ms)"
                        )
                    else:
                        LOG_ERROR(f"[保护按键] 注册失败: {key}")
            else:
                LOG_INFO("[热键管理] 优先级配置未启用")

            LOG_INFO("[热键管理] 配置相关热键注册完成")

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册配置相关热键时发生错误: {e}")
            import traceback

            LOG_ERROR(f"[热键管理] 详细错误: {traceback.format_exc()}")

    def _handle_ahk_intercept_key(self, key: str, **kwargs):
        """处理AHK拦截的系统热键（F8/F7/F9/Z）和原地模式按键"""
        key_lower = key.lower()
        LOG_INFO(f"[热键管理] 收到AHK拦截按键: {key}")

        if key_lower == "f8":
            self._handle_f8_press()
        elif key_lower == "f7":
            self._on_f7_key_press()
        elif key_lower == "f9":
            self._on_f9_key_press()
        elif key_lower == "z":
            self._on_z_key_press()
        elif key_lower == self._current_stationary_key:
            # 原地模式按键（X键）- 按一下切换状态
            self._on_stationary_key_press()
        else:
            LOG_INFO(f"[热键管理] 未处理的按键: {key}")

    def _handle_ahk_special_key_down(self, key: str):
        """处理特殊按键按下（如space）- 不拦截，持续状态检测"""
        LOG(f"[特殊按键] 按下: {key}")
        # 特殊按键按下不立即暂停，等待special_key_pause事件

    def _handle_ahk_special_key_up(self, key: str):
        """处理特殊按键释放（如space）"""
        LOG(f"[特殊按键] 释放: {key}")
        # 特殊按键释放不立即恢复，等待special_key_pause事件

    def _handle_ahk_special_key_pause(self, action: str):
        """处理特殊按键状态变化（最小实现）：特殊按键激活期间丢弃所有非紧急入队"""
        if action == "start":
            LOG_INFO("【特殊按键】 特殊按键激活 - 丢弃所有非紧急入队")
            # 启用丢弃非紧急入队（HP/MP除外）
            if hasattr(self.input_handler, "set_drop_non_emergency"):
                self.input_handler.set_drop_non_emergency(True)
        elif action == "end":
            LOG_INFO("【特殊按键】 特殊按键释放 - 允许非紧急入队")
            # 关闭丢弃非紧急入队
            if hasattr(self.input_handler, "set_drop_non_emergency"):
                self.input_handler.set_drop_non_emergency(False)

    def _handle_ahk_managed_key_down(self, key: str):
        """处理管理按键按下（如RButton/e）- 拦截+延迟+映射"""
        LOG_INFO(f"[管理按键] ========== 按下: {key} ==========")
        LOG_INFO(f"[管理按键] 当前状态: {self._state}")
        
        # 🎯 关键修复：暂停Python调度器，防止技能队列继续添加动作
        LOG_INFO(f"[管理按键] 发布scheduler_pause_requested事件")
        event_bus.publish(
            "scheduler_pause_requested",
            {"reason": f"managed_key_down:{key}", "active_keys": [key]},
        )
        
        # 🎯 清空非紧急队列中的待处理动作(AHK 端 HandleManagedKey 已先清过,
        # 这里再清一次确保 Python 侧后续提交的也被丢弃)
        # 🔧 BUG修复(#4): 必须保留 emergency 队列,否则 HP/MP 救命药剂会被误清
        self.input_handler.clear_non_emergency_queue()

        LOG_INFO(f"[管理按键] 管理按键处理完成")

    def _handle_ahk_managed_key_complete(self, key: str):
        """处理管理按键完成（延迟+按键执行完毕）"""
        LOG_INFO(f"[管理按键] ========== 完成: {key} ==========")
        
        # 🎯 恢复Python调度器
        LOG_INFO(f"[管理按键] 发布scheduler_resume_requested事件")
        event_bus.publish(
            "scheduler_resume_requested",
            {"reason": f"managed_key_complete:{key}"},
        )
        
        LOG_INFO(f"[管理按键] 调度器已恢复")

    def _handle_ahk_priority_key_down(self, key: str):
        """处理AHK优先级按键按下（兼容旧版本）"""
        LOG_INFO(f"[热键管理] 收到AHK拦截按键: {key}")

        # 🎯 关键修复：发布scheduler_pause_requested事件
        # 这会暂停统一调度器，实现"零资源浪费"优化
        event_bus.publish(
            "scheduler_pause_requested",
            {"reason": f"priority_key_down:{key}", "active_keys": [key]},
        )

    def _handle_ahk_priority_key_up(self, key: str):
        """处理AHK优先级按键释放（兼容旧版本）"""
        LOG_INFO(f"[热键管理] 收到AHK按键释放: {key}")

        # 🎯 关键修复：发布scheduler_resume_requested事件
        # 这会恢复统一调度器
        event_bus.publish(
            "scheduler_resume_requested", {"reason": f"priority_key_up:{key}"}
        )

    def _handle_ahk_monitor_key_down(self, key: str):
        """处理AHK监控按键按下（交互键A等）"""
        key_lower = key.lower()

        # 检查是否是交互/强制移动按键
        if key_lower == self._current_force_move_key:
            self._on_force_move_key_press()

    def _handle_ahk_monitor_key_up(self, key: str):
        """处理AHK监控按键释放"""
        key_lower = key.lower()

        # 检查是否是交互/强制移动按键
        if key_lower == self._current_force_move_key:
            self._on_force_move_key_release()

    def _set_state(self, new_state: MacroState) -> bool:
        try:
            with self._state_lock:
                if self._state == new_state:
                    LOG_INFO(f"[状态转换] 状态未改变: {self._state}")
                    return False
                if new_state not in self.VALID_TRANSITIONS.get(self._state, []):
                    LOG_ERROR(f"[状态转换] 无效转换: {self._state} → {new_state}")
                    return False

                old_state = self._state
                self._state = new_state
                LOG_INFO(f"[状态转换] 状态转换成功: {old_state} → {new_state}")

                # 音效播放由MainWindow统一处理，避免重复播放

                try:
                    self._on_state_enter(new_state, from_state=old_state)
                except Exception as e:
                    LOG_ERROR(f"[状态转换] _on_state_enter 异常: {e}")
                    import traceback

                    LOG_ERROR(
                        f"[状态转换] _on_state_enter 异常详情:\n{traceback.format_exc()}"
                    )
                    # 即使_on_state_enter失败，也要继续发布事件
                    pass

                try:
                    event_bus.publish(
                        "engine:state_changed", new_state=new_state, old_state=old_state
                    )
                    # 在状态转换时，发布完整的状态更新
                    self._publish_status_update()
                except Exception as e:
                    LOG_ERROR(f"[状态转换] 事件发布异常: {e}")
                    import traceback

                    LOG_ERROR(f"[状态转换] 事件发布异常详情:\n{traceback.format_exc()}")
                    # 即使事件发布失败，状态转换也算成功
                    pass

                # 更新OSD可见性
                self._update_osd_visibility()

                return True
        except Exception as e:
            LOG_ERROR(f"[状态转换] _set_state 异常: {e}")
            import traceback

            LOG_ERROR(f"[状态转换] _set_state 异常详情:\n{traceback.format_exc()}")
            return False

    def _on_state_enter(
        self, state: MacroState, from_state: Optional[MacroState] = None
    ):
        LOG_INFO(f"[状态转换] 进入状态: {state}")

        if state == MacroState.READY:
            # 进入READY状态时注册所有动态热键
            self._register_secondary_hotkeys()

            # 激活目标窗口并做准备动作
            self.input_handler.activate_target_window()
            self.input_handler.start()

            self.skill_manager.prepare_border_only()  # 预计算边框
            self.border_manager.enable_debug_save()

            # 收集资源区域配置，用于模板截取
            resource_regions = self._collect_resource_regions()
            self.border_manager.capture_once_for_debug_and_cache(
                self._global_config.get("capture_interval", 40), resource_regions
            )

            # 通知ResourceManager截取HSV模板
            if self.resource_manager and resource_regions:
                current_frame = self.border_manager.get_current_frame()
                if current_frame is not None:
                    self.resource_manager.capture_template_hsv(current_frame)

        elif state == MacroState.RUNNING:
            # 如果是从暂停状态恢复，调用resume；否则启动子系统
            if from_state == MacroState.PAUSED:
                LOG_INFO("[状态转换] 从暂停状态恢复")
                if self._prepared_mode == "combat":
                    self.skill_manager.resume()
                elif self._prepared_mode == "pathfinding":
                    self.pathfinding_manager.resume()
                self.resource_manager.resume()
                self.border_manager.resume_capture()
            else:
                # 首次启动
                self._start_subsystems_based_on_mode()

        elif state == MacroState.PAUSED:
            self.input_handler.clear_queue()  # 清空按键队列
            if self._prepared_mode == "combat":
                self.skill_manager.pause()
            elif self._prepared_mode == "pathfinding":
                self.pathfinding_manager.pause()
            self.resource_manager.pause()
            self.border_manager.pause_capture()

        elif state == MacroState.STOPPED:
            # 进入STOPPED状态时清理所有动态热键（保留 F8/F7/F9 永久根热键）
            try:
                self.input_handler.clear_all_configurable_hooks()
                LOG_INFO("[热键管理] 已清理所有动态热键（F8/F7/F9 永久根热键保留）")
                LOG_INFO("[热键管理] AHK进程保持运行，F8/F7/F9 永久根热键保持监听")
            except Exception as e:
                LOG_ERROR(f"[热键管理] 清理动态热键失败: {e}")

            self.skill_manager.stop()
            self.pathfinding_manager.stop()
            self.resource_manager.stop()
            self.border_manager.stop()
            # 注意：不调用 input_handler.cleanup()，保持AHK进程和F8热键运行
            self._prepared_mode = "none"

            LOG_INFO("[状态转换] STOPPED状态处理完成，等待F8重新启动")

        event_bus.publish(f"engine:macro_{state.name.lower()}")

    def _start_subsystems_based_on_mode(self):
        """根据当前准备的模式，启动或恢复对应的子系统。"""
        LOG_INFO(f"[状态转换] 启动子系统，当前模式: {self._prepared_mode}")

        # 统一启动屏幕捕获
        self.border_manager.start_capture_loop(capture_region=None)

        if self._prepared_mode == "combat":
            LOG_INFO("[状态转换] 启动技能管理器")
            self.skill_manager.start()
        elif self._prepared_mode == "pathfinding":
            LOG_INFO("[状态转换] 启动寻路管理器")
            self.pathfinding_manager.start()

        # 恢复捕获（如果之前是暂停状态）
        self.border_manager.resume_capture()

        # 启动资源管理器（如果有启用配置）
        self.resource_manager.start()

        LOG_INFO("[状态转换] 子系统启动完成")

    def _publish_status_update(self):
        """发布当前完整的状态信息，确保状态同步"""
        status_info = {
            "state": self._state,
            "stationary_mode": self._stationary_mode_active,
            "force_move_active": self._force_move_active,
        }
        event_bus.publish("engine:status_updated", status_info)

    def _update_osd_visibility(self):
        """根据当前宏状态和调试模式配置，控制DEBUG OSD的显示/隐藏"""
        # Debug模式启用且程序在READY/RUNNING/PAUSED状态时才显示OSD
        should_show_debug_osd = self._is_debug_mode_active and self._state in [
            MacroState.READY,
            MacroState.RUNNING,
            MacroState.PAUSED,
        ]

        if should_show_debug_osd:
            if self._state == MacroState.READY:
                # READY状态：显示OSD但不启动数据发布
                self.debug_display_manager.stop()  # 确保停止数据发布
                event_bus.publish("debug_osd_show")
                event_bus.publish("debug_osd_ready_state")  # 发送准备状态事件
                LOG_INFO("[DEBUG MODE] OSD已显示 - READY状态")
            elif self._state == MacroState.RUNNING:
                # RUNNING状态：显示OSD并启动数据发布
                self.debug_display_manager.start()
                event_bus.publish("debug_osd_show")
                LOG_INFO("[DEBUG MODE] OSD已显示，数据发布已启动 - RUNNING状态")
            elif self._state == MacroState.PAUSED:
                # PAUSED状态：显示OSD但停止数据发布
                self.debug_display_manager.stop()
                event_bus.publish("debug_osd_show")
                LOG_INFO("[DEBUG MODE] OSD已显示，数据发布已停止 - PAUSED状态")
        else:
            # 任何其他状态（包括STOPPED）都隐藏Debug OSD
            self.debug_display_manager.stop()
            event_bus.publish("debug_osd_hide")
            LOG_INFO(f"[DEBUG MODE] OSD已隐藏，当前状态: {self._state}")

    def _handle_f8_press(self, full_config: Optional[Dict[str, Any]] = None):
        try:
            LOG_INFO(f"[热键] ========== F8按键处理开始 ==========")
            LOG_INFO(f"[热键] 当前状态: {self._state}")
            LOG_INFO(f"[热键] 是否有配置: {full_config is not None}")
            with self._transition_lock:
                if self._state == MacroState.STOPPED:
                    # 🔧 三模式硬互斥(combat/pathfinding/洗练):洗练运行时拒绝启动主功能,
                    # 与 _on_f9_key_press 保持对称(F9 也有此检查)
                    if self.affix_reroll_manager.status.is_running:
                        LOG_INFO("[MacroEngine] 洗练进行中,无法启动主功能。请先按 F7 停止洗练。")
                        return
                    LOG_INFO("【热键】 F8 - 从 STOPPED状态启动")
                    if full_config:
                        self._skills_config = full_config.get("skills", {})
                        self._global_config = full_config.get("global", {})
                        self.sound_manager.update_config(self._global_config)
                        
                        # 设置输入模式
                        input_mode = self._global_config.get("input_mode", "direct")
                        if hasattr(self.input_handler, "command_sender") and self.input_handler.command_sender:
                            try:
                                self.input_handler.command_sender.set_send_mode(input_mode)
                                LOG_INFO(f"【输入模式】 已设置为: {input_mode}")
                            except Exception as e:
                                LOG_ERROR(f"【输入模式】 设置失败: {e}")
                        
                        event_bus.publish(
                            "engine:config_updated",
                            self._skills_config,
                            self._global_config,
                        )
                    self._prepared_mode = "combat"
                    # 检查状态转换是否成功
                    if not self.prepare_border_only():
                        LOG_ERROR("[MacroEngine] 准备边框失败，无法启动技能模式")
                        return
                    LOG_INFO("[热键] F8 - 成功转换为READY状态")

                else:
                    LOG_INFO(f"[热键] F8 - 从{self._state}状态停止")
                    self.stop_macro()
                    LOG_INFO(f"[热键] F8 - 成功转换为STOPPED状态")

        except Exception as e:
            LOG_ERROR(f"[热键] F8处理异常: {e}")
            import traceback

            LOG_ERROR(f"[热键] F8异常详情:\n{traceback.format_exc()}")

    def _on_f9_key_press(self):
        with self._transition_lock:
            if self._state == MacroState.STOPPED:
                if self.affix_reroll_manager.status.is_running:
                    LOG_INFO("[MacroEngine] 洗练进行中，无法准备寻路。")
                    return
                self._prepared_mode = "pathfinding"
                self.prepare_border_only()
            elif self._prepared_mode == "pathfinding":
                self.stop_macro()

    def _on_f7_key_press(self):
        if self._state != MacroState.STOPPED:
            LOG_INFO(f"[MacroEngine] 主功能运行时无法启动洗练功能")
            return
        event_bus.publish("hotkey:affix_reroll_start")

    def _handle_z_press(self):
        try:
            LOG_INFO(f"[热键] Z键被按下，当前状态: {self._state}")
            result = self.toggle_pause_resume()
            LOG_INFO(
                f"[热键] toggle_pause_resume 返回结果: {result}, 新状态: {self._state}"
            )
        except Exception as e:
            LOG_ERROR(f"[热键] Z键处理异常: {e}")
            import traceback

            LOG_ERROR(f"[热键] Z键异常详情:\n{traceback.format_exc()}")

    def _on_z_key_press(self):
        event_bus.publish("hotkey:z_press")

    # _should_suppress_hotkey 已删除，AHK处理所有热键拦截

    def _collect_resource_regions(self) -> Dict[str, Tuple[int, int, int, int]]:
        """收集资源检测区域配置"""
        resource_regions = {}
        resource_config = self._global_config.get("resource_management", {})

        # HP区域
        hp_config = resource_config.get("hp_config", {})
        if hp_config.get("enabled", False):
            x1 = hp_config.get("region_x1", 0)
            y1 = hp_config.get("region_y1", 0)
            x2 = hp_config.get("region_x2", 0)
            y2 = hp_config.get("region_y2", 0)
            if x1 < x2 and y1 < y2:
                resource_regions["hp_region"] = (x1, y1, x2, y2)

        # MP区域
        mp_config = resource_config.get("mp_config", {})
        if mp_config.get("enabled", False):
            x1 = mp_config.get("region_x1", 0)
            y1 = mp_config.get("region_y1", 0)
            x2 = mp_config.get("region_x2", 0)
            y2 = mp_config.get("region_y2", 0)
            if x1 < x2 and y1 < y2:
                resource_regions["mp_region"] = (x1, y1, x2, y2)

        return resource_regions

    def _on_config_updated(
        self, skills_config: Dict[str, Any], global_config: Dict[str, Any]
    ):
        """处理配置更新事件（纯配置更新，不涉及热键管理）"""
        try:
            LOG_INFO("[配置更新] _on_config_updated 被调用")

            # 更新全局配置
            self._global_config = global_config

            # 更新技能配置
            self._skills_config = skills_config

            # 更新资源管理配置
            resource_config = global_config.get("resource_management", {})
            self.resource_manager.update_config(resource_config)

            # 更新调试模式
            debug_config = global_config.get("debug_mode", {})
            debug_enabled = debug_config.get("enabled", False)
            self._is_debug_mode_active = debug_enabled
            self.input_handler.set_dry_run_mode(debug_enabled)
            LOG_INFO(
                f"[DEBUG MODE] _on_config_updated: 干跑模式已设置为 {debug_enabled}"
            )

            # 更新窗口激活配置
            window_config = global_config.get("window_activation", {})
            if window_config.get("enabled", False):
                ahk_class = window_config.get("ahk_class", "")
                if ahk_class:
                    LOG_INFO(f"[窗口激活] 设置目标窗口（类名）: {ahk_class}")
                    self.input_handler.set_target_window(f"ahk_class {ahk_class}")

            # 边框管理器不需要配置更新

            # 洗练管理器配置通过事件系统更新，不需要直接调用

            # 设置强制移动键到AHK（仅设置，不注册Hook）
            # 总是发送，即使是空值，以便清空之前的配置
            stationary_config = global_config.get("stationary_mode_config", {})
            force_move_key = stationary_config.get("force_move_hotkey", "")
            self.input_handler.set_force_move_key(force_move_key)
            if force_move_key:
                LOG_INFO(f"[强制移动键] 已设置到AHK: {force_move_key}")
            else:
                LOG_INFO("[强制移动键] 已清空AHK配置")

            # 设置强制移动替换键到AHK（只有用户配置了才发送）
            if "force_move_replacement_key" in stationary_config:
                force_move_replacement_key = stationary_config["force_move_replacement_key"]
                self.input_handler.set_force_move_replacement_key(
                    force_move_replacement_key
                )
                LOG_INFO(f"[强制移动替换键] 已设置到AHK: {force_move_replacement_key}")
            else:
                # 用户未配置，发送空字符串清空AHK配置
                self.input_handler.set_force_move_replacement_key("")
                LOG_INFO("[强制移动替换键] 用户未配置，已清空AHK配置")

            # 🎯 新增：批量更新AHK紧急按键缓存（修复BUG）
            self._update_ahk_emergency_keys_cache(global_config)
            
            # 注意：热键管理现在由状态机驱动，不在这里处理

            # 更新OSD可见性
            self._update_osd_visibility()

        except Exception as e:
            LOG_ERROR(f"[配置更新] 处理配置更新时发生错误: {e}")
            import traceback

            LOG_ERROR(f"[配置更新] 详细错误: {traceback.format_exc()}")

    def _update_ahk_emergency_keys_cache(self, global_config: Dict[str, Any]):
        """更新AHK紧急按键缓存（修复space按键时HP/MP无法执行的BUG）"""
        try:
            # 收集HP/MP按键配置
            resource_config = global_config.get("resource_management", {})
            hp_config = resource_config.get("hp_config", {})
            mp_config = resource_config.get("mp_config", {})
            
            batch_config = {}
            
            # HP按键
            if hp_config.get("enabled", False):
                hp_key = hp_config.get("key", "")
                if hp_key:
                    batch_config["hp_key"] = hp_key.lower()
            
            # MP按键
            if mp_config.get("enabled", False):
                mp_key = mp_config.get("key", "")
                if mp_key:
                    batch_config["mp_key"] = mp_key.lower()
            
            # 添加其他缓存配置
            stationary_config = global_config.get("stationary_mode_config", {})
            mode_type = stationary_config.get("mode_type", "")
            if mode_type:
                batch_config["stationary_type"] = mode_type
            
            # 只有在有配置更新时才发送
            if batch_config and hasattr(self.input_handler, "command_sender") and self.input_handler.command_sender:
                self.input_handler.command_sender.batch_update_config(batch_config)
                LOG_INFO(f"【紧急按键缓存】 已更新AHK配置: {batch_config}")
            
        except Exception as e:
            LOG_ERROR(f"【紧急按键缓存】 更新失败: {e}")
            import traceback
            LOG_ERROR(f"【紧急按键缓存】 异常详情:\n{traceback.format_exc()}")

    # 旧的热键管理方法已删除，现在使用AHK处理所有热键

    def _on_stationary_key_press(self):
        """原地模式热键按下事件 - 切换模式"""
        # 无论当前状态如何，都允许切换原地模式
        self._stationary_mode_active = not self._stationary_mode_active

        # 通知AHKCommandSender原地模式状态变化
        if (
            hasattr(self.input_handler, "command_sender")
            and self.input_handler.command_sender
        ):
            stationary_config = self._global_config.get("stationary_mode_config", {})
            mode_type = stationary_config.get("mode_type", "shift_modifier")

            self.input_handler.command_sender.set_stationary_mode(
                self._stationary_mode_active, mode_type
            )

            # 添加调试日志
            LOG_INFO(
                f"[原地模式] 通知AHK命令发送器: 状态={self._stationary_mode_active}, 类型={mode_type}"
            )

        self._publish_status_update()
        if self._stationary_mode_active:
            LOG_INFO("[原地模式] 已激活")
        else:
            LOG_INFO("[原地模式] 已取消")

    def _on_stationary_key_release(self):
        """原地模式热键释放事件 - 切换模式下不需要处理"""
        pass

    def _on_force_move_key_press(self):
        """交互/强制移动热键按下事件 - 按住激活"""
        self._force_move_active = True

        # 通知AHK强制移动状态变化
        if (
            hasattr(self.input_handler, "command_sender")
            and self.input_handler.command_sender
        ):
            self.input_handler.command_sender.set_force_move_state(True)

        self._publish_status_update()
        LOG_INFO("[交互模式] 已激活")

    def _on_force_move_key_release(self):
        """交互/强制移动热键释放事件 - 松开取消"""
        self._force_move_active = False

        # 通知AHK强制移动状态变化
        if (
            hasattr(self.input_handler, "command_sender")
            and self.input_handler.command_sender
        ):
            self.input_handler.command_sender.set_force_move_state(False)

        self._publish_status_update()
        LOG_INFO("[交互模式] 已取消")

    def get_current_state(self) -> MacroState:
        return self._state

    def prepare_border_only(self) -> bool:
        return self._set_state(MacroState.READY)

    def stop_macro(self) -> bool:
        return self._set_state(MacroState.STOPPED)

    def toggle_pause_resume(self) -> bool:
        try:
            LOG_INFO(f"[状态转换] toggle_pause_resume 被调用，当前状态: {self._state}")
            if self._state == MacroState.RUNNING:
                LOG_INFO("[状态转换] RUNNING → PAUSED")
                result = self._set_state(MacroState.PAUSED)
                LOG_INFO(f"[状态转换] RUNNING → PAUSED 结果: {result}")
                return result
            if self._state == MacroState.PAUSED:
                LOG_INFO("[状态转换] PAUSED → RUNNING")
                result = self._set_state(MacroState.RUNNING)
                LOG_INFO(f"[状态转换] PAUSED → RUNNING 结果: {result}")
                return result
            if self._state == MacroState.READY:
                LOG_INFO("[状态转换] READY → RUNNING")
                result = self._set_state(MacroState.RUNNING)
                LOG_INFO(f"[状态转换] READY → RUNNING 结果: {result}")
                return result
            LOG_INFO(f"[状态转换] 无效的状态转换请求，当前状态: {self._state}")
            return False
        except Exception as e:
            LOG_ERROR(f"[状态转换] toggle_pause_resume 异常: {e}")
            import traceback

            LOG_ERROR(
                f"[状态转换] toggle_pause_resume 异常详情:\n{traceback.format_exc()}"
            )
            return False

    def set_debug_mode(self, enabled: bool):
        """设置DEBUG MODE配置标志，并触发配置更新"""
        try:
            LOG_INFO(f"[DEBUG MODE] 收到设置DEBUG MODE请求: {enabled}")

            # 更新配置
            if "debug_mode" not in self._global_config:
                self._global_config["debug_mode"] = {}
            self._global_config["debug_mode"]["enabled"] = enabled

            # 发布配置更新事件，让所有订阅者（包括自身）响应
            event_bus.publish(
                "engine:config_updated", self._skills_config, self._global_config
            )
            LOG_INFO(f"[DEBUG MODE] DEBUG MODE配置已更新并发布事件: {enabled}")
        except Exception as e:
            LOG_ERROR(f"[DEBUG MODE] 设置DEBUG MODE异常: {e}")
            import traceback

            LOG_ERROR(f"[DEBUG MODE] 设置DEBUG MODE异常详情:\n{traceback.format_exc()}")

    def load_config(self, config_file: str):
        LOG_INFO(f"[配置加载] 开始加载配置文件: {config_file}")
        try:
            config_path = __import__("pathlib").Path(config_file)
            if not config_path.exists() or config_path.stat().st_size == 0:
                LOG_INFO(
                    f"[MacroEngine] 配置文件 '{config_file}' 不存在或为空，生成默认配置。"
                )
                config_data = self._generate_default_config()
                self.config_manager.save_config(config_data, config_file)
            else:
                LOG_INFO(f"[MacroEngine] 从文件 '{config_file}' 加载配置。")
                config_data = self.config_manager.load_config(config_file)

            self._skills_config = config_data.get("skills", {})
            self._global_config = config_data.get("global", {})
            self.sound_manager.update_config(self._global_config)
            event_bus.publish(
                "engine:config_updated", self._skills_config, self._global_config
            )
            self._remember_config_file(config_file)
        except Exception as e:
            LOG_ERROR(f"加载配置文件 '{config_file}' 失败: {e}")

    def save_full_config(self, file_path: str, full_config: Dict[str, Any]):
        try:
            self._skills_config = full_config.get("skills", {})
            self._global_config = full_config.get("global", {})
            self.sound_manager.update_config(self._global_config)
            event_bus.publish(
                "engine:config_updated", self._skills_config, self._global_config
            )
            self.config_manager.save_config(full_config, file_path)
            self._remember_config_file(file_path)
        except Exception as e:
            LOG_ERROR(f"保存配置文件 '{file_path}' 失败: {e}")

    def _handle_ui_request_current_config(self):
        """处理UI请求当前配置的事件"""
        LOG_INFO("[MacroEngine] 收到UI请求当前配置事件，发布配置更新")
        event_bus.publish(
            "engine:config_updated", self._skills_config, self._global_config
        )

    def _generate_default_config(self) -> Dict[str, Any]:
        """生成包含默认值的完整配置"""
        default_skills = {}
        for i in range(1, 9):
            default_skills[f"Skill{i}"] = {
                "Enabled": False,
                "Key": str(i),
                "Priority": False,
                "Timer": 1000,
                "TriggerMode": 0,
                "CooldownCoordX": 0,
                "CooldownCoordY": 0,
                "CooldownSize": 12,
                "ColorTolerance": 12,
                "ExecuteCondition": 0,
                "ConditionCoordX": 0,
                "ConditionCoordY": 0,
                "ConditionColor": 0,
                "AltKey": "",
            }

        default_global = {
            "sequence_enabled": False,
            "skill_sequence": "1,2,3,4,5,6,7,8",
            "sequence_timer_interval": 1000,
            "queue_processor_interval": 50,
            "cooldown_checker_interval": 100,
            "capture_interval": 40,
            "sound_feedback_enabled": False,
            "window_activation": {"enabled": False, "ahk_class": "", "ahk_exe": ""},
            "stationary_mode_config": {
                "mode_type": "block_mouse",
                "hotkey": "",
                "force_move_hotkey": "",
            },
            "affix_reroll": {
                "enabled": False,
                "target_affixes": [],
                "max_attempts": 100,
                "click_delay": 200,
                "enchant_button_coord": None,
                "first_affix_button_coord": None,
                "replace_button_coord": None,
                "close_button_coord": None,
            },
            "pathfinding_config": {
                "hotkey": "f9",
                "minimap_area": [1600, 70, 250, 250],  # 默认小地图区域 (示例值)
            },
            "priority_keys": {
                "enabled": False,  # 默认禁用
                "special_keys": [],
                "managed_keys": {},
            },
        }
        return {"skills": default_skills, "global": default_global}

    def cleanup(self):
        """分层清理机制，确保按依赖关系安全地释放所有资源。"""
        with self._cleanup_lock:
            if self._cleanup_done:
                LOG_INFO("[清理] MacroEngine 已清理过，跳过重复清理。")
                return
            self._cleanup_done = True

        LOG_INFO("[清理] 开始执行分层清理...")

        # 定义清理层级，从上层业务逻辑到底层系统资源
        cleanup_layers = [
            # Layer 1: 停止所有活动的用户级任务
            (
                "业务逻辑层",
                [
                    self.skill_manager,
                    self.pathfinding_manager,
                    self.affix_reroll_manager,
                    self.resource_manager,
                ],
            ),
            # Layer 2: 停止核心服务和IO
            ("核心服务层", [self.border_manager, self.input_handler]),
        ]

        # 执行分层清理
        for layer_name, components in cleanup_layers:
            self._cleanup_layer(layer_name, components)

        LOG_INFO("[清理] MacroEngine相关组件清理完毕。")

    def _cleanup_layer(self, layer_name: str, components: list):
        """安全地清理指定层级的所有组件，为每个组件设置超时以防假死。"""
        LOG_INFO(f"-- 开始清理: {layer_name} --")
        for component in components:
            if component is None:
                continue

            cleanup_thread = threading.Thread(
                target=self._safe_cleanup_component, args=(component,)
            )
            cleanup_thread.daemon = True  # 设置为守护线程
            cleanup_thread.start()

            # 为清理操作设置2秒的超时
            cleanup_thread.join(timeout=2.0)

            if cleanup_thread.is_alive():
                component_name = component.__class__.__name__
                LOG_ERROR(f"  - 清理组件 {component_name} 超时！(超过2秒)")

    def _safe_cleanup_component(self, component: Any):
        """在独立的线程中执行单个组件的清理操作。"""
        component_name = component.__class__.__name__
        try:
            # 尝试调用cleanup，如果不存在则调用其他停止方法
            if hasattr(component, "cleanup"):
                component.cleanup()
                LOG_INFO(f"  - {component_name}.cleanup() 调用成功")
            elif hasattr(component, "stop"):
                component.stop()
                LOG_INFO(f"  - {component_name}.stop() 调用成功")
            elif hasattr(component, "stop_reroll"):  # 特殊处理
                component.stop_reroll("Application cleanup")
                LOG_INFO(f"  - {component_name}.stop_reroll() 调用成功")
            elif hasattr(component, "stop_listening"):  # 特殊处理
                component.stop_listening()
                LOG_INFO(f"  - {component_name}.stop_listening() 调用成功")
        except Exception as e:
            LOG_ERROR(f"  - 清理组件 {component_name} 时发生错误: {e}")
