"""重构后的SkillManager - 专注于技能执行逻辑，具备自主调度能力"""

import threading
import time
from typing import Dict, Any, List, Optional
from queue import Queue, Empty
import numpy as np

from .ahk_input_handler import AHKInputHandler
from .event_bus import event_bus
from .states import MacroState
from .unified_scheduler import UnifiedScheduler
from ..utils.border_frame_manager import BorderFrameManager
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO
from ..utils.key_names import migrate_skill_sequence_to_steps
from ..utils.config_values import config_float, config_int


class SkillManager:
    """重构后的技能管理器 - 专注于技能执行逻辑，具备自主调度能力"""

    def __init__(
        self,
        input_handler: AHKInputHandler,
        macro_engine_ref,
        border_manager: BorderFrameManager,
        resource_manager=None,
        debug_display_manager=None,
    ):
        self.input_handler = input_handler
        self.border_frame_manager = border_manager
        self._macro_engine = macro_engine_ref
        self.resource_manager = resource_manager
        self.debug_display_manager = debug_display_manager

        self._skills_config: Dict[str, Dict[str, Any]] = {}
        
        # 🎯 方案2性能监控统计
        self._frame_usage_stats = {
            "total_frame_gets": 0,  # 总的get_current_frame调用次数
            "cached_frame_usage": 0,  # 使用缓存帧的次数
            "performance_ratio": 0.0,  # 性能优化比例
        }

        self._is_running = False
        self._is_paused = False
        # 配置热更新是一个跨 Python 调度器/AHK 输入层的事务。事务期间，已经
        # 进入回调但尚未真正发动作的生产者也必须在最后一道门上停住。
        self._config_update_in_progress = False
        self._cleanup_done = False

        # 线程安全配置
        self._config_lock = threading.Lock()
        self._config_generation = 0
        self._resource_condition_history = {}
        self._required_consecutive_checks = 2
        self._boss_mode_active = False

        # 注意:不再镜像"实际按住了哪些键"。TriggerMode=2 持键的唯一权威账本在 AHK 端
        # (SkillHeldKeys/SkillHeldOrder),Python 只声明期望集合,见 _sync_skill_hold_keys。

        # 自主调度相关属性
        self._global_config = {}

        # 统一调度器
        self.unified_scheduler = UnifiedScheduler()

        # 订阅MacroEngine事件
        self._setup_event_subscriptions()
        # 注意:初始化阶段不下发持键声明;期望持键只在 start/pause/resume/stop 或配置热更新时声明一次

    def _setup_event_subscriptions(self):
        """设置事件订阅"""
        # 移除对engine:state_changed的订阅，避免与MacroEngine的直接调用产生竞态条件
        event_bus.subscribe("engine:config_updated", self._on_config_updated)

        # 🔧 已移除 scheduler_pause_requested / scheduler_resume_requested 订阅:
        # 管理按键期间暂停整个 UnifiedScheduler 会连 resource_checker 一起停(HP/MP 救命
        # 药剂检测停摆),且 resume() 会重置**所有**任务的 next_run_time —— 频繁按管理键
        # 时长周期任务与资源检测会被无限推迟(饿死)。管理按键的输入独占语义已由 AHK 端
        # HandleManagedKey(清非紧急队列 + delay_clear 期间持续清队)完整保证,Python 侧
        # 再暂停调度器不产生额外效果,只贡献相位漂移。

    def _on_config_updated(self, skills_config, global_config):
        """把技能配置和全局配置作为一个最终状态事务应用。"""
        if getattr(self, "_cleanup_done", False):
            return
        self._apply_config_update(skills_config, global_config)
    
    def _start_autonomous_scheduling(self):
        """使用统一调度器启动所有定时任务"""
        if not self._is_running:
            return

        # 启动统一调度器
        if not self.unified_scheduler.get_status()["running"]:
            self.unified_scheduler.start()
            LOG_INFO("[统一调度器] 启动成功")

        # 设置所有定时任务
        self._setup_all_scheduled_tasks()

    def _setup_all_scheduled_tasks(self):
        """根据配置，互斥地设置定时任务到统一调度器"""
        # 清除现有任务
        self.unified_scheduler.clear_all_tasks()

        is_sequence_mode = self._global_config.get("sequence_enabled") is True

        if is_sequence_mode:
            # 宏模式由 AHK 端解释器执行,Python 调度器只保留资源检测任务。
            LOG_INFO("[统一调度器] 进入宏模式，宏步骤由 AHK 端循环执行")
        else:
            # 技能模式：添加定时和冷却任务
            # 1. 添加定时技能任务
            self._setup_timed_skills_tasks()

            # 2. 添加冷却检查任务
            cooldown_interval = self._global_interval_seconds(
                "cooldown_checker_interval", 100
            )
            self.unified_scheduler.add_task(
                "cooldown_checker", cooldown_interval, self.check_cooldowns
            )
            LOG_INFO(
                f"[统一调度器] 进入技能模式，添加冷却检查任务，间隔: {cooldown_interval:.3f}s"
            )

        # 3. 添加资源管理任务（独立调度，序列/技能模式都需要 HP/MP 自动药剂）
        if self.resource_manager:
            raw_resource_config = self._global_config.get(
                "resource_management", {}
            )
            resource_config = (
                raw_resource_config
                if isinstance(raw_resource_config, dict)
                else {}
            )
            resource_interval = self._milliseconds_to_seconds(
                resource_config.get("check_interval", 200)
            )
            if resource_interval is None:
                LOG_ERROR("[统一调度器] 资源检查间隔无效，回退为 200ms")
                resource_interval = 0.2
            self.unified_scheduler.add_task(
                "resource_checker", resource_interval, self.check_resources
            )
            LOG_INFO(
                f"[统一调度器] 添加资源管理任务，间隔: {resource_interval:.3f}s"
            )

    def _setup_timed_skills_tasks(self):
        """设置定时技能任务"""
        timed_skills_count = 0
        with self._config_lock:
            for name, config in self._skills_config.items():
                if not isinstance(config, dict):
                    LOG_ERROR(f"[统一调度器] 技能 {name} 配置不是对象，跳过")
                    continue
                trigger_mode = self._coerce_mode(config.get("TriggerMode", 0))
                if config.get("Enabled") is True and trigger_mode == 0:
                    interval = self._milliseconds_to_seconds(
                        config.get("Timer", 1000)
                    )
                    if interval is None:
                        LOG_ERROR(f"[统一调度器] 定时技能 {name} 的 Timer 无效，跳过")
                        continue
                    task_id = f"timed_skill_{name}"

                    success = self.unified_scheduler.add_task(
                        task_id, interval, self.execute_timed_skill, args=(name,)
                    )

                    if success:
                        timed_skills_count += 1
                        LOG_INFO(
                            f"[统一调度器] 添加定时技能: {name}, 间隔: {interval:.3f}s"
                        )

        LOG_INFO(f"[统一调度器] 共添加 {timed_skills_count} 个定时技能任务")

    def _stop_autonomous_scheduling(self):
        """停止所有调度器线程并等待它们结束"""
        # 1. 停止统一调度器
        if self.unified_scheduler.get_status()["running"]:
            self.unified_scheduler.stop()
            LOG_INFO("[统一调度器] 已停止")

    def pause(self):
        """暂停所有技能活动"""
        self._is_paused = True
        # 释放所有技能持键(声明空集合) + 停止 AHK 端宏(释放宏持键)
        self._release_skill_hold_keys()
        self._stop_ahk_macro()

        # 暂停统一调度器
        if self.unified_scheduler.get_status()["running"]:
            self.unified_scheduler.pause()
            LOG_INFO("[统一调度器] 已暂停")

    def resume(self):
        """恢复所有技能活动"""
        if not self._is_running:
            return False
        if not self._is_paused:
            return True

        # 先恢复 AHK 输入状态；失败时调度器继续保持暂停，不会出现 Python 已生产、
        # AHK 却拒绝 START_MACRO/持键声明的半恢复状态。
        if self._is_macro_mode():
            input_ready = self._start_ahk_macro()
        else:
            input_ready = self._sync_skill_hold_keys()
        if not input_ready:
            return False

        if not self.unified_scheduler.resume():
            # 输入状态已经先恢复，调度器却拒绝 resume 时必须立即补偿；否则
            # MacroEngine 尚未完成 RUNNING 转换，AHK 宏却会独自继续发键。
            if self._is_macro_mode():
                self._stop_ahk_macro()
            else:
                self._release_skill_hold_keys()
            return False
        self._is_paused = False
        LOG_INFO("[统一调度器] 已恢复")
        return True

    @staticmethod
    def _coerce_mode(value: Any) -> Optional[int]:
        try:
            return config_int(value)
        except ValueError:
            return None

    @staticmethod
    def _milliseconds_to_seconds(value: Any) -> Optional[float]:
        try:
            milliseconds = config_float(value)
        except ValueError:
            return None
        if milliseconds <= 0:
            return None
        return milliseconds / 1000.0

    def _global_interval_seconds(self, key: str, default_ms: int) -> float:
        interval = self._milliseconds_to_seconds(
            self._global_config.get(key, default_ms)
        )
        if interval is None:
            LOG_ERROR(
                f"[统一调度器] {key} 无效，回退为 {default_ms}ms"
            )
            return default_ms / 1000.0
        return interval

    @classmethod
    def _timed_skill_intervals(
        cls, skills_config: Dict[str, Any]
    ) -> Dict[str, float]:
        intervals: Dict[str, float] = {}
        if not isinstance(skills_config, dict):
            return intervals
        for name, config in skills_config.items():
            if not isinstance(config, dict) or config.get("Enabled") is not True:
                continue
            if cls._coerce_mode(config.get("TriggerMode", 0)) != 0:
                continue
            interval = cls._milliseconds_to_seconds(config.get("Timer", 1000))
            if interval is not None:
                intervals[name] = interval
        return intervals

    def _update_timed_skill_tasks(
        self, old_skills_config: Dict[str, Any]
    ) -> None:
        """按最终技能配置更新任务；不改变未变任务的执行相位。"""
        old_intervals = self._timed_skill_intervals(old_skills_config)
        new_intervals = self._timed_skill_intervals(self._skills_config)
        old_names = set(old_intervals)
        new_names = set(new_intervals)

        for skill_name in old_names - new_names:
            if self.unified_scheduler.remove_task(f"timed_skill_{skill_name}"):
                LOG_INFO(f"[统一调度器] 移除定时技能任务: {skill_name}")

        for skill_name in new_names - old_names:
            interval = new_intervals[skill_name]
            if self.unified_scheduler.add_task(
                f"timed_skill_{skill_name}",
                interval,
                self.execute_timed_skill,
                args=(skill_name,),
            ):
                LOG_INFO(
                    f"[统一调度器] 添加定时技能任务: {skill_name}, "
                    f"间隔: {interval:.3f}s"
                )

        for skill_name in old_names & new_names:
            interval = new_intervals[skill_name]
            if abs(interval - old_intervals[skill_name]) < 1e-9:
                continue
            self.unified_scheduler.remove_task(f"timed_skill_{skill_name}")
            if self.unified_scheduler.add_task(
                f"timed_skill_{skill_name}",
                interval,
                self.execute_timed_skill,
                args=(skill_name,),
            ):
                LOG_INFO(
                    f"[统一调度器] 更新定时技能任务: {skill_name}, "
                    f"间隔: {interval:.3f}s"
                )

    def update_all_configs(self, skills_config: Dict[str, Any]) -> bool:
        """兼容单独更新技能配置；按当前全局配置应用同一事务。"""
        return self._apply_config_update(skills_config, self._global_config)

    def _abort_running_config_update(self, reason: str) -> bool:
        """热更新失败时先关 AHK 闸门，再等待 Python 生产者退出。"""
        was_running = self._is_running
        self._is_running = False
        self._is_paused = False

        # UnifiedScheduler.stop() 最长会 join 2 秒；在等待它之前必须先让 AHK
        # fail-closed，否则旧 AHK 宏/已经入队的动作会在 Python 等待时继续执行。
        try:
            self.input_handler.set_accepting_actions(False)
        except Exception as e:
            LOG_ERROR(f"[配置热更新] 关闭 AHK 输入闸门异常: {e}")

        if was_running:
            try:
                self._stop_autonomous_scheduling()
            except Exception as e:
                LOG_ERROR(f"[配置热更新] 停止 Python 调度器异常: {e}")
        # 调度器退出后再清理一次，收掉配置事务开始后才完成的
        # 在飞检测结果。否则 stop() 看到 _is_running=False 会提前返回，
        # 这些样本就可能残留到下一次 start。
        with self._config_lock:
            self._resource_condition_history.clear()
        LOG_ERROR(f"[配置热更新] {reason}，已停止输入生产并请求回退 STOPPED")
        try:
            event_bus.publish("skill_manager:input_sync_failed", reason=reason)
        except Exception as e:
            LOG_ERROR(f"[配置热更新] 发布停机请求失败: {e}")
        return False

    def update_global_config(self, global_config: Dict[str, Any]) -> bool:
        """兼容单独更新全局配置；按当前技能配置应用同一事务。"""
        return self._apply_config_update(self._skills_config, global_config)

    def _apply_config_update(
        self,
        skills_config: Dict[str, Any],
        global_config: Dict[str, Any],
    ) -> bool:
        """原子地收敛到技能+全局配置共同描述的最终输入状态。

        两份配置必须先一起暂存，随后只发送最终模式所需的持键/宏命令；最后才
        改调度任务。这样技能→宏且同时 A→B 的更新不会产生一次短暂 B down/up。
        """
        skills_config = skills_config if isinstance(skills_config, dict) else {}
        global_config = global_config if isinstance(global_config, dict) else {}
        old_skills_config = (
            self._skills_config if isinstance(self._skills_config, dict) else {}
        )
        old_global_config = (
            self._global_config if isinstance(self._global_config, dict) else {}
        )
        old_sequence_enabled = old_global_config.get("sequence_enabled") is True
        new_sequence_enabled = global_config.get("sequence_enabled") is True
        old_macro_steps = old_global_config.get("macro_steps")
        new_macro_steps = global_config.get("macro_steps")
        macro_steps_changed = old_macro_steps != new_macro_steps

        with self._config_lock:
            # 事务门与两份配置在同一临界区提交；调度回调不会看到“新配置、旧门”
            # 或“已开事务门、仍读取旧配置”的中间组合。
            self._config_update_in_progress = True
            self._config_generation += 1
            self._skills_config = skills_config
            self._global_config = global_config
            # 连续性样本只属于生成它的配置世代。即使技能同名，
            # 新配置也必须重新收集完整的连续帧，不能沿用旧的 True。
            self._resource_condition_history.clear()

        succeeded = False
        try:
            if self._is_running:
                if old_sequence_enabled and new_sequence_enabled:
                    if macro_steps_changed:
                        if not self._stop_ahk_macro():
                            return self._abort_running_config_update(
                                "停止旧 AHK 宏失败"
                            )
                        if not self._set_ahk_macro_steps():
                            return self._abort_running_config_update(
                                "下发热更新宏步骤失败"
                            )
                        if (
                            not self._is_paused
                            and not self._start_ahk_macro(sync_steps=False)
                        ):
                            return self._abort_running_config_update(
                                "启动热更新 AHK 宏失败"
                            )
                elif old_sequence_enabled and not new_sequence_enabled:
                    if not self._stop_ahk_macro():
                        return self._abort_running_config_update("停止旧 AHK 宏失败")
                    if not self._is_paused and not self._sync_skill_hold_keys():
                        return self._abort_running_config_update(
                            "切回技能模式时恢复持键失败"
                        )
                elif not old_sequence_enabled and new_sequence_enabled:
                    if not self._release_skill_hold_keys():
                        return self._abort_running_config_update(
                            "切入宏模式时释放技能持键失败"
                        )
                    if not self._set_ahk_macro_steps():
                        return self._abort_running_config_update(
                            "下发热更新宏步骤失败"
                        )
                    if (
                        not self._is_paused
                        and not self._start_ahk_macro(sync_steps=False)
                    ):
                        return self._abort_running_config_update(
                            "启动热更新 AHK 宏失败"
                        )
                elif not self._is_paused and not self._sync_skill_hold_keys():
                    return self._abort_running_config_update("技能持键热更新失败")

            if self._is_running and self.unified_scheduler.get_status()["running"]:
                if old_sequence_enabled != new_sequence_enabled:
                    LOG_INFO(
                        "[统一调度器] 序列模式状态变化: "
                        f"{old_sequence_enabled} -> {new_sequence_enabled}"
                    )
                    self._setup_all_scheduled_tasks()
                else:
                    if not new_sequence_enabled:
                        self._update_timed_skill_tasks(old_skills_config)
                        cooldown_interval = self._global_interval_seconds(
                            "cooldown_checker_interval", 100
                        )
                        if self.unified_scheduler.update_task_interval(
                            "cooldown_checker", cooldown_interval
                        ):
                            LOG_INFO(
                                "[统一调度器] 更新冷却检查间隔: "
                                f"{cooldown_interval:.3f}s"
                            )

                    if self.resource_manager:
                        raw_resource_config = global_config.get(
                            "resource_management", {}
                        )
                        resource_config = (
                            raw_resource_config
                            if isinstance(raw_resource_config, dict)
                            else {}
                        )
                        resource_interval = self._milliseconds_to_seconds(
                            resource_config.get("check_interval", 200)
                        )
                        if resource_interval is None:
                            LOG_ERROR(
                                "[统一调度器] 资源检查间隔无效，回退为 200ms"
                            )
                            resource_interval = 0.2
                        if self.unified_scheduler.update_task_interval(
                            "resource_checker", resource_interval
                        ):
                            LOG_INFO(
                                "[统一调度器] 更新资源管理间隔: "
                                f"{resource_interval:.3f}s"
                            )
            succeeded = True
            return True
        except Exception as e:
            LOG_ERROR(f"[配置热更新] 应用最终配置异常: {e}")
            if self._is_running:
                return self._abort_running_config_update("配置事务异常")
            return False
        finally:
            # 成功时重新放行生产者；失败路径已经把 _is_running 清成 False，
            # 门保持关闭直到下一次完整 start，避免延迟旧回调在 STOPPED 后发键。
            if succeeded:
                with self._config_lock:
                    # 事务期间已进入检测的旧回调可能在第一次清理后
                    # 才追加结果；重新开门前再清一次，确保新世代从空历史开始。
                    self._resource_condition_history.clear()
                    self._config_update_in_progress = False

    def execute_timed_skill(self, skill_name: str):
        """执行定时技能 - 统一帧管理版本"""
        if (
            not self._is_running
            or self._is_paused
            or self._config_update_in_progress
        ):
            return

        with self._config_lock:
            skill_config = self._skills_config.get(skill_name)
            config_generation = self._config_generation

        if isinstance(skill_config, dict) and skill_config.get("Enabled") is True:
            if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
                return
            # 🎯 方案2核心：为每个定时技能也获取帧数据，支持条件检测
            cached_frame = self._prepare_frame_detection_cache()
            if cached_frame is None:
                LOG_ERROR(f"[帧管理] 定时技能 {skill_name} 无法获取帧数据，跳过执行")
                return
                
            # ✅ 使用获取到的帧数据执行技能，确保条件检测准确性
            self._try_execute_skill(
                skill_name,
                skill_config,
                cached_frame,
                config_generation=config_generation,
            )

    def _get_macro_steps(self):
        """返回当前宏的原子步骤列表(down/up/press/delay)。

        macro_steps 为权威来源;键存在(哪怕是空列表)即尊重用户配置。仅当配置里完全
        没有 macro_steps 键时,才从旧 skill_sequence 现场迁移(兜底未归一化的配置;
        正常路径已在 normalize_config_keys 迁移过)。
        """
        g = self._global_config
        if "macro_steps" in g:
            steps = g.get("macro_steps")
            return steps if isinstance(steps, list) else []
        seq = g.get("skill_sequence")
        return migrate_skill_sequence_to_steps(seq) if seq else []

    def _is_macro_mode(self) -> bool:
        return self._global_config.get("sequence_enabled") is True

    def _set_ahk_macro_steps(self):
        steps = self._get_macro_steps()
        if not hasattr(self.input_handler, "set_macro_steps"):
            LOG_ERROR("[宏] 输入层不支持下发宏步骤")
            return False
        if not self.input_handler.set_macro_steps(steps):
            LOG_ERROR("[宏] 下发 AHK 宏步骤失败")
            return False
        LOG_INFO(f"[宏] 已下发 AHK 宏步骤: {len(steps)}")
        return True

    def _start_ahk_macro(self, sync_steps: bool = True):
        if not self._is_macro_mode():
            return True
        if sync_steps and not self._set_ahk_macro_steps():
            return False
        if not hasattr(self.input_handler, "start_macro"):
            LOG_ERROR("[宏] 输入层不支持启动宏")
            return False
        if not self.input_handler.start_macro():
            LOG_ERROR("[宏] AHK 宏循环启动失败")
            return False
        LOG_INFO("[宏] AHK 宏循环已启动")
        return True

    def _stop_ahk_macro(self) -> bool:
        if not hasattr(self.input_handler, "stop_macro"):
            return True
        try:
            if self.input_handler.stop_macro() is False:
                LOG_ERROR("[宏] AHK 宏循环停止失败")
                return False
        except Exception as e:
            LOG_ERROR(f"[宏] AHK 宏循环停止异常: {e}")
            return False
        LOG_INFO("[宏] AHK 宏循环已停止")
        return True

    def check_cooldowns(self):
        """统一技能冷却检查 - 使用单帧数据确保一致性"""
        if (
            not self._is_running
            or self._is_paused
            or self._config_update_in_progress
        ):
            return

        # 🎯 方案2核心：一次性获取帧数据，所有技能检测复用同一帧
        cached_frame = self._prepare_frame_detection_cache()
        if cached_frame is None:
            LOG_ERROR("[帧管理] 无法获取帧数据，跳过本轮技能检测")
            return  # 如果无法获取帧数据，跳过本次检测

        with self._config_lock:
            config_generation = self._config_generation
            # 按优先级排序：优先级高的技能先检查
            skills_to_check = sorted(
                (
                    item
                    for item in self._skills_config.items()
                    if isinstance(item[1], dict)
                ),
                key=lambda x: (x[1].get("Priority") is not True, x[0])
            )

        # 🔄 所有技能检测都使用同一帧数据，确保时序一致性
        priority_skills_executed = 0
        for skill_name, skill_config in skills_to_check:
            if not isinstance(skill_config, dict):
                continue
            if (
                skill_config.get("Enabled") is True
                and self._coerce_mode(skill_config.get("TriggerMode", 0)) == 1
            ):
                if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
                    continue
                is_priority = skill_config.get("Priority") is True
                if is_priority:
                    priority_skills_executed += 1
                LOG(f"[冷却检查] 检查技能 {skill_name} (优先级: {'高' if is_priority else '普通'})")
                
                # ✅ 关键：所有技能检测使用同一cached_frame，确保数据一致性
                self._try_execute_skill(
                    skill_name,
                    skill_config,
                    cached_frame,
                    config_generation=config_generation,
                )

        if priority_skills_executed > 0:
            LOG(f"[冷却检查] 本轮使用同一帧执行了 {priority_skills_executed} 个高优先级技能")

        # 注意：资源管理现在有独立的调度任务，不在这里调用

    def check_resources(self):
        """独立的资源管理检查任务（被统一调度器调用）- 使用统一帧管理"""
        if (
            not self._is_running
            or self._is_paused
            or self._config_update_in_progress
            or not self.resource_manager
        ):
            return

        # 🎯 方案2：为资源管理也获取独立的帧数据
        cached_frame = self._prepare_frame_detection_cache()
        if cached_frame is None:
            LOG_ERROR("[帧管理] 资源检查无法获取帧数据，跳过本轮")
            return

        # 截图可能阻塞；配置事务若在此期间开始，旧回调不得跨过事务边界执行。
        if self._config_update_in_progress or not self._is_running or self._is_paused:
            return

        # ✅ 将同一帧数据传递给资源管理器，确保资源检测的一致性
        self.resource_manager.check_and_execute_resources(cached_frame)

    def _prepare_frame_detection_cache(self) -> Optional[np.ndarray]:
        """
        性能优化：一次性获取当前帧数据（方案2核心实现）
        
        优势：
        1. 减少get_current_frame()调用次数
        2. 确保同一轮检测使用相同帧数据，保证时序一致性
        3. 避免重复的numpy数组对象创建
        
        返回帧数据，供后续所有检测使用
        """
        try:
            # 📊 统计get_current_frame调用次数
            self._frame_usage_stats["total_frame_gets"] += 1
            
            frame = self.border_frame_manager.get_current_frame()
            if frame is None:
                LOG_ERROR(f"[帧管理-统计] 获取帧数据失败: None")
            return frame
        except Exception as e:
            LOG_ERROR(f"[帧缓存] 准备检测缓存失败: {e}")
            return None

    def get_frame_performance_stats(self) -> Dict[str, Any]:
        """获取帧管理性能统计（方案2效果验证）"""
        stats = self._frame_usage_stats.copy()
        stats["optimization_summary"] = (
            f"总调用: {stats['total_frame_gets']}, "
            f"缓存命中: {stats['cached_frame_usage']}, "
            f"优化率: {stats['performance_ratio']:.1f}%"
        )
        return stats

    def _try_execute_skill(
        self,
        skill_name: str,
        skill_config: Dict[str, Any],
        cached_frame: Optional[np.ndarray] = None,
        *,
        config_generation: Optional[int] = None,
    ):
        """
        统一的技能执行方法（方案2优化版本）
        - 如果提供了cached_frame，使用缓存帧数据进行检测（高性能）
        - 如果没有提供cached_frame，使用实时检测（兼容性）
        """
        # 调度器的 run generation 只能隔离 stop/start，不会作废同一运行
        # 世代中已进入的配置回调。生产路径传入与 skill_config 同时
        # 快照的令牌；直接调用者则在入口捕获当前令牌。
        with self._config_lock:
            if config_generation is None:
                config_generation = self._config_generation
            if (
                self._config_update_in_progress
                or self._config_generation != config_generation
            ):
                return

        # 🔍 帧使用统计
        if cached_frame is not None:
            self._frame_usage_stats["cached_frame_usage"] += 1
            # 计算性能优化比例
            if self._frame_usage_stats["total_frame_gets"] > 0:
                self._frame_usage_stats["performance_ratio"] = (
                    self._frame_usage_stats["cached_frame_usage"] / 
                    self._frame_usage_stats["total_frame_gets"] * 100
                )
        else:
            LOG_ERROR(f"[帧管理-统计] 技能 {skill_name} 未使用缓存帧，性能未优化")
        
        try:
            trigger_mode = config_int(skill_config.get("TriggerMode", 0))
            execute_condition = config_int(skill_config.get("ExecuteCondition", 0))
        except ValueError:
            LOG_ERROR(f"[技能执行] {skill_name} 的触发/条件模式无效，本轮跳过")
            return
        if trigger_mode not in (0, 1, 2) or execute_condition not in (0, 1, 2):
            LOG_ERROR(f"[技能执行] {skill_name} 的触发/条件模式越界，本轮跳过")
            return
        if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
            return
        alt_key = skill_config.get("AltKey", "")

        is_ready = True
        # 1. 检查冷却（仅当是冷却模式时）
        if trigger_mode == 1:  # 冷却模式（技能图标检测）
            is_ready = self._check_cooldown_ready(
                skill_name, skill_config, cached_frame
            )
            
        # 2. 如果冷却就绪，再检查执行条件
        condition_result = True
        if is_ready:
            condition_result = self._check_execution_conditions(
                skill_name,
                skill_config,
                cached_frame,
                config_generation=config_generation,
            )

        # None 表示条件未知（缺帧、越界或坏配置）。无论 Key/AltKey 都不发，
        # 避免把检测故障解释成一个业务分支。
        if condition_result is None:
            return

        # 3. 根据条件类型和结果决定按键执行逻辑
        if not is_ready:
            # 冷却未就绪，不执行任何按键
            return

        key_to_use = None
        if execute_condition == 1:  # BUFF限制模式
            if not condition_result:
                key_to_use = skill_config.get("Key", "")
        elif execute_condition == 2:  # 资源条件模式
            if condition_result:
                key_to_use = skill_config.get("Key", "")
            else:
                key_to_use = alt_key
        else:  # 无条件模式 (execute_condition == 0)
            key_to_use = skill_config.get("Key", "")

        if key_to_use:
            # 检测/OCR 可能阻塞。热更新开始后，已经进入的旧任务也必须在真正
            # 下发输入前退出。检查与有界发送用配置锁线性化：事务要么等待这次
            # 已经决定的旧动作发完，要么先关门使该动作退出，不能从缝隙穿过。
            with self._config_lock:
                if (
                    self._config_update_in_progress
                    or self._config_generation != config_generation
                ):
                    return
                # 🎯 使用语义化接口根据优先级执行技能
                is_priority_skill = skill_config.get("Priority") is True
                if is_priority_skill:
                    self.input_handler.execute_skill_high(key_to_use)
                else:
                    self.input_handler.execute_skill_normal(key_to_use)

    def set_boss_mode_active(self, active: bool):
        """设置运行时 BOSS 模式状态。只影响 BossOnly 的定时/冷却技能。"""
        self._boss_mode_active = bool(active)
        LOG_INFO(f"[BOSS模式] 技能管理器状态: {'开' if active else '关'}")

    def _is_skill_suppressed_by_boss_mode(
        self, skill_name: str, skill_config: Dict[str, Any]
    ) -> bool:
        """BOSS 模式关闭时跳过 BossOnly 的自动技能。

        TriggerMode=2(按住)不参与 BOSS 模式,避免引入额外 hold/release 状态变化。
        """
        if skill_config.get("BossOnly") is not True:
            return False
        try:
            if config_int(skill_config.get("TriggerMode", 0)) == 2:
                return False
        except ValueError:
            return True
        if self._boss_mode_active:
            return False
        LOG(f"[BOSS模式] 跳过 {skill_name}: BossOnly 且当前为跑图态")
        return True

    def _check_cooldown_ready(
        self,
        skill_name: str,
        skill_config: Dict[str, Any],
        cached_frame: Optional[np.ndarray] = None,
    ) -> bool:
        try:
            if config_int(skill_config.get("TriggerMode", 0)) != 1:
                return True
        except ValueError:
            return False
        x, y, size = (
            skill_config.get("CooldownCoordX", 0),
            skill_config.get("CooldownCoordY", 0),
            skill_config.get("CooldownSize", 12),
        )

        try:
            x, y, size = config_int(x), config_int(y), config_int(size)
        except ValueError:
            LOG_ERROR(
                f"[冷却检测] {skill_name} - 坐标或尺寸不是整数: "
                f"({x!r}, {y!r}, {size!r})"
            )
            return False

        # 添加调试日志 (高频: 使用 LOG 受 DEBUG 控制)
        LOG(f"[冷却检测] 检查技能 {skill_name} - 坐标: ({x}, {y}), 大小: {size}")

        # 确保帧数据不为None
        if cached_frame is None:
            LOG_ERROR(f"[冷却检测] {skill_name} - 帧数据为空")
            return False

        # x/y 是虚拟桌面绝对坐标：原点与左/上副屏的负坐标都合法。
        # 尺寸必须为正；区域是否落在当前帧内由 compare_cooldown_image
        # 通过 BorderFrameManager 的统一坐标换算判定。
        if size <= 0 or size > 32767:
            LOG_ERROR(f"[冷却检测] {skill_name} - 无效尺寸: {size}")
            return False

        # 使用统一的接口，支持缓存帧数据
        match_percentage = self.border_frame_manager.compare_cooldown_image(
            cached_frame, x, y, skill_name, size, threshold=0.95
        )
        
        # 向调试管理器上报技能检测区域
        if self.debug_display_manager:
            self.debug_display_manager.update_detection_region(
                f"skill_{skill_name}",
                {
                    "type": "rectangle",
                    "x1": x,
                    "y1": y,
                    "x2": x + size,
                    "y2": y + size,
                    "color": "yellow",
                    "skill_name": skill_name,
                    "match_percentage": match_percentage if match_percentage is not None else 0
                }
            )
        
        # 失败安全：若返回 None，跳过本轮（视为未知状态，不判定就绪）
        if match_percentage is None:
            LOG_ERROR(f"[冷却检测] {skill_name} - 本轮检测失败(模板/区域/异常)，跳过判定")
            return False

        # 添加调试日志 (高频)
        LOG(f"[冷却检测] {skill_name} - 匹配度: {match_percentage:.2f}%")

        # 技能冷却检测：模板保存的是技能就绪状态
        # 匹配度高表示当前状态与就绪状态相似，技能就绪
        # 匹配度低表示当前状态与就绪状态不同，技能在冷却中
        is_ready = match_percentage >= 95.0  # 95%以上匹配度表示冷却完成

        # 将状态报告给DebugDisplayManager
        if self.debug_display_manager:
            self.debug_display_manager.update_skill_status(skill_name, match_percentage, is_ready)

        # 高频: 状态结论
        LOG(f"[冷却检测] {skill_name} - 冷却状态: {'就绪' if is_ready else '未就绪'}")
        return is_ready

    def _check_execution_conditions(
        self,
        skill_name: str,
        skill_config: Dict[str, Any],
        cached_frame: Optional[np.ndarray] = None,
        *,
        config_generation: Optional[int] = None,
    ) -> Optional[bool]:
        try:
            condition = config_int(skill_config.get("ExecuteCondition", 0))
        except ValueError:
            LOG_ERROR(f"[条件检测] {skill_name} - ExecuteCondition 无效")
            return None

        if condition == 0:
            return True

        try:
            x = config_int(skill_config["ConditionCoordX"])
            y = config_int(skill_config["ConditionCoordY"])
        except (KeyError, ValueError):
            LOG_ERROR(f"[条件检测] {skill_name} - 条件坐标缺失或无效")
            return None

        if cached_frame is None:
            LOG_ERROR(f"[条件检测] {skill_name} - 缓存帧缺失，本轮不发送任何按键")
            return None

        # (0, 0) 与负数均可能是合法虚拟桌面坐标。统一切片负责判断该点
        # 是否真的属于当前帧；未知状态必须跳过整个技能，而不是误走 Key/AltKey。
        region_getter = getattr(self.border_frame_manager, "get_region_from_frame", None)
        if region_getter is not None and region_getter(cached_frame, x, y, 1, 1) is None:
            LOG_ERROR(f"[条件检测] {skill_name} - 条件坐标不在当前帧内: ({x}, {y})")
            return None

        normalized_config = dict(skill_config)
        normalized_config["ConditionCoordX"] = x
        normalized_config["ConditionCoordY"] = y

        # 使用统一的接口进行条件检测
        try:
            if config_generation is None:
                result = self._evaluate_condition(
                    condition, skill_name, normalized_config, cached_frame
                )
            else:
                result = self._evaluate_condition(
                    condition,
                    skill_name,
                    normalized_config,
                    cached_frame,
                    config_generation=config_generation,
                )
        except Exception as e:
            LOG_ERROR(f"[条件检测] {skill_name} - 检查异常: {e}")
            return None
        
        return result

    def _evaluate_condition(
        self,
        condition: int,
        skill_name: str,
        skill_config: Dict[str, Any],
        cached_frame: Optional[np.ndarray] = None,
        *,
        config_generation: Optional[int] = None,
    ) -> Optional[bool]:
        x, y = skill_config.get("ConditionCoordX", 0), skill_config.get(
            "ConditionCoordY", 0
        )
        try:
            color = config_int(skill_config.get("ConditionColor", 0))
            tolerance = config_int(skill_config.get("ColorTolerance", 12))
        except ValueError:
            LOG_ERROR(f"[条件检测] {skill_name} - 颜色或容差无效")
            return None
        if not 0 <= color <= 0xFFFFFF or not 0 <= tolerance <= 255:
            LOG_ERROR(
                f"[条件检测] {skill_name} - 颜色或容差越界: "
                f"color={color}, tolerance={tolerance}"
            )
            return None

        if condition == 1:  # BUFF限制模式
            if cached_frame is None:
                LOG_ERROR(f"[条件检测] {skill_name} - 缓存帧缺失")
                return None
            if color == 0:
                result = self.border_frame_manager.is_resource_sufficient(
                    cached_frame, x, y, color_range_threshold=tolerance
                )
            elif color == 1:
                result = self.border_frame_manager.is_hp_sufficient(cached_frame, x, y)
            else:
                result = self.border_frame_manager.rgb_similarity(
                    cached_frame, x, y, color, tolerance
                )

            return result

        elif condition == 2:  # 资源条件模式
            try:
                if cached_frame is None:
                    LOG(f"[资源条件] {skill_name} - 缓存帧缺失，本轮跳过")
                    return None
                if color == 0:
                    is_sufficient = self.border_frame_manager.is_resource_sufficient(
                        cached_frame, x, y, color_range_threshold=tolerance
                    )
                elif color == 1:
                    is_sufficient = self.border_frame_manager.is_hp_sufficient(
                        cached_frame, x, y
                    )
                else:
                    is_sufficient = self.border_frame_manager.rgb_similarity(
                        cached_frame, x, y, color, tolerance
                    )

                # 对于资源条件，使用连续性检查确保稳定性
                final_result = self._check_resource_continuity(
                    skill_name,
                    is_sufficient,
                    config_generation=config_generation,
                )
                return final_result
            except Exception as e:
                LOG_ERROR(f"[资源条件] {skill_name} - 检查异常: {e}")
                return None

        LOG_ERROR(f"[条件检测] {skill_name} - 未知条件类型: {condition}")
        return None

    def _check_resource_continuity(
        self,
        skill_name: str,
        current_result: bool,
        *,
        config_generation: Optional[int] = None,
    ) -> Optional[bool]:
        """
        资源条件连续性检查
        - 如果当前结果为True（资源充足），需要连续多次True才执行主按键
        - 如果当前结果为False（资源不足），立即执行备用按键
        """
        # 检查令牌与追加历史必须在同一个短临界区：配置事务
        # 要么在此后增代并清空样本，要么旧回调先看到已增代而退出。
        # 检测/OCR 本身仍全部在锁外执行。
        with self._config_lock:
            if config_generation is not None and (
                self._config_update_in_progress
                or self._config_generation != config_generation
            ):
                return None

            history = self._resource_condition_history.setdefault(skill_name, [])
            history.append(current_result)
            if len(history) > self._required_consecutive_checks:
                history.pop(0)

            # 如果当前结果为False（资源不足），立即返回False执行AltKey
            if not current_result:
                return False

            # 如果当前结果为True（资源充足），需要连续多次True才返回True
            if all(history) and len(history) == self._required_consecutive_checks:
                # 不再清空历史记录，实现滑动窗口效果
                return True

            return False


    # ===== 按住/释放：一次性生命周期管理（不在循环中） =====
    @staticmethod
    def _is_mouse_hold_key(key: str) -> bool:
        return key.lower() in {"lbutton", "rbutton", "mbutton", "xbutton1", "xbutton2"}

    def _get_configured_hold_keys(self) -> List[str]:
        keys: List[str] = []
        seen = set()
        try:
            for name, cfg in self._skills_config.items():
                if not isinstance(cfg, dict):
                    continue
                if (
                    cfg.get("Enabled") is True
                    and self._coerce_mode(cfg.get("TriggerMode", 0)) == 2
                ):
                    k = str(cfg.get("Key") or "").strip()
                    if k and k not in seen:
                        keys.append(k)
                        seen.add(k)
            # 稳定顺序：鼠标键先按下；同类内部保持配置文件/Skill1..Skill8 顺序。
            keys.sort(key=lambda key: 0 if self._is_mouse_hold_key(key) else 1)
            self._warn_hold_key_conflicts(keys)
        except Exception as e:
            LOG_ERROR(f"[按住] 提取配置失败: {e}")
        return keys

    def _warn_hold_key_conflicts(self, hold_keys: List[str]) -> None:
        """检出"管理键映射目标 == TriggerMode=2 持久持键"这种自相矛盾的配置。

        管理键会为 target 生成 press:/hold:+release: 队列动作,净效果是把该键抬起;
        AHK 端已能靠 ForgetSkillHeldKey + Reconcile 恢复,但用户的真实意图几乎肯定
        不是"按一下管理键就把持续技能打断再重按",所以这里明确报出来。
        """
        if not hold_keys:
            return
        lowered = {k.lower() for k in hold_keys}
        raw_priority = self._global_config.get("priority_keys", {})
        priority = raw_priority if isinstance(raw_priority, dict) else {}
        raw_managed = priority.get("managed_keys", {})
        managed = raw_managed if isinstance(raw_managed, dict) else {}
        for key, cfg in managed.items():
            target = key
            if isinstance(cfg, dict):
                target = (cfg.get("target") or key) or key
            if str(target).lower() in lowered:
                LOG_ERROR(
                    f"[按住] 配置冲突: 管理键 {key} 的映射目标 {target} 同时是 TriggerMode=2 持久持键, "
                    f"按下管理键会打断该持续技能(AHK 会在管理键序列结束后自动补按)"
                )

    def _sync_skill_hold_keys(self, keys: Optional[List[str]] = None) -> bool:
        """把期望持键的**完整集合**声明给 AHK(单一权威在 AHK 端)。

        Python 不再镜像"实际按住了什么":AHK 收到完整集合后做幂等差量同步
        (LIFO 释放多余键、按序补按缺失键,抑制期只推迟新增 down)。
        因此这里既是 apply 也是 delta,也是 release —— 传空集合即全部释放。
        """
        target = list(keys) if keys is not None else self._get_configured_hold_keys()
        try:
            ok = self.input_handler.set_skill_hold_keys(target)
            LOG_INFO(f"[按住] 已声明期望持键: {target or '(空:全部释放)'}")
            return bool(ok)
        except Exception as e:
            LOG_ERROR(f"[按住] 下发期望持键失败 {target}: {e}")
            return False

    def _release_skill_hold_keys(self) -> bool:
        """释放全部技能持键(声明空集合)。属安全清理命令,干跑下也会真实下发。"""
        return self._sync_skill_hold_keys([])

    # ===== 现有逻辑 =====
    def prepare_border_only(self):
        """仅准备边框区域，不启动循环捕获"""
        with self._config_lock:
            raw_resource_config = self._global_config.get("resource_management", {})
            resource_config = (
                raw_resource_config if isinstance(raw_resource_config, dict) else {}
            )
            self.border_frame_manager.prepare_border(self._skills_config, resource_config)

    def start_capture_loop(self, interval_ms: int):
        """启动边框图循环捕获"""
        self.border_frame_manager.start_capture_loop(interval_ms)

    def start(self):
        if self._is_running:
            return True

        # 设置技能坐标并计算边框
        with self._config_lock:
            # 一次完整 start 是新的运行世代。失败热更或异常停机后
            # 即使有晚到检测结果，也不得带入这次运行。
            self._resource_condition_history.clear()
            raw_resource_config = self._global_config.get("resource_management", {})
            resource_config = (
                raw_resource_config if isinstance(raw_resource_config, dict) else {}
            )
            self.border_frame_manager.prepare_border(self._skills_config, resource_config)

        # AHK 输入状态先成功提交，再启动 Python 生产者。失败时保持未运行，交给
        # MacroEngine 的 RUNNING 入口回滚关闭闸门，绝不发布伪 RUNNING。
        if self._is_macro_mode():
            input_ready = self._start_ahk_macro()
        else:
            input_ready = self._sync_skill_hold_keys()
        if not input_ready:
            return False

        self._is_running = True
        self._is_paused = False
        self._boss_mode_active = False
        try:
            self._start_autonomous_scheduling()
            if not self.unified_scheduler.get_status()["running"]:
                raise RuntimeError("统一调度器启动失败")
        except Exception:
            self._is_running = False
            self._is_paused = False
            self._stop_autonomous_scheduling()
            if self._is_macro_mode():
                self._stop_ahk_macro()
            else:
                self._release_skill_hold_keys()
            raise
        with self._config_lock:
            # 可能承接一次失败热更新留下的 fail-closed 事务门；只有完整 start
            # 已成功提交 AHK 输入并启动调度器后才允许生产者继续。
            self._config_update_in_progress = False
        return True

    def stop(self, *, cleanup_input: bool = True):
        """停止 Python 生产者，并按需清理 AHK 输入状态。

        ``cleanup_input=False`` 只供上层已经成功执行单条原子
        ``reset_runtime`` 后使用，避免再串行发送空持键/stop_macro。默认保持
        独立调用时的完整安全清理语义。
        """
        if not self._is_running:
            return
        # 🔧 停止顺序:停生产者并**等待调度线程退出** → 再释放持键/停宏。
        # 反过来(先释放后停调度)会让在飞的回调在释放之后又入队新动作。
        self._is_running = False
        self._is_paused = False

        # 1) 停生产者:_stop_autonomous_scheduling 内部 join 调度线程(timeout 2s)
        self._stop_autonomous_scheduling()

        # 2) 生产者已停,再释放技能持键(声明空集合)与宏持键。若 MacroEngine
        # 已用 reset_runtime 原子完成这些动作，则禁止重复跨进程清理。
        if cleanup_input:
            self._release_skill_hold_keys()
            self._stop_ahk_macro()

        self.border_frame_manager.stop()
        self.clear_cache()

    def clear_cache(self):
        # 清理资源条件历史记录（不再需要其他缓存）
        self._resource_condition_history.clear()

    def emergency_stop(self):
        """紧急停止 - 强制终止所有线程"""
        LOG_ERROR("[紧急停止] 强制终止所有技能管理器线程")

        # 强制设置停止标志
        self._is_running = False
        self._is_paused = True

        # 释放技能持键与宏持键,防止紧急停止时键悬空
        try:
            self._release_skill_hold_keys()
        except Exception as e:
            LOG_ERROR(f"[紧急停止] 释放技能持键失败: {e}")
        try:
            self._stop_ahk_macro()
        except Exception as e:
            LOG_ERROR(f"[紧急停止] 中止宏失败: {e}")

        # 强制停止统一调度器
        try:
            self.unified_scheduler.stop()
            LOG_INFO("[紧急停止] 统一调度器已强制停止")
        except Exception as e:
            LOG_ERROR(f"[紧急停止] 统一调度器停止失败: {e}")

        # 停止边框管理器
        if self.border_frame_manager:
            try:
                self.border_frame_manager.stop()
            except Exception as e:
                LOG_ERROR(f"[紧急停止] 边框管理器停止失败: {e}")

    def cleanup(self):
        """清理资源"""
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        try:
            self.stop()
        except Exception as e:
            LOG_ERROR(f"[清理] 正常停止失败，执行紧急停止: {e}")
            self.emergency_stop()

        # 清理统一调度器
        try:
            self.unified_scheduler.clear_all_tasks()
            LOG_INFO("[清理] 统一调度器任务已清理")
        except Exception as e:
            LOG_ERROR(f"[清理] 统一调度器清理失败: {e}")

        # 取消事件订阅
        try:
            event_bus.unsubscribe("engine:config_updated", self._on_config_updated)
        except Exception as e:
            LOG_ERROR(f"[清理] 事件订阅取消失败: {e}")
