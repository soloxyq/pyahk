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
from ..utils.key_names import normalize_config_keys


# GUI「图像捕获间隔」的合法范围与默认值(毫秒)。
# 下限 10:更低会让 DXGI 捕获线程空转抢 CPU;上限 1000:更高时资源检测/技能冷却
# 基本失明,一定是配错了(比如把秒填成了毫秒的倒数)。
CAPTURE_INTERVAL_MIN_MS = 10
CAPTURE_INTERVAL_MAX_MS = 1000
CAPTURE_INTERVAL_DEFAULT_MS = 40
SPECIAL_KEY_RESUME_DELAY_MIN_MS = 0
SPECIAL_KEY_RESUME_DELAY_MAX_MS = 1000
SPECIAL_KEY_RESUME_DELAY_DEFAULT_MS = 0
KEY_PRESS_DURATION_MIN_MS = 1
KEY_PRESS_DURATION_MAX_MS = 1000
KEY_PRESS_DURATION_DEFAULT_MS = 10


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
        # QTimer 调度失败会在当前状态转换栈内同步回退，因此必须允许重入。
        self._transition_lock = threading.RLock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_done = False
        self._event_subscriptions = []
        self._skills_config: Dict[str, Any] = {}
        self._global_config: Dict[str, Any] = {}
        # 启动期加载失败的暂存:__init__ 里的 load_config 发布 config_load_result 时
        # GUI 还没构造(main.py 先建引擎后建窗口),事件会被 EventBus 丢进虚空。
        # 失败结果存在这里,由 ui:request_current_config 握手时一次性重放。
        self._pending_load_error: Optional[Tuple[str, str]] = None
        self.current_config_file = self._resolve_initial_config_file(config_file)
        # AHK 传输熔断:置位后 F8 先做一次有界 PING 探测，成功才解除。
        # 超时消息不会重放；安全清理命令仍可 force 发送。
        self._ahk_transport_failed = False
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
        self._current_boss_mode_key = ""
        self._boss_mode_active = False

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
        self._subscribe_event("ui:load_config_requested", self.load_config)
        self._subscribe_event("ui:save_full_config_requested", self.save_full_config)
        self._subscribe_event(
            "ui:sync_and_toggle_state_requested", self._handle_f8_press
        )  # F8 启动时的统一配置同步入口
        self._subscribe_event(
            "ui:request_current_config", self._handle_ui_request_current_config
        )
        self._subscribe_event("hotkey:z_press", self._handle_z_press)
        self._subscribe_event("engine:config_updated", self._on_config_updated)
        # AHK 子进程意外退出 → 强制停机 + 告警。由 AHKInputHandler 在命令发送失败时探测到进程
        # 已退出后,经 ahk_signal_bridge 切回 GUI 线程发布本事件(故本 handler 已在主线程)。
        self._subscribe_event("ahk_process_died", self._on_ahk_process_died)
        # AHK 进程活着但消息循环挂死(命令 500ms 无响应):同样延迟停机,并锁定 READY 入口
        self._subscribe_event("ahk_transport_failed", self._on_ahk_transport_failed)
        self._subscribe_event("ahk_transport_recovered", self._on_ahk_transport_recovered)
        self._subscribe_event(
            "skill_manager:input_sync_failed", self._on_skill_input_sync_failed
        )
        # AHK 丢弃了待发动作(过载预算裁剪 / 等待过期),data 带两个累计计数
        self._subscribe_event("queue_drop", self._on_queue_drop)

    def _subscribe_event(self, event_name: str, handler):
        """订阅事件并登记释放信息，避免重建引擎后旧实例继续接收事件。"""
        subscriptions = getattr(self, "_event_subscriptions", None)
        if subscriptions is None:
            subscriptions = []
            self._event_subscriptions = subscriptions
        subscription = (event_name, handler)
        if subscription in subscriptions:
            return
        event_bus.subscribe(event_name, handler)
        subscriptions.append(subscription)

    def _unsubscribe_event_handlers(self):
        """幂等注销 MacroEngine 自己登记的全部 EventBus handler。"""
        subscriptions = getattr(self, "_event_subscriptions", [])
        self._event_subscriptions = []
        for event_name, handler in subscriptions:
            try:
                event_bus.unsubscribe(event_name, handler)
            except Exception as e:
                LOG_ERROR(f"[清理] 注销事件订阅失败 {event_name}: {e}")

    def _on_queue_drop(self, key: str = ""):
        """AHK 丢弃了待发动作。data 形如 "overload=12,expired=3"(**累计**计数)。

        必须让用户看得见,而且**两种原因要分开诊断** —— 混成一条"入队速度超上限"
        会把"只是配了个长 delay"的用户引去调根本没问题的技能生产率:
        - overload:入队速度超过执行上限(实测约 63 动作/秒,见 hold_server_extended.ahk
          中 QUEUE_TICK_MS 与 MAX_PENDING_ATOMS 的说明)→ 该调生产侧。
        - expired:动作排队等待超过 STALE_MS(约 500ms,单调时钟)→ 常见原因是
          序列里的长 delay 或低优先级被高优先级持续压制,与生产速率无关。
        AHK 端已按每秒最多一条节流;这里按与上次的差值报告本轮新增。
        """
        counts = {}
        for part in key.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    counts[k.strip()] = int(v)
                except ValueError:
                    pass
        if not counts:
            LOG_ERROR(f"[队列丢弃] AHK 报告丢弃了待发动作({key})")
            return

        over = counts.get("overload", 0)
        expired = counts.get("expired", 0)
        last_over, last_expired = getattr(self, "_last_queue_drop_counts", (0, 0))
        self._last_queue_drop_counts = (over, expired)

        msgs = []
        if over > last_over:
            msgs.append(
                f"过载丢弃 +{over - last_over}(入队速度超过约 63 动作/秒的执行上限;"
                f"建议减少同时启用的冷却检测技能、调大技能 Timer 或缩短逗号序列)"
            )
        if expired > last_expired:
            msgs.append(
                f"过期丢弃 +{expired - last_expired}(动作排队等待超过约 500ms;"
                f"常见原因:序列里的长 delay 或低优先级被高优先级持续压制,与生产速率无关)"
            )
        if not msgs:
            # 计数没有增长却收到通知(如 AHK 重启后计数回卷),报累计值兜底
            msgs.append(f"累计 过载 {over} / 过期 {expired}")
        LOG_ERROR("[队列丢弃] " + ";".join(msgs))

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
                if not self.input_handler.register_root_hook(key):
                    raise RuntimeError(f"永久根热键注册失败: {key}")
                LOG_INFO(f"[热键管理] [OK] 永久根热键注册成功: {key}")

            # 订阅AHK拦截事件（系统热键）
            self._subscribe_event("intercept_key_down", self._handle_ahk_intercept_key)

            # 🎯 订阅新的按键事件系统
            # 特殊按键事件（如space）- 持续状态检测
            self._subscribe_event("special_key_down", self._handle_ahk_special_key_down)
            self._subscribe_event("special_key_up", self._handle_ahk_special_key_up)
            self._subscribe_event("special_key_pause", self._handle_ahk_special_key_pause)

            # 管理按键事件（如RButton/e）- 拦截+延迟+映射
            self._subscribe_event("managed_key_down", self._handle_ahk_managed_key_down)
            self._subscribe_event(
                "managed_key_complete", self._handle_ahk_managed_key_complete
            )

            # 兼容旧的优先级事件（逐步迁移）
            # priority_key_down/up 订阅已移除:AHK 端从不发送这两个事件(死代码)

            # 订阅AHK监控事件（交互键A等）
            self._subscribe_event("monitor_key_down", self._handle_ahk_monitor_key_down)
            self._subscribe_event("monitor_key_up", self._handle_ahk_monitor_key_up)

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册 F8/F7/F9 永久根热键时发生错误: {e}")
            # 根热键是停机入口，缺任意一个都不能继续构造一个“看似可用”的引擎。
            # 走完整分层清理，撤销各组件订阅并停止 AHK，清掉可能已注册的部分根键。
            try:
                self.cleanup()
            except Exception as cleanup_error:
                LOG_ERROR(f"[热键管理] 根热键失败后清理失败: {cleanup_error}")
            raise RuntimeError("F8/F7/F9 永久根热键初始化失败") from e

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
                return False

            # 注册配置相关的动态热键（原地模式、交互模式等）
            LOG_INFO("[热键管理] 准备注册配置相关热键...")
            if not self._register_config_based_hotkeys():
                return False

            LOG_INFO("[热键管理] ========== 动态热键注册完成 ==========")
            return True

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册动态热键时发生错误: {e}")
            import traceback

            LOG_ERROR(f"[热键管理] 详细错误: {traceback.format_exc()}")
            return False

    def _register_config_based_hotkeys(self):
        """根据当前配置注册热键"""
        try:
            LOG_INFO("[热键管理] 开始注册配置相关热键...")
            registration_ok = True

            # 获取当前配置
            stationary_config = self._global_config.get("stationary_mode_config", {})
            stationary_key = stationary_config.get("hotkey")
            LOG_INFO(f"[热键管理] 原地模式配置: {stationary_config}")
            LOG_INFO(f"[热键管理] 原地模式热键: {stationary_key}")
            # 跟踪动态热键,避免后注册覆盖先注册的 AHK Hotkey。
            # Z 已在 _register_secondary_hotkeys 中注册为执行/暂停键。
            registered_business_keys: set[str] = {"z"}

            # 注册原地模式热键
            self._current_stationary_key = ""
            if stationary_key:
                key_lower = (stationary_key or "").lower()
                LOG_INFO(f"[热键管理] 准备注册原地模式热键: {stationary_key}")
                if key_lower in registered_business_keys:
                    LOG_ERROR(
                        f"[原地模式] 跳过 '{stationary_key}': 已被其他动态热键占用"
                    )
                elif self.input_handler.register_hook(stationary_key, "intercept"):
                    # 更新当前原地模式热键
                    self._current_stationary_key = key_lower
                    registered_business_keys.add(key_lower)
                    LOG_INFO(f"[原地模式] 注册Hook成功: {stationary_key} (intercept)")
                    LOG_INFO(
                        f"[原地模式] 当前原地模式热键已设置为: {self._current_stationary_key}"
                    )
                else:
                    LOG_ERROR(f"[原地模式] 注册Hook失败: {stationary_key}")
                    registration_ok = False
            else:
                LOG_INFO("[热键管理] 未配置原地模式热键")

            # 注册强制移动键（monitor模式，不拦截但监控状态）
            force_move_key = stationary_config.get("force_move_hotkey")
            self._current_force_move_key = ""
            if force_move_key:
                key_lower = (force_move_key or "").lower()
                LOG_INFO(f"[热键管理] 准备注册强制移动键: {force_move_key}")
                if key_lower in registered_business_keys:
                    LOG_ERROR(
                        f"[强制移动键] 跳过 '{force_move_key}': 已被其他动态热键占用"
                    )
                elif self.input_handler.register_hook(force_move_key, "monitor"):
                    # 更新当前强制移动键
                    self._current_force_move_key = key_lower
                    registered_business_keys.add(key_lower)
                    LOG_INFO(f"[强制移动键] 注册Hook成功: {force_move_key} (monitor)")
                    LOG_INFO(
                        f"[强制移动键] 当前强制移动键已设置为: {self._current_force_move_key}"
                    )
                else:
                    LOG_ERROR(f"[强制移动键] 注册Hook失败: {force_move_key}")
                    registration_ok = False
            else:
                LOG_INFO("[热键管理] 未配置强制移动键")

            # BOSS 模式切换键：只在技能模式注册,宏模式不生效。
            boss_mode_key = self._global_config.get("boss_mode_hotkey", "")
            self._current_boss_mode_key = ""
            if self._global_config.get("sequence_enabled", False):
                if boss_mode_key:
                    LOG_INFO("[BOSS模式] 当前为宏模式,不注册 BOSS 模式热键")
            elif boss_mode_key:
                key_lower = (boss_mode_key or "").lower()
                LOG_INFO(f"[热键管理] 准备注册 BOSS 模式热键: {boss_mode_key}")
                if key_lower in registered_business_keys:
                    LOG_ERROR(
                        f"[BOSS模式] 跳过 '{boss_mode_key}': 已被其他动态热键占用"
                    )
                elif self.input_handler.register_hook(boss_mode_key, "intercept"):
                    self._current_boss_mode_key = key_lower
                    registered_business_keys.add(key_lower)
                    LOG_INFO(f"[BOSS模式] 注册Hook成功: {boss_mode_key} (intercept)")
                else:
                    LOG_ERROR(f"[BOSS模式] 注册Hook失败: {boss_mode_key}")
                    registration_ok = False
            else:
                LOG_INFO("[BOSS模式] 未配置 BOSS 模式热键")

            # 注册管理按键配置
            priority_config = self._global_config.get("priority_keys", {})
            LOG_INFO(f"[热键管理] 优先级配置: {priority_config}")

            if priority_config.get("enabled", False):
                LOG_INFO("[热键管理] 优先级配置已启用")

                # 注册特殊按键（如space）- 使用AHK标准按键名
                special_keys = priority_config.get("special_keys", [])
                LOG_INFO(f"[热键管理] 特殊按键列表: {special_keys}")
                LOG_INFO(f"[热键管理] 特殊按键数量: {len(special_keys)}")
                for key in special_keys:
                    key_lower = (key or "").lower()
                    if key_lower in registered_business_keys:
                        LOG_ERROR(
                            f"[特殊按键] 跳过 '{key}': 已被其他类别注册,"
                            f"special/managed 不应共用同一按键"
                        )
                        continue
                    LOG_INFO(f"[热键管理] 准备注册特殊按键: '{key}' (类型: {type(key).__name__})")
                    if self.input_handler.register_hook(key, "special"):
                        registered_business_keys.add(key_lower)
                        LOG_INFO(f"[特殊按键] 注册成功: {key} (special)")
                    else:
                        LOG_ERROR(f"[特殊按键] 注册失败: {key}")
                        registration_ok = False

                # 注册管理按键（如RButton/e键,程序代按）
                managed_keys = priority_config.get("managed_keys", {})
                LOG_INFO(f"[热键管理] 管理按键配置: {managed_keys}")
                for key, config in managed_keys.items():
                    key_lower = (key or "").lower()
                    if key_lower in registered_business_keys:
                        LOG_ERROR(
                            f"[管理按键] 跳过 '{key}': 已被其他类别注册,"
                            f"special/managed 不应共用同一按键"
                        )
                        continue
                    LOG_INFO(f"[热键管理] 准备注册管理按键: {key}, 配置: {config}")
                    if self.input_handler.register_hook(key, "priority"):
                        registered_business_keys.add(key_lower)
                        target = config.get("target", key)
                        delay = config.get("delay", 0)
                        hold_ms = config.get("hold_ms", 0)
                        # 发送管理按键配置到AHK
                        configured = self.input_handler.set_managed_key_config(
                            key, target, delay, hold_ms
                        )
                        if not configured:
                            LOG_ERROR(f"[管理按键] 下发配置失败: {key}")
                            registration_ok = False
                        else:
                            LOG_INFO(
                                f"[管理按键] 注册成功: {key} -> {target} "
                                f"(延迟: {delay}ms, 按住: {hold_ms}ms)"
                            )
                    else:
                        LOG_ERROR(f"[管理按键] 注册失败: {key}")
                        registration_ok = False
            else:
                LOG_INFO("[热键管理] 优先级配置未启用")

            LOG_INFO("[热键管理] 配置相关热键注册完成")
            return registration_ok

        except Exception as e:
            LOG_ERROR(f"[热键管理] 注册配置相关热键时发生错误: {e}")
            import traceback

            LOG_ERROR(f"[热键管理] 详细错误: {traceback.format_exc()}")
            return False

    def _handle_ahk_intercept_key(self, key: str, **kwargs):
        """处理AHK拦截的系统热键（F8/F7/F9/Z）和原地模式按键"""
        key_lower = key.lower()
        LOG_INFO(f"[热键管理] 收到AHK拦截按键: {key}")

        if key_lower in ("f8", "f8_stop"):
            if self._state == MacroState.STOPPED:
                if key_lower == "f8_stop":
                    # AHK 在动态 Hook 存在时把 F8 标成 stop-only。
                    # GUI 卡顿/主动停机后迟到的旧意图不得反向启动；但上一次
                    # CLEAR_HOOKS 可能只完成了部分注销，AHK 此时会保持物理 stop
                    # latch 并拒绝重新开闸。重跑幂等清理给瞬时失败一条恢复路径。
                    LOG_INFO("[热键管理] STOPPED 收到 F8 停机意图，重试安全清理")
                    with self._transition_lock:
                        self._enter_stopped_state()
                    return
                # 启动时先由 MainWindow 采集当前控件值,再发布
                # ui:sync_and_toggle_state_requested 回到 _handle_f8_press。
                # 直接启动会绕过尚未保存的 GUI 修改,继续使用旧配置。
                event_bus.publish("hotkey:f8_system_toggle")
            else:
                # 停止是安全路径,不能依赖 GUI 配置采集:任何控件取值异常都不应
                # 阻止用户用物理 F8 立即停机。
                self._handle_f8_press()
            return
        if key_lower == "f7":
            self._on_f7_key_press()
            return
        if key_lower == "f9":
            self._on_f9_key_press()
            return

        # 可靠 FIFO 会在 GUI 恢复后按顺序补发 F8 后的按键。F8 已进入
        # STOPPED 时，随后到达的 Z/原地/BOSS 都是旧运行世代的动态事件；
        # 让它们重新改写 AHK 状态会破坏 STOPPED 安全屏障。
        if self._state == MacroState.STOPPED:
            LOG_INFO(f"[热键管理] STOPPED 忽略迟到动态按键: {key}")
            return

        if key_lower == "z":
            self._on_z_key_press()
        elif key_lower == self._current_stationary_key:
            # 原地模式按键（X键）- 按一下切换状态
            self._on_stationary_key_press()
        elif key_lower == self._current_boss_mode_key:
            self._toggle_boss_mode()
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
            # 🔒 迟到的 start(QueuedConnection 跨状态转换)不得在 STOPPED 重新打开
            # 丢弃闸:special Hook 已注销,end 永远不会到,非紧急入队会被永久丢弃
            # (下轮开跑一个技能都不发且无日志)。end 不设门禁 —— 安全方向。
            if self._state == MacroState.STOPPED:
                return
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
        # 🔒 STOPPED 时迟到的管理键事件没有意义(停止链已清空全部队列),
        # 且熔断闩死时下面的 force 清队还要白等一次 500ms —— 直接丢弃。
        if self._state == MacroState.STOPPED:
            return
        LOG_INFO(f"[管理按键] ========== 按下: {key} ==========")
        LOG_INFO(f"[管理按键] 当前状态: {self._state}")

        # 🔧 已移除 scheduler_pause_requested 发布:暂停整个 UnifiedScheduler 会连
        # resource_checker 一起停(HP/MP 检测停摆),且 resume 重置全部任务相位 → 频繁按
        # 管理键会饿死长周期任务与资源检测。输入独占由 AHK 端 HandleManagedKey 保证。

        # 🎯 清空非紧急队列中的待处理动作(AHK 端 HandleManagedKey 已先清过,
        # 这里再清一次确保 Python 侧后续提交的也被丢弃)
        # 🔧 BUG修复(#4): 必须保留 emergency 队列,否则 HP/MP 救命药剂会被误清
        self.input_handler.clear_non_emergency_queue()

        LOG_INFO(f"[管理按键] 管理按键处理完成")

    def _handle_ahk_managed_key_complete(self, key: str):
        """处理管理按键完成（延迟+按键执行完毕）

        🔧 不再发布 scheduler_resume_requested(见 _handle_ahk_managed_key_down 的说明)。
        AHK 端在最后一个管理键完成时会调 ReconcileSkillHoldKeys() 补齐被推迟的持键。
        """
        LOG_INFO(f"[管理按键] ========== 完成: {key} ==========")

    # 注:_handle_ahk_priority_key_down/up 已删除 —— AHK 端从不发送 priority_key_down/up
    # 事件(只发 managed_key_down / managed_key_complete / special_key_*),那是旧版本遗留
    # 的死代码,且同样携带会饿死调度器的 pause/resume 逻辑。

    def _handle_ahk_monitor_key_down(self, key: str):
        """处理AHK监控按键按下（交互键A等）"""
        # 🔒 QueuedConnection 下事件可能跨状态转换迟到:STOPPED 时 monitor Hook 已注销,
        # 迟到的 down 不得把刚归零的 force-move 重新点亮(配对的 up 永远不会再来,
        # AHK 端 ForceMoveActive 会卡 true → 下轮开跑技能全被换成交互键)。
        # up 方向不设门禁 —— 它只会关闭状态,是安全方向。
        if self._state == MacroState.STOPPED:
            return
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
                # STOPPED 是安全方向：先提交本地状态，让 QueuedConnection 中迟到的
                # monitor/special/managed down 立即被门禁丢弃。其入口清理逐项容错，
                # 不会因单个组件异常留下“状态已停但清理被截断”的旧问题。
                stopped_precommitted = new_state == MacroState.STOPPED
                if stopped_precommitted:
                    self._state = MacroState.STOPPED
                try:
                    entered = self._on_state_enter(new_state, from_state=old_state)
                except Exception as e:
                    LOG_ERROR(f"[状态转换] _on_state_enter 异常: {e}")
                    import traceback

                    LOG_ERROR(
                        f"[状态转换] _on_state_enter 异常详情:\n{traceback.format_exc()}"
                    )
                    if not stopped_precommitted:
                        self._rollback_failed_state_entry(new_state, old_state)
                    return False

                if entered is False:
                    LOG_ERROR(
                        f"[状态转换] 状态入口拒绝转换: {old_state} → {new_state}"
                    )
                    return False

                # 可运行状态在入口逻辑全部成功后才提交，外部观察者不会看到一个
                # 尚未完成初始化的 READY/RUNNING 伪状态。
                if not stopped_precommitted:
                    self._state = new_state
                LOG_INFO(f"[状态转换] 状态转换成功: {old_state} → {new_state}")

                try:
                    event_bus.publish(
                        "engine:state_changed", new_state=new_state, old_state=old_state
                    )
                    event_bus.publish(f"engine:macro_{new_state.name.lower()}")
                    # 在状态转换时，发布完整的状态更新
                    self._publish_status_update()
                except Exception as e:
                    LOG_ERROR(f"[状态转换] 事件发布异常: {e}")
                    import traceback

                    LOG_ERROR(f"[状态转换] 事件发布异常详情:\n{traceback.format_exc()}")
                    # 即使事件发布失败，状态转换也算成功
                    pass

                # OSD/UI 通知失败不回滚已经完成的运行时转换。
                try:
                    self._update_osd_visibility()
                except Exception as e:
                    LOG_ERROR(f"[状态转换] 更新 OSD 可见性失败: {e}")

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
            # 两阶段 READY：先在 AHK 标记主模式已 armed，但保持输入闸门关闭。
            # 物理 F8 从此刻起就是 stop-only，即使首个动态 Hook 尚未登记。
            if not self._arm_main_mode("进入 READY"):
                return False

            # 进入READY状态时注册所有动态热键
            if not self._register_secondary_hotkeys():
                raise RuntimeError("动态热键注册未完整成功")

            # 激活目标窗口并做准备动作
            if self.input_handler.activate_target_window() is False:
                raise RuntimeError("目标窗口激活失败")
            self.input_handler.start()

            self.skill_manager.prepare_border_only()  # 预计算边框
            self.border_manager.enable_debug_save()

            # 收集资源区域配置，用于模板截取
            resource_regions = self._collect_resource_regions()
            ready_frame = self.border_manager.capture_once_for_debug_and_cache(
                self._capture_interval_ms(), resource_regions
            )

            # 通知ResourceManager截取HSV模板
            if self.resource_manager and resource_regions:
                if ready_frame is not None:
                    self.resource_manager.capture_template_hsv(ready_frame)
                else:
                    LOG_ERROR("[ResourceManager] READY阶段未获取到帧，跳过HSV模板截取")

            # 锁定 PaddleOCR 数字框位置（仅 text_ocr + ocr_engine==paddle 的资源生效）
            # 放在 resource_regions 守卫之外：paddle 配置可能不贡献 HSV 矩形区域
            if self.resource_manager:
                if ready_frame is not None:
                    self.resource_manager.lock_ocr_number_position(ready_frame)
                else:
                    LOG_ERROR("[OCR锁定] READY阶段未获取到帧，跳过PaddleOCR数字框锁定")

            # 所有 Hook 和可能较慢的捕获/OCR 准备完成后才开闸。失败 = AHK
            # 不可达或期间已有物理 F8 stop latch；拒绝提交 READY 并延后清理。
            if not self._open_runtime_gate("进入 READY"):
                return False

        elif state == MacroState.RUNNING:
            # 开闸必须先于恢复生产者:resume/start 会重新声明**非空**持键集合并
            # START_MACRO,这两个命令在闸门关闭时会被 AHK 拒绝。
            # PAUSED 是关着闸的;READY→RUNNING 时闸门已开,重开是幂等空操作。
            if not self._open_runtime_gate("进入 RUNNING"):
                return False

            # AHK 在开闸时已按本地物理 monitor 账本恢复强制移动。
            # Python 仍回发自己的 OSD 账本作纵深对齐；AHK 只触发重算，
            # 不盲写迟到的 true/false，因此不会覆盖更新的物理边沿。
            if hasattr(self.input_handler, "set_force_move_state"):
                force_move_synced = self.input_handler.set_force_move_state(
                    self._force_move_active
                )
                if force_move_synced is False:
                    raise RuntimeError("强制移动状态重对齐失败")

            # 如果是从暂停状态恢复，调用resume；否则启动子系统
            if from_state == MacroState.PAUSED:
                LOG_INFO("[状态转换] 从暂停状态恢复")
                if self._prepared_mode == "combat":
                    resumed = self.skill_manager.resume()
                    if (
                        resumed is False
                        or getattr(self.skill_manager, "_is_running", True) is False
                        or getattr(self.skill_manager, "_is_paused", False)
                    ):
                        raise RuntimeError("技能管理器恢复失败")
                elif self._prepared_mode == "pathfinding":
                    self.pathfinding_manager.resume()
                    pause_event = getattr(self.pathfinding_manager, "_pause_event", None)
                    if (
                        getattr(self.pathfinding_manager, "is_running", True) is False
                        or (pause_event is not None and pause_event.is_set())
                    ):
                        raise RuntimeError("寻路管理器恢复失败")
                self.resource_manager.resume()
                self.border_manager.resume_capture()
                if hasattr(self.resource_manager, "is_running"):
                    if not self.resource_manager.is_running():
                        raise RuntimeError("资源管理器恢复失败")
                if (
                    getattr(self.border_manager, "running", True) is False
                    or getattr(self.border_manager, "paused", False)
                ):
                    raise RuntimeError("屏幕捕获恢复失败")
            else:
                # 首次启动
                self._start_subsystems_based_on_mode()

        elif state == MacroState.PAUSED:
            # 关闸 = AHK 端原子停止屏障:同一条 WM_COPYDATA 内完成
            # 清队(-1)+停宏+释放全部持键,不给 ProcessQueue/MacroTick 留空窗。
            # 同时挡住两类竞态:join 超时的在飞回调再入队;暂停期间管理键重映射
            # (物理按键被 $Hook 吞掉,无输出 —— PAUSED 就是"完全停下")。
            # 旧的 clear_queue() 由屏障取代;skill_manager.pause() 的空集合声明
            # 与 stop_macro 属安全清理命令,闸门放行。
            self._run_safety_step(
                "[暂停] 关闭运行时闸门",
                lambda: self.input_handler.set_accepting_actions(False),
            )
            if self._prepared_mode == "combat":
                self._run_safety_step("[暂停] 暂停技能管理器", self.skill_manager.pause)
            elif self._prepared_mode == "pathfinding":
                self._run_safety_step(
                    "[暂停] 暂停寻路管理器", self.pathfinding_manager.pause
                )
            self._run_safety_step("[暂停] 暂停资源管理器", self.resource_manager.pause)
            self._run_safety_step("[暂停] 暂停捕获", self.border_manager.pause_capture)

        elif state == MacroState.STOPPED:
            self._enter_stopped_state()

        return True

    @staticmethod
    def _run_safety_step(label: str, operation) -> bool:
        """运行一个停机/回滚步骤；单步失败不得截断后续安全动作。"""
        try:
            result = operation()
            if result is False:
                LOG_ERROR(f"{label}失败")
                return False
            return True
        except Exception as e:
            LOG_ERROR(f"{label}失败: {e}")
            return False

    def _enter_stopped_state(self):
        """执行幂等 STOPPED 安全清理，任何单步异常都不会阻断后续步骤。"""
        # 第一步必须是 AHK 原子屏障。set_accepting_actions(False) 内部强制发送，
        # 在同一条消息里关闸、清队、停宏并释放全部持键。
        self._run_safety_step(
            "[停止] 关闭运行时闸门",
            lambda: self.input_handler.set_accepting_actions(False),
        )

        # Hook 注销可能吞掉物理 key-up；Python 本地账本也必须立即向安全方向复位。
        self._force_move_active = False
        self._stationary_mode_active = False
        stationary_config = self._global_config.get("stationary_mode_config", {}) or {}
        stationary_type = stationary_config.get("mode_type", "shift_modifier")
        self._run_safety_step(
            "[停止] 复位原地模式",
            lambda: self.input_handler.set_stationary_mode(False, stationary_type),
        )
        if hasattr(self.input_handler, "set_drop_non_emergency"):
            self._run_safety_step(
                "[停止] 复位特殊键丢弃状态",
                lambda: self.input_handler.set_drop_non_emergency(False),
            )

        # 停生产者。每个组件独立保护，前一个 stop 抛错也不能阻止最终清场。
        self._run_safety_step("[停止] 停止技能管理器", self.skill_manager.stop)
        self._run_safety_step("[停止] 停止寻路管理器", self.pathfinding_manager.stop)
        self._run_safety_step("[停止] 停止资源管理器", self.resource_manager.stop)

        if self._run_safety_step("[停止] 清空 AHK 队列", self.input_handler.clear_queue):
            LOG_INFO("[停止] 已清空 AHK 按键队列(含 emergency)")

        self._run_safety_step("[停止] 停止捕获", self.border_manager.stop)

        if self._run_safety_step(
            "[热键管理] 清理动态热键",
            self.input_handler.clear_all_configurable_hooks,
        ):
            LOG_INFO("[热键管理] 已清理所有动态热键（F8/F7/F9 永久根热键保留）")
            LOG_INFO("[热键管理] AHK进程保持运行，F8/F7/F9 永久根热键保持监听")

        # Hook 清理前仍可能有最后一条管理键动作入队，注销后再清一次封住窗口。
        self._run_safety_step("[停止] 注销 Hook 后补清队列", self.input_handler.clear_queue)
        self._run_safety_step(
            "[停止] 复位 BOSS 模式",
            lambda: self._set_boss_mode_active(False, notify=False),
        )
        self._prepared_mode = "none"

        self._run_safety_step(
            "[干跑模式] STOPPED 同步干跑标志", self._sync_stopped_debug_mode
        )
        LOG_INFO("[状态转换] STOPPED状态处理完成，等待F8重新启动")

    def _sync_stopped_debug_mode(self):
        debug_enabled = bool(
            (self._global_config.get("debug_mode") or {}).get("enabled", False)
        )
        if bool(self.input_handler.dry_run_mode) != debug_enabled:
            self.input_handler.set_dry_run_mode(debug_enabled)
            LOG_INFO(f"[干跑模式] 已在 STOPPED 同步为配置值: {debug_enabled}")

    def _rollback_failed_state_entry(
        self, failed_state: MacroState, old_state: MacroState
    ):
        """补偿入口阶段已产生的副作用，同时保持已提交状态不变。"""
        LOG_ERROR(f"[状态转换] 回滚未完成的状态入口: {old_state} → {failed_state}")

        if failed_state == MacroState.READY:
            # READY 只能从 STOPPED 进入；按完整 STOPPED 屏障撤销已注册 Hook、
            # 已打开的闸门及可能启动的捕获准备动作。
            self._enter_stopped_state()
            return

        if failed_state != MacroState.RUNNING:
            return

        # RUNNING 入口可能已经启动/恢复了部分生产者，先原子关闸止血。
        self._run_safety_step(
            "[状态回滚] 关闭运行时闸门",
            lambda: self.input_handler.set_accepting_actions(False),
        )

        if old_state == MacroState.PAUSED:
            if self._prepared_mode == "combat":
                self._run_safety_step(
                    "[状态回滚] 重新暂停技能管理器", self.skill_manager.pause
                )
            elif self._prepared_mode == "pathfinding":
                self._run_safety_step(
                    "[状态回滚] 重新暂停寻路管理器", self.pathfinding_manager.pause
                )
            self._run_safety_step(
                "[状态回滚] 重新暂停资源管理器", self.resource_manager.pause
            )
            self._run_safety_step(
                "[状态回滚] 重新暂停捕获", self.border_manager.pause_capture
            )
            return

        # READY → RUNNING 失败：停止任何已启动生产者，但保留 READY 的动态 Hook
        # 与准备模式，最后重新打开 READY 应有的运行时闸门。
        self._run_safety_step("[状态回滚] 停止技能管理器", self.skill_manager.stop)
        self._run_safety_step("[状态回滚] 停止寻路管理器", self.pathfinding_manager.stop)
        self._run_safety_step("[状态回滚] 停止资源管理器", self.resource_manager.stop)
        self._run_safety_step("[状态回滚] 停止捕获", self.border_manager.stop)
        self._run_safety_step(
            "[状态回滚] 清空 AHK 队列", self.input_handler.clear_queue
        )
        self._run_safety_step(
            "[状态回滚] 恢复 READY 运行时闸门",
            lambda: self._open_runtime_gate("RUNNING 入口失败后恢复 READY"),
        )

    def _capture_interval_ms(self) -> int:
        """「图像捕获间隔」的唯一读取口径:10..1000ms,默认 40。

        钳制放在读取处而不是加载处:_global_config 有两条赋值路径(F8 与
        _on_config_updated),读取处钳制两条都罩住。非法值要**可见**地纠正 ——
        静默用 0/负数会让捕获线程空转,静默用超大值会让检测"失明",都比报错难查。
        """
        raw = self._global_config.get("capture_interval", CAPTURE_INTERVAL_DEFAULT_MS)
        try:
            val = int(raw)
        except (TypeError, ValueError):
            LOG_ERROR(
                f"[捕获间隔] 配置值非法({raw!r}),回退默认 {CAPTURE_INTERVAL_DEFAULT_MS}ms"
            )
            return CAPTURE_INTERVAL_DEFAULT_MS
        clamped = min(max(val, CAPTURE_INTERVAL_MIN_MS), CAPTURE_INTERVAL_MAX_MS)
        if clamped != val:
            LOG_ERROR(
                f"[捕获间隔] 配置值 {val}ms 超出合法范围 "
                f"{CAPTURE_INTERVAL_MIN_MS}..{CAPTURE_INTERVAL_MAX_MS}ms,已钳制为 {clamped}ms"
            )
        return clamped

    def _start_subsystems_based_on_mode(self):
        """根据当前准备的模式，启动或恢复对应的子系统。"""
        LOG_INFO(f"[状态转换] 启动子系统，当前模式: {self._prepared_mode}")

        # 统一启动屏幕捕获(捕获间隔与 READY 期一次性捕获同一口径,见 _capture_interval_ms)
        self.border_manager.start_capture_loop(
            interval_ms=self._capture_interval_ms(), capture_region=None
        )
        if getattr(self.border_manager, "running", True) is False:
            raise RuntimeError("屏幕捕获启动失败")

        if self._prepared_mode == "combat":
            LOG_INFO("[状态转换] 启动技能管理器")
            self._set_boss_mode_active(False, notify=False)
            started = self.skill_manager.start()
            if (
                started is False
                or getattr(self.skill_manager, "_is_running", True) is False
            ):
                raise RuntimeError("技能管理器启动失败")
        elif self._prepared_mode == "pathfinding":
            LOG_INFO("[状态转换] 启动寻路管理器")
            self.pathfinding_manager.start()
            if getattr(self.pathfinding_manager, "is_running", True) is False:
                raise RuntimeError("寻路管理器启动失败")

        # 恢复捕获（如果之前是暂停状态）
        self.border_manager.resume_capture()

        # 启动资源管理器（如果有启用配置）
        self.resource_manager.start()
        if hasattr(self.resource_manager, "is_running"):
            if not self.resource_manager.is_running():
                raise RuntimeError("资源管理器启动失败")

        LOG_INFO("[状态转换] 子系统启动完成")

    def _publish_status_update(self):
        """发布当前完整的状态信息，确保状态同步"""
        boss_mode_available = (
            self._prepared_mode == "combat"
            and bool(self._global_config.get("boss_mode_hotkey"))
            and not self._global_config.get("sequence_enabled", False)
        )
        status_info = {
            "state": self._state,
            "stationary_mode": self._stationary_mode_active,
            "force_move_active": self._force_move_active,
            "boss_mode": self._boss_mode_active,
            "boss_mode_available": boss_mode_available,
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
                    # timeout 不等于 AHK 没执行。熔断后只允许一次无副作用 PING
                    # 探测恢复；失败继续 fail-closed，成功后才允许 READY 初始化。
                    if not self._recover_ahk_transport_for_start():
                        LOG_ERROR(
                            "[MacroEngine] AHK 通信仍不可用或处于恢复冷却期，"
                            "本次启动已拒绝；请稍后重试，持续失败时重启应用。"
                        )
                        return
                    # 🔧 三模式硬互斥(combat/pathfinding/洗练):洗练运行时拒绝启动主功能,
                    # 与 _on_f9_key_press 保持对称(F9 也有此检查)
                    if self.affix_reroll_manager.status.is_running:
                        LOG_INFO("[MacroEngine] 洗练进行中,无法启动主功能。请先按 F7 停止洗练。")
                        return
                    LOG_INFO("【热键】 F8 - 从 STOPPED状态启动")
                    if full_config:
                        normalize_config_keys(full_config)
                        self._skills_config = full_config.get("skills", {})
                        self._global_config = full_config.get("global", {})
                        self.sound_manager.update_config(self._global_config)
                        
                        # 设置输入模式
                        input_mode = self._global_config.get("input_mode", "direct")
                        if hasattr(self.input_handler, "set_send_mode"):
                            try:
                                self.input_handler.set_send_mode(input_mode)
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
            hp_region = self.border_manager.get_resource_region_from_config(hp_config)
            if hp_region:
                resource_regions["hp_region"] = hp_region

        # MP区域
        mp_config = resource_config.get("mp_config", {})
        if mp_config.get("enabled", False):
            mp_region = self.border_manager.get_resource_region_from_config(mp_config)
            if mp_region:
                resource_regions["mp_region"] = mp_region

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
            # 🔧 干跑标志只在 STOPPED 时随配置生效。set_debug_mode 已拦住复选框那条路,
            # 但"运行中加载另一个配置文件"同样会走到这里 —— 若在 RUNNING/PAUSED 翻转干跑,
            # Python 与 AHK 端(宏循环/持键是跨进程持久状态)会永久失配。
            # OSD 显示(_is_debug_mode_active)不受此限制,可随时跟随配置变化。
            if self._state == MacroState.STOPPED:
                self.input_handler.set_dry_run_mode(debug_enabled)
                LOG_INFO(
                    f"[DEBUG MODE] _on_config_updated: 干跑模式已设置为 {debug_enabled}"
                )
            elif bool(self.input_handler.dry_run_mode) != bool(debug_enabled):
                LOG_ERROR(
                    f"[DEBUG MODE] 忽略运行中({self._state.name})的干跑模式变更"
                    f"({self.input_handler.dry_run_mode} → {debug_enabled}):"
                    f"请先按 F8 停止。配置已记录,进入 STOPPED 时统一生效"
                    f"(见 _on_state_enter 的 STOPPED 第 5 步)。"
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

            # 强制移动期间不被替换的白名单(位移技能等,如 RButton 闪现)
            # 总是下发,即使空列表也要清空 AHK 端旧值
            passthrough_keys = stationary_config.get(
                "force_move_passthrough_keys", []
            ) or []
            self.input_handler.set_force_move_passthrough_keys(passthrough_keys)
            if passthrough_keys:
                LOG_INFO(
                    f"[强制移动白名单] 已设置到AHK: {passthrough_keys} "
                    f"(强制移动期间这些键正常发送,不替换为 {stationary_config.get('force_move_replacement_key', '')})"
                )
            else:
                LOG_INFO("[强制移动白名单] 已清空AHK配置(强制移动期间非紧急键都替换)")

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

            raw_resume_delay = global_config.get(
                "special_key_resume_delay_ms", SPECIAL_KEY_RESUME_DELAY_DEFAULT_MS
            )
            try:
                resume_delay = int(raw_resume_delay)
            except (TypeError, ValueError):
                LOG_ERROR(
                    f"[特殊键恢复] 配置值非法({raw_resume_delay!r}),"
                    f"回退默认 {SPECIAL_KEY_RESUME_DELAY_DEFAULT_MS}ms"
                )
                resume_delay = SPECIAL_KEY_RESUME_DELAY_DEFAULT_MS
            clamped_resume_delay = min(
                max(resume_delay, SPECIAL_KEY_RESUME_DELAY_MIN_MS),
                SPECIAL_KEY_RESUME_DELAY_MAX_MS,
            )
            if clamped_resume_delay != resume_delay:
                LOG_ERROR(
                    f"[特殊键恢复] 配置值 {resume_delay}ms 超出合法范围 "
                    f"{SPECIAL_KEY_RESUME_DELAY_MIN_MS}.."
                    f"{SPECIAL_KEY_RESUME_DELAY_MAX_MS}ms,"
                    f"已钳制为 {clamped_resume_delay}ms"
                )
            # 始终下发 0,否则从带保护窗口的配置切换到默认配置会在 AHK 留下旧值。
            batch_config["special_key_resume_delay_ms"] = clamped_resume_delay

            raw_key_press_duration = global_config.get(
                "key_press_duration", KEY_PRESS_DURATION_DEFAULT_MS
            )
            try:
                key_press_duration = int(raw_key_press_duration)
            except (TypeError, ValueError):
                LOG_ERROR(
                    f"[按键持续时间] 配置值非法({raw_key_press_duration!r}),"
                    f"回退默认 {KEY_PRESS_DURATION_DEFAULT_MS}ms"
                )
                key_press_duration = KEY_PRESS_DURATION_DEFAULT_MS
            clamped_key_press_duration = min(
                max(key_press_duration, KEY_PRESS_DURATION_MIN_MS),
                KEY_PRESS_DURATION_MAX_MS,
            )
            if clamped_key_press_duration != key_press_duration:
                LOG_ERROR(
                    f"[按键持续时间] 配置值 {key_press_duration}ms 超出合法范围 "
                    f"{KEY_PRESS_DURATION_MIN_MS}..{KEY_PRESS_DURATION_MAX_MS}ms,"
                    f"已钳制为 {clamped_key_press_duration}ms"
                )
            batch_config["key_press_duration"] = clamped_key_press_duration
            
            # 只有在有配置更新时才发送
            if batch_config and hasattr(self.input_handler, "batch_update_config"):
                self.input_handler.batch_update_config(batch_config)
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
        if hasattr(self.input_handler, "set_stationary_mode"):
            stationary_config = self._global_config.get("stationary_mode_config", {})
            mode_type = stationary_config.get("mode_type", "shift_modifier")

            self.input_handler.set_stationary_mode(
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
        if hasattr(self.input_handler, "set_force_move_state"):
            self.input_handler.set_force_move_state(True)

        self._publish_status_update()
        LOG_INFO("[交互模式] 已激活")

    def _on_force_move_key_release(self):
        """交互/强制移动热键释放事件 - 松开取消"""
        self._force_move_active = False

        # 通知AHK强制移动状态变化
        if hasattr(self.input_handler, "set_force_move_state"):
            self.input_handler.set_force_move_state(False)

        self._publish_status_update()
        LOG_INFO("[交互模式] 已取消")

    def _set_boss_mode_active(self, active: bool, notify: bool = True):
        """设置运行时 BOSS 模式状态。该状态不写入配置。"""
        self._boss_mode_active = bool(active)
        if hasattr(self.skill_manager, "set_boss_mode_active"):
            self.skill_manager.set_boss_mode_active(self._boss_mode_active)
        if notify:
            LOG_INFO(f"[BOSS模式] {'开启' if self._boss_mode_active else '关闭'}")
            event_bus.publish("engine:boss_mode_changed", self._boss_mode_active)
            self._publish_status_update()

    def _toggle_boss_mode(self):
        """BOSS 模式热键:只在技能模式运行/暂停时切换。"""
        if self._prepared_mode != "combat" or self._global_config.get("sequence_enabled", False):
            LOG_INFO("[BOSS模式] 当前非技能模式,忽略切换")
            return
        if self._state not in (MacroState.RUNNING, MacroState.PAUSED):
            LOG_INFO("[BOSS模式] 仅在 RUNNING/PAUSED 状态切换")
            return
        self._set_boss_mode_active(not self._boss_mode_active)

    def get_current_state(self) -> MacroState:
        return self._state

    def prepare_border_only(self) -> bool:
        return self._set_state(MacroState.READY)

    def stop_macro(self) -> bool:
        return self._set_state(MacroState.STOPPED)

    def _open_runtime_gate(self, context: str) -> bool:
        """打开 AHK 运行时闸门;失败则拒绝进入可运行状态并延后回退 STOPPED。

        闸门开不了 = AHK 不可达(死亡探测由 _check_send 内部已触发)。此时若继续
        进入 READY/RUNNING,所有后续命令都会静默失效,用户却以为系统在跑。
        回退用 QTimer.singleShot(0) 延后:本方法在 _on_state_enter 内被调用,
        同步 _set_state 会重入(同 _on_ahk_process_died 的处理方式)。
        """
        try:
            ok = bool(self.input_handler.set_accepting_actions(True))
        except Exception as e:
            LOG_ERROR(f"[闸门] {context}: 打开运行时闸门异常: {e}")
            ok = False
        if not ok:
            LOG_ERROR(f"[闸门] {context}: 打开运行时闸门失败,拒绝进入可运行状态")
            self._schedule_stopped_after_gate_failure()
        return ok

    def _arm_main_mode(self, context: str) -> bool:
        """在不开闸的前提下武装主模式；失败时重施 STOPPED 安全屏障。"""
        try:
            ok = bool(self.input_handler.arm_main_mode())
        except Exception as e:
            LOG_ERROR(f"[闸门] {context}: 武装主模式异常: {e}")
            ok = False
        if not ok:
            # SendMessageTimeoutW 超时不代表 AHK 没执行。即使 arm_main 随后迟到
            # 生效，也必须再发一轮幂等 STOPPED 清理把 armed 标记和动态 Hook 清掉。
            LOG_ERROR(f"[闸门] {context}: 武装主模式失败,拒绝进入可运行状态")
            self._schedule_stopped_after_gate_failure()
        return ok

    def _schedule_stopped_after_gate_failure(self):
        """延后重施 STOPPED 屏障，避免在状态入口内同步重入状态机。"""
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._force_stopped_after_gate_failure)
        except Exception as e:
            LOG_ERROR(f"[闸门] 调度回退 STOPPED 失败,直接停机: {e}")
            self._force_stopped_after_gate_failure()

    def _force_stopped_after_gate_failure(self):
        """闸门打开失败后的回退(主线程,延后执行):强制切回 STOPPED。"""
        try:
            with self._transition_lock:
                if self._state != MacroState.STOPPED:
                    self._set_state(MacroState.STOPPED)
                else:
                    # STOPPED→READY 的开闸消息可能已投递、只是发送方 500ms 超时。
                    # 本地状态从未提交 READY，此处仍必须再发一次 force=false 屏障；
                    # 否则迟到的 true 会在 Python 显示 STOPPED 时把 AHK 闸门重新打开。
                    self._enter_stopped_state()
        except Exception as e:
            LOG_ERROR(f"[闸门] 回退 STOPPED 失败: {e}")

    def _on_ahk_process_died(self, key: str = ""):
        """AHK 子进程意外退出的应急处理(已在 GUI 线程):告警 + 延后停机。

        停机用 QTimer.singleShot(0) 延后到下一轮事件循环:命令发送失败的探测可能发生在状态
        转换内部(_set_state → _on_state_enter 里的控制命令发送失败),若此处同步 stop 会重入
        _set_state 造成状态错乱。延后执行可避开重入,且最终一定落到 STOPPED。
        AHK 既死,Python 已无法经 WM_COPYDATA 发释放命令,游戏内卡死的持久按住键留待 OS 级兜底。
        """
        LOG_ERROR("[引擎] ⚠️ 检测到 AHK 子进程已退出,强制停止主功能并告警。")
        # 告警:不可错过的提示音 + 既有声音反馈(若启用)
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
        try:
            if self.sound_manager:
                self.sound_manager.play("goodbye")
        except Exception as e:
            LOG_ERROR(f"[引擎] AHK 崩溃告警音播放失败: {e}")
        try:
            event_bus.publish("engine:ahk_died_notice")
        except Exception:
            pass
        # 延后停机,避免在状态转换内部同步重入 _set_state
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._stop_due_to_ahk_death)
        except Exception as e:
            LOG_ERROR(f"[引擎] 调度 AHK 崩溃停机失败,改为直接停机: {e}")
            self._stop_due_to_ahk_death()

    def _stop_due_to_ahk_death(self):
        """实际停机(主线程,延后执行):AHK 崩溃/挂死后强制切回 STOPPED。"""
        try:
            if self._state != MacroState.STOPPED:
                self.stop_macro()
        except Exception as e:
            LOG_ERROR(f"[引擎] AHK 崩溃后强制停止失败: {e}")

    def _on_ahk_transport_failed(self, key: str = ""):
        """AHK 传输挂死(进程活着但命令 500ms 无响应)的应急处理(已在 GUI 线程)。

        与 ahk_process_died 汇入同一个延迟 STOPPED 流程,并暂时熔断 READY 入口。
        冷却后 F8 只发送无副作用 PING；成功才恢复，不自动重放超时业务命令，
        也不通过 TerminateProcess 强杀可能仍持键的 AHK。
        """
        self._ahk_transport_failed = True
        reason = key or "unknown"
        LOG_ERROR(
            f"[引擎] ⚠️ AHK 通信失效({reason})。已停止主功能并暂时熔断启动入口；"
            "冷却后再次按 F8 会先探测通信，持续失败请重启本应用。"
        )
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass
        try:
            if self.sound_manager:
                self.sound_manager.play("goodbye")
        except Exception as e:
            LOG_ERROR(f"[引擎] AHK 挂死告警音播放失败: {e}")
        try:
            event_bus.publish("engine:ahk_transport_notice")
        except Exception:
            pass
        # 与 ahk_process_died 相同:延后停机,避免在状态转换内部同步重入 _set_state
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._stop_due_to_ahk_death)
        except Exception as e:
            LOG_ERROR(f"[引擎] 调度 AHK 挂死停机失败,改为直接停机: {e}")
            self._stop_due_to_ahk_death()

    def _recover_ahk_transport_for_start(self) -> bool:
        """在 STOPPED→READY 前解除可恢复熔断，不发送任何业务动作。"""
        if not self._ahk_transport_failed:
            return True

        recover = getattr(self.input_handler, "recover_transport", None)
        if not callable(recover):
            return False
        try:
            recovered = bool(recover())
        except Exception as e:
            LOG_ERROR(f"[引擎] AHK 通信恢复探测异常: {e}")
            return False
        if not recovered:
            return False

        self._ahk_transport_failed = False
        LOG_INFO("[引擎] AHK 通信探测成功，已解除启动熔断")
        return True

    def _on_ahk_transport_recovered(self, key: str = ""):
        """同步输入层的恢复结果；幂等安全。"""
        if self._ahk_transport_failed:
            LOG_INFO("[引擎] 收到 AHK 通信恢复通知")
        self._ahk_transport_failed = False

    def _on_skill_input_sync_failed(self, reason: str = ""):
        """运行中输入配置提交失败：延后进入 STOPPED，避免嵌套事件重入状态转换。"""
        LOG_ERROR(f"[引擎] 技能输入状态同步失败({reason or 'unknown'})，强制停止主功能")
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._stop_due_to_skill_input_failure)
        except Exception as e:
            LOG_ERROR(f"[引擎] 调度输入同步失败停机异常，改为直接停机: {e}")
            self._stop_due_to_skill_input_failure()

    def _stop_due_to_skill_input_failure(self):
        """输入热更新失败后的实际停机回调。"""
        try:
            if self._state != MacroState.STOPPED:
                self.stop_macro()
        except Exception as e:
            LOG_ERROR(f"[引擎] 输入同步失败后强制停止异常: {e}")

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

    def set_debug_mode(self, enabled: bool) -> bool:
        """设置DEBUG MODE配置标志，并触发配置更新。

        返回是否被接受。🔧 只允许在 STOPPED 状态切换:
        DEBUG 同时驱动干跑(dry_run)标志,而干跑标志是 Python 端本地状态,AHK 端的宏循环
        与持键却是跨进程持久状态。运行中翻转干跑会让两端永久失配(AHK 还在真实发键,
        Python 却认为自己什么都没发,且清理命令若被干跑吞掉就再也停不下来)。
        与之配套的不变量:stop_macro / set_skill_hold_keys(空集) / clear_queue 等
        **安全清理命令永不被干跑拦截**(见 AHKInputHandler)。
        """
        try:
            LOG_INFO(f"[DEBUG MODE] 收到设置DEBUG MODE请求: {enabled}")

            if self._state != MacroState.STOPPED:
                LOG_ERROR(
                    f"[DEBUG MODE] 拒绝在 {self._state.name} 状态下切换 DEBUG/干跑模式。"
                    f"请先按 F8 停止后再切换(运行中翻转会造成 Python 与 AHK 状态失配)。"
                )
                # 让 UI 把复选框回滚到引擎的真实值
                self._publish_status_update()
                return False

            # 更新配置
            if "debug_mode" not in self._global_config:
                self._global_config["debug_mode"] = {}
            self._global_config["debug_mode"]["enabled"] = enabled

            # 发布配置更新事件，让所有订阅者（包括自身）响应
            event_bus.publish(
                "engine:config_updated", self._skills_config, self._global_config
            )
            LOG_INFO(f"[DEBUG MODE] DEBUG MODE配置已更新并发布事件: {enabled}")
            return True
        except Exception as e:
            LOG_ERROR(f"[DEBUG MODE] 设置DEBUG MODE异常: {e}")
            import traceback

            LOG_ERROR(f"[DEBUG MODE] 设置DEBUG MODE异常详情:\n{traceback.format_exc()}")
            return False

    @staticmethod
    def _validated_config_sections(
        config_data: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """验证完整配置的最小结构并返回 skills/global 两段。"""
        if not isinstance(config_data, dict):
            raise ValueError("完整配置顶层必须是 JSON 对象")

        skills_config = config_data.get("skills", {})
        global_config = config_data.get("global", {})
        if not isinstance(skills_config, dict):
            raise ValueError("配置字段 'skills' 必须是 JSON 对象")
        if not isinstance(global_config, dict):
            raise ValueError("配置字段 'global' 必须是 JSON 对象")
        return skills_config, global_config

    def load_config(self, config_file: str) -> bool:
        """加载并验证完整配置,成功后才提交到运行态。"""
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

            skills_config, global_config = self._validated_config_sections(
                config_data
            )
            # 🔧 统一归一化所有按键字段为 AHK 标准名(单一可信源,见 utils/key_names.py)
            normalize_config_keys(config_data)
        except Exception as e:
            LOG_ERROR(f"加载配置文件 '{config_file}' 失败: {e}")
            # 暂存失败结果:启动期(GUI 尚未订阅)这条 publish 会被丢弃,
            # 之后由 ui:request_current_config 握手一次性重放,保证失败可见。
            self._pending_load_error = (config_file, str(e))
            event_bus.publish(
                "engine:config_load_result", config_file, False, str(e)
            )
            return False

        self._pending_load_error = None
        self._skills_config = skills_config
        self._global_config = global_config
        self._remember_config_file(config_file)

        try:
            self.sound_manager.update_config(self._global_config)
        except Exception as e:
            # 文件与运行配置均已成功提交,附属声音配置失败单独告警。
            LOG_ERROR(f"加载后更新声音配置失败: {e}")

        event_bus.publish(
            "engine:config_updated", self._skills_config, self._global_config
        )
        event_bus.publish("engine:config_load_result", config_file, True, "")
        return True

    def save_full_config(self, file_path: str, full_config: Dict[str, Any]) -> bool:
        """原子保存完整配置,成功后才提交到运行态。

        磁盘写入是提交边界:写入/序列化失败时保留当前引擎配置和当前文件名,
        避免 UI/运行态显示新配置而磁盘仍是旧配置。F8 的临时运行同步不走这里,
        因此不会未经用户点击“保存”就覆盖配置文件。
        """
        try:
            skills_config, global_config = self._validated_config_sections(
                full_config
            )
            # 🔧 保存前归一化按键字段,保证写回磁盘的也是 AHK 标准名
            normalize_config_keys(full_config)
            self.config_manager.save_config(full_config, file_path)
        except Exception as e:
            LOG_ERROR(f"保存配置文件 '{file_path}' 失败: {e}")
            event_bus.publish(
                "engine:config_save_result", file_path, False, str(e)
            )
            return False

        # 只有原子落盘成功后才更新运行态与当前配置文件。
        # 成功保存 = 运行态与磁盘重新一致,启动期暂存的加载失败不再适用。
        self._pending_load_error = None
        self._skills_config = skills_config
        self._global_config = global_config
        self._remember_config_file(file_path)

        try:
            self.sound_manager.update_config(self._global_config)
        except Exception as e:
            # 配置已经成功保存,声音反馈更新失败不应谎报成“保存失败”。
            LOG_ERROR(f"保存后更新声音配置失败: {e}")

        event_bus.publish(
            "engine:config_updated", self._skills_config, self._global_config
        )
        event_bus.publish("engine:config_save_result", file_path, True, "")
        return True

    def get_pending_load_error(self) -> Optional[Tuple[str, str]]:
        """启动期暂存的加载失败 (config_file, error);无则 None。

        供 GUI 初始化时判断"当前文件名标签是否在谎报"(加载失败时运行态是
        初始空配置,不应把损坏文件名当作已加载展示)。
        """
        return self._pending_load_error

    def _handle_ui_request_current_config(self):
        """处理UI请求当前配置的事件"""
        LOG_INFO("[MacroEngine] 收到UI请求当前配置事件，发布配置更新")
        event_bus.publish(
            "engine:config_updated", self._skills_config, self._global_config
        )
        # 一次性重放启动期丢失的加载失败:__init__ 里 load_config 发布
        # config_load_result(False) 时 GUI 还没订阅(main.py 先建引擎后建窗口),
        # 事件被 EventBus 丢弃。此刻 GUI 已订阅完并主动请求配置,补发一次。
        # GUI 侧 handler 走 QTimer.singleShot(0) 延后弹窗,不在 publish 链内开模态;
        # 清空后不会因重复请求二次弹窗。
        if self._pending_load_error is not None:
            file_path, error = self._pending_load_error
            self._pending_load_error = None
            event_bus.publish("engine:config_load_result", file_path, False, error)

    def _generate_default_config(self) -> Dict[str, Any]:
        """生成包含默认值的完整配置"""
        default_skills = {}
        for i in range(1, 9):
            default_skills[f"Skill{i}"] = {
                "Enabled": False,
                "Key": str(i),
                "Priority": False,
                "BossOnly": False,
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
            "queue_processor_interval": 50,
            "cooldown_checker_interval": 100,
            "capture_interval": 40,
            "key_press_duration": KEY_PRESS_DURATION_DEFAULT_MS,
            "special_key_resume_delay_ms": 0,
            "sound_feedback_enabled": False,
            "boss_mode_hotkey": "",
            "window_activation": {"enabled": False, "ahk_class": "", "ahk_exe": ""},
            "stationary_mode_config": {
                "mode_type": "block_mouse",
                "hotkey": "",
                "force_move_hotkey": "",
                "force_move_replacement_key": "f",
                "force_move_passthrough_keys": [],
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

        # 先断开外部事件入口，避免组件释放过程中旧引擎再次响应 F8/AHK/config 事件。
        self._unsubscribe_event_handlers()

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
