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

        # 线程安全配置
        self._config_lock = threading.Lock()
        self._resource_condition_history = {}
        self._required_consecutive_checks = 2
        self._boss_mode_active = False

        # 按住键状态跟踪（一次性按下/释放，不在循环中）
        self._held_hold_keys = set()
        self._held_hold_order: List[str] = []

        # 自主调度相关属性
        self._global_config = {}

        # 统一调度器
        self.unified_scheduler = UnifiedScheduler()

        # 订阅MacroEngine事件
        self._setup_event_subscriptions()
        # 注意：初始化阶段不执行按住/释放（_release_hold_keys），按住/释放只在 start/pause/resume/stop 或配置热更新时一次性执行

    def _setup_event_subscriptions(self):
        """设置事件订阅"""
        # 移除对engine:state_changed的订阅，避免与MacroEngine的直接调用产生竞态条件
        event_bus.subscribe("engine:config_updated", self._on_config_updated)
        
        # 🚀 订阅优先级按键的调度器控制事件
        event_bus.subscribe("scheduler_pause_requested", self._on_scheduler_pause_requested)
        event_bus.subscribe("scheduler_resume_requested", self._on_scheduler_resume_requested)

    def _on_config_updated(self, skills_config, global_config):
        """响应配置更新，并动态更新调度器任务"""
        # 更新内部配置
        self.update_all_configs(skills_config)
        self.update_global_config(global_config)
    
    def _on_scheduler_pause_requested(self, event_data):
        """响应优先级按键按下 - 暂停调度器以节省CPU资源"""
        try:
            reason = event_data.get('reason', 'unknown')
            active_keys = event_data.get('active_keys', [])
            
            # 暂停统一调度器，但不改变 _is_paused 状态（这是临时性能优化暂停）
            if self.unified_scheduler.get_status()["running"]:
                self.unified_scheduler.pause()
                LOG(f"[性能优化] 调度器已暂停 - {reason}, 激活按键: {active_keys}")

        except Exception as e:
            LOG_ERROR(f"[性能优化] 暂停调度器异常: {e}")
    
    def _on_scheduler_resume_requested(self, event_data):
        """响应优先级按键释放 - 恢复调度器"""
        try:
            reason = event_data.get('reason', 'unknown')
            
            # 只有在 SkillManager 正在运行且未被用户手动暂停时才恢复
            if self._is_running and not self._is_paused:
                self.unified_scheduler.resume()
                LOG(f"[性能优化] 调度器已恢复 - {reason}")
            
        except Exception as e:
            LOG_ERROR(f"[性能优化] 恢复调度器异常: {e}")

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

        is_sequence_mode = self._global_config.get("sequence_enabled", False)

        if is_sequence_mode:
            # 宏模式由 AHK 端解释器执行,Python 调度器只保留资源检测任务。
            LOG_INFO("[统一调度器] 进入宏模式，宏步骤由 AHK 端循环执行")
        else:
            # 技能模式：添加定时和冷却任务
            # 1. 添加定时技能任务
            self._setup_timed_skills_tasks()

            # 2. 添加冷却检查任务
            cooldown_interval = (
                self._global_config.get("cooldown_checker_interval", 100) / 1000.0
            )
            self.unified_scheduler.add_task(
                "cooldown_checker", cooldown_interval, self.check_cooldowns
            )
            LOG_INFO(
                f"[统一调度器] 进入技能模式，添加冷却检查任务，间隔: {cooldown_interval:.3f}s"
            )

        # 3. 添加资源管理任务（独立调度，序列/技能模式都需要 HP/MP 自动药剂）
        if self.resource_manager:
            resource_config = self._global_config.get("resource_management", {})
            resource_interval = resource_config.get("check_interval", 200) / 1000.0
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
                if config.get("Enabled") and config.get("TriggerMode") == 0:
                    interval = config.get("Timer", 1000) / 1000.0
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
        # 一次性释放所有按住键 + 停止 AHK 端宏(释放宏持键)
        self._release_hold_keys()
        self._stop_ahk_macro()

        # 暂停统一调度器
        if self.unified_scheduler.get_status()["running"]:
            self.unified_scheduler.pause()
            LOG_INFO("[统一调度器] 已暂停")

    def resume(self):
        """恢复所有技能活动"""
        if self._is_running:
            self._is_paused = False

            # 恢复统一调度器
            self.unified_scheduler.resume()
            LOG_INFO("[统一调度器] 已恢复")

            if self._is_macro_mode():
                self._start_ahk_macro()
            else:
                # 一次性重新按住
                self._apply_hold_keys()

    def update_all_configs(self, skills_config: Dict[str, Any]):
        """更新所有技能配置并同步调度器"""
        with self._config_lock:
            # 记录旧的技能配置用于对比
            old_timed_skills = {
                name
                for name, config in self._skills_config.items()
                if config.get("Enabled") and config.get("TriggerMode") == 0
            }
            # 🔧 BUG修复: 覆盖前记录旧间隔,用于只在间隔真正变化时才重建任务,
            # 避免每次配置热更新(如运行中切换 DEBUG)都重置全部定时技能的执行相位。
            old_intervals = {
                name: config.get("Timer", 1000) / 1000.0
                for name, config in self._skills_config.items()
                if config.get("Enabled") and config.get("TriggerMode") == 0
            }

            # 在覆盖前提取旧的“按住”集合
            old_hold_keys = self._get_configured_hold_keys()

            # 更新配置
            self._skills_config = skills_config

            # 覆盖后提取新的“按住”集合
            new_hold_keys = self._get_configured_hold_keys()

            # 记录新的技能配置
            new_timed_skills = {
                name
                for name, config in self._skills_config.items()
                if config.get("Enabled") and config.get("TriggerMode") == 0
            }

            # 如果调度器正在运行，需要更新任务
            if self._is_running and self.unified_scheduler.get_status()["running"]:
                if self._is_macro_mode():
                    return
                # 非暂停状态下，同步按住集合的增量（一次性按/放）
                if not self._is_paused:
                    self._apply_delta_hold_keys(old_hold_keys, new_hold_keys)
                # 移除不再需要的定时技能任务
                removed_skills = old_timed_skills - new_timed_skills
                for skill_name in removed_skills:
                    task_id = f"timed_skill_{skill_name}"
                    if self.unified_scheduler.remove_task(task_id):
                        LOG_INFO(f"[统一调度器] 移除定时技能任务: {skill_name}")

                # 添加新的定时技能任务
                added_skills = new_timed_skills - old_timed_skills
                for skill_name in added_skills:
                    config = self._skills_config[skill_name]
                    interval = config.get("Timer", 1000) / 1000.0
                    task_id = f"timed_skill_{skill_name}"

                    if self.unified_scheduler.add_task(
                        task_id, interval, self.execute_timed_skill, args=(skill_name,)
                    ):
                        LOG_INFO(
                            f"[统一调度器] 添加定时技能任务: {skill_name}, 间隔: {interval:.3f}s"
                        )

                # 更新现有技能的间隔:仅在间隔实际变化时才重建任务
                for skill_name in old_timed_skills & new_timed_skills:
                    config = self._skills_config[skill_name]
                    interval = config.get("Timer", 1000) / 1000.0
                    task_id = f"timed_skill_{skill_name}"

                    # 🔧 BUG修复: 间隔未变则保持原相位不动(不 remove+add)。否则 add_task 会把
                    # next_run 重置为 now+interval,每次无关的配置热更新都会推迟技能、造成漂移/漏放。
                    if abs(interval - old_intervals.get(skill_name, -1.0)) < 1e-9:
                        continue

                    # 间隔确实变了:移除后重新添加
                    self.unified_scheduler.remove_task(task_id)
                    if self.unified_scheduler.add_task(
                        task_id, interval, self.execute_timed_skill, args=(skill_name,)
                    ):
                        LOG_INFO(
                            f"[统一调度器] 更新定时技能任务: {skill_name}, 间隔: {interval:.3f}s"
                        )

    def update_global_config(self, global_config: Dict[str, Any]):
        """更新全局配置并同步调度器"""
        old_sequence_enabled = self._global_config.get("sequence_enabled", False)
        new_sequence_enabled = global_config.get("sequence_enabled", False)
        old_macro_steps = self._global_config.get("macro_steps")
        new_macro_steps = global_config.get("macro_steps")
        macro_changed = old_sequence_enabled != new_sequence_enabled or old_macro_steps != new_macro_steps

        self._global_config = global_config

        if self._is_running and macro_changed:
            if old_sequence_enabled:
                self._stop_ahk_macro()
            if new_sequence_enabled:
                # 🔧 进入宏模式:先释放技能模式遗留的 TriggerMode=2 持久按住键,否则卡键。
                # config_updated 先于本方法调用 update_all_configs(),而那时 self._global_config 仍是
                # 旧值(sequence_enabled=False),会沿技能路径保留这些持键。此处兜底释放。
                if not old_sequence_enabled:
                    self._release_hold_keys()
                self._set_ahk_macro_steps()
                if not self._is_paused:
                    self._start_ahk_macro(sync_steps=False)

        # 如果调度器正在运行，需要更新任务
        if self._is_running and self.unified_scheduler.get_status()["running"]:
            # 如果序列模式状态发生变化，重新设置所有任务
            if old_sequence_enabled != new_sequence_enabled:
                LOG_INFO(
                    f"[统一调度器] 序列模式状态变化: {old_sequence_enabled} -> {new_sequence_enabled}"
                )
                self._setup_all_scheduled_tasks()
                # 🔧 切回技能模式:宏模式期间从未持有 TriggerMode=2 按住键,需补按下。
                # (update_all_configs 在旧宏模式下 _is_macro_mode() 读旧值=True 而提前 return,
                #  跳过了按住键应用;_apply_hold_keys 幂等,仅按下尚未持有的键)
                if not new_sequence_enabled and not self._is_paused:
                    self._apply_hold_keys()
            else:
                if not new_sequence_enabled:
                    # 技能模式：更新冷却检查间隔
                    cooldown_interval = (
                        global_config.get("cooldown_checker_interval", 100) / 1000.0
                    )
                    if self.unified_scheduler.update_task_interval(
                        "cooldown_checker", cooldown_interval
                    ):
                        LOG_INFO(
                            f"[统一调度器] 更新冷却检查间隔: {cooldown_interval:.3f}s"
                        )

                # 更新资源管理间隔（序列/技能模式都需要）
                if self.resource_manager:
                    resource_config = global_config.get("resource_management", {})
                    resource_interval = (
                        resource_config.get("check_interval", 200) / 1000.0
                    )
                    if self.unified_scheduler.update_task_interval(
                        "resource_checker", resource_interval
                    ):
                        LOG_INFO(
                            f"[统一调度器] 更新资源管理间隔: {resource_interval:.3f}s"
                        )

    def execute_timed_skill(self, skill_name: str):
        """执行定时技能 - 统一帧管理版本"""
        if not self._is_running or self._is_paused:
            return

        with self._config_lock:
            skill_config = self._skills_config.get(skill_name)

        if skill_config and skill_config.get("Enabled"):
            if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
                return
            # 🎯 方案2核心：为每个定时技能也获取帧数据，支持条件检测
            cached_frame = self._prepare_frame_detection_cache()
            if cached_frame is None:
                LOG_ERROR(f"[帧管理] 定时技能 {skill_name} 无法获取帧数据，跳过执行")
                return
                
            # ✅ 使用获取到的帧数据执行技能，确保条件检测准确性
            self._try_execute_skill(skill_name, skill_config, cached_frame)

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
        return bool(self._global_config.get("sequence_enabled", False))

    def _set_ahk_macro_steps(self):
        steps = self._get_macro_steps()
        if hasattr(self.input_handler, "set_macro_steps"):
            self.input_handler.set_macro_steps(steps)
        LOG_INFO(f"[宏] 已下发 AHK 宏步骤: {len(steps)}")

    def _start_ahk_macro(self, sync_steps: bool = True):
        if not self._is_macro_mode():
            return
        if sync_steps:
            self._set_ahk_macro_steps()
        if hasattr(self.input_handler, "start_macro"):
            self.input_handler.start_macro()
        LOG_INFO("[宏] AHK 宏循环已启动")

    def _stop_ahk_macro(self):
        if hasattr(self.input_handler, "stop_macro"):
            self.input_handler.stop_macro()
        LOG_INFO("[宏] AHK 宏循环已停止")

    def check_cooldowns(self):
        """统一技能冷却检查 - 使用单帧数据确保一致性"""
        if not self._is_running or self._is_paused:
            return

        # 🎯 方案2核心：一次性获取帧数据，所有技能检测复用同一帧
        cached_frame = self._prepare_frame_detection_cache()
        if cached_frame is None:
            LOG_ERROR("[帧管理] 无法获取帧数据，跳过本轮技能检测")
            return  # 如果无法获取帧数据，跳过本次检测

        with self._config_lock:
            # 按优先级排序：优先级高的技能先检查
            skills_to_check = sorted(
                self._skills_config.items(),
                key=lambda x: (not x[1].get("Priority", False), x[0])  # Priority=True的排在前面
            )

        # 🔄 所有技能检测都使用同一帧数据，确保时序一致性
        priority_skills_executed = 0
        for skill_name, skill_config in skills_to_check:
            if skill_config.get("Enabled") and skill_config.get("TriggerMode") == 1:
                if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
                    continue
                is_priority = skill_config.get("Priority", False)
                if is_priority:
                    priority_skills_executed += 1
                LOG(f"[冷却检查] 检查技能 {skill_name} (优先级: {'高' if is_priority else '普通'})")
                
                # ✅ 关键：所有技能检测使用同一cached_frame，确保数据一致性
                self._try_execute_skill(skill_name, skill_config, cached_frame)

        if priority_skills_executed > 0:
            LOG(f"[冷却检查] 本轮使用同一帧执行了 {priority_skills_executed} 个高优先级技能")

        # 注意：资源管理现在有独立的调度任务，不在这里调用

    def check_resources(self):
        """独立的资源管理检查任务（被统一调度器调用）- 使用统一帧管理"""
        if not self._is_running or self._is_paused or not self.resource_manager:
            return

        # 🎯 方案2：为资源管理也获取独立的帧数据
        cached_frame = self._prepare_frame_detection_cache()
        if cached_frame is None:
            LOG_ERROR("[帧管理] 资源检查无法获取帧数据，跳过本轮")
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
    ):
        """
        统一的技能执行方法（方案2优化版本）
        - 如果提供了cached_frame，使用缓存帧数据进行检测（高性能）
        - 如果没有提供cached_frame，使用实时检测（兼容性）
        """
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
        
        trigger_mode = skill_config.get("TriggerMode")
        if self._is_skill_suppressed_by_boss_mode(skill_name, skill_config):
            return
        alt_key = skill_config.get("AltKey", "")
        execute_condition = skill_config.get("ExecuteCondition", 0)

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
                skill_name, skill_config, cached_frame
            )

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
            # 🎯 使用语义化接口根据优先级执行技能
            is_priority_skill = skill_config.get("Priority", False)
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
        if not skill_config.get("BossOnly", False):
            return False
        if skill_config.get("TriggerMode") == 2:
            return False
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
        if skill_config.get("TriggerMode") != 1:
            return True
        x, y, size = (
            skill_config.get("CooldownCoordX", 0),
            skill_config.get("CooldownCoordY", 0),
            skill_config.get("CooldownSize", 12),
        )

        # 添加调试日志 (高频: 使用 LOG 受 DEBUG 控制)
        LOG(f"[冷却检测] 检查技能 {skill_name} - 坐标: ({x}, {y}), 大小: {size}")

        # 确保帧数据不为None
        if cached_frame is None:
            LOG_ERROR(f"[冷却检测] {skill_name} - 帧数据为空")
            return True

        # 检查坐标是否有效
        if x <= 0 or y <= 0:
            LOG_ERROR(f"[冷却检测] {skill_name} - 无效坐标: ({x}, {y})")
            return True

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
    ) -> bool:
        condition = skill_config.get("ExecuteCondition", 0)

        if condition == 0:
            return True

        x, y = skill_config.get("ConditionCoordX", 0), skill_config.get(
            "ConditionCoordY", 0
        )

        if x == 0 or y == 0:
            return True

        # 使用统一的接口进行条件检测
        result = self._evaluate_condition(
            condition, skill_name, skill_config, cached_frame
        )
        
        return result

    def _evaluate_condition(
        self,
        condition: int,
        skill_name: str,
        skill_config: Dict[str, Any],
        cached_frame: Optional[np.ndarray] = None,
    ) -> bool:
        x, y = skill_config.get("ConditionCoordX", 0), skill_config.get(
            "ConditionCoordY", 0
        )
        color, tolerance = skill_config.get("ConditionColor", 0), skill_config.get(
            "ColorTolerance", 12
        )

        if condition == 1:  # BUFF限制模式
            if cached_frame is None:
                LOG_ERROR(f"[条件检测] {skill_name} - 缓存帧缺失，跳过条件判断(返回True防止误触发替代逻辑)")
                return True
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
                    LOG(f"[资源条件] {skill_name} - 缓存帧缺失，返回False以走AltKey")
                    return False
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
                    skill_name, is_sufficient
                )
                return final_result
            except Exception as e:
                LOG_ERROR(f"[资源条件] {skill_name} - 检查异常: {e}")
                # 异常时默认返回False，执行AltKey
                return False

        # 未知条件类型，默认返回True
        return True

    def _check_resource_continuity(self, skill_name: str, current_result: bool) -> bool:
        """
        资源条件连续性检查
        - 如果当前结果为True（资源充足），需要连续多次True才执行主按键
        - 如果当前结果为False（资源不足），立即执行备用按键
        """
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
                if cfg.get("Enabled") and cfg.get("TriggerMode") == 2:
                    k = (cfg.get("Key") or "").strip()
                    if k and k not in seen:
                        keys.append(k)
                        seen.add(k)
            # 稳定顺序：鼠标键先按下；同类内部保持配置文件/Skill1..Skill8 顺序。
            keys.sort(key=lambda key: 0 if self._is_mouse_hold_key(key) else 1)
        except Exception as e:
            LOG_ERROR(f"[按住] 提取配置失败: {e}")
        return keys

    def _apply_hold_keys(self):
        """按下当前应按住但尚未按住的键，并记录在 _held_hold_keys"""
        target = self._get_configured_hold_keys()
        to_press = [key for key in target if key not in self._held_hold_keys]
        if not to_press:
            return
        LOG_INFO(f"[按住] 按下: {to_press}")
        for k in to_press:
            try:
                if self.input_handler.hold_key(k):
                    self._held_hold_keys.add(k)
                    if k not in self._held_hold_order:
                        self._held_hold_order.append(k)
            except Exception as e:
                LOG_ERROR(f"[按住] hold_key 失败 {k}: {e}")

    def _release_hold_keys(self):
        """释放当前已按住的所有键，并清空 _held_hold_keys"""
        if not self._held_hold_keys:
            return
        keys = [key for key in reversed(self._held_hold_order) if key in self._held_hold_keys]
        remaining = sorted(self._held_hold_keys - set(keys))
        keys.extend(remaining)
        LOG_INFO(f"[按住] 释放: {keys}")
        for k in keys:
            try:
                self.input_handler.release_key(k)
            except Exception as e:
                LOG_ERROR(f"[按住] release_key 失败 {k}: {e}")
        self._held_hold_keys.clear()
        self._held_hold_order.clear()

    def _apply_delta_hold_keys(self, old_set, new_set):
        """运行中配置热更新：按下新增，释放移除，保持一次性语义"""
        old_set = set(old_set)
        new_order = list(new_set)
        new_set = set(new_order)
        to_press = [key for key in new_order if key not in old_set]
        to_release = [key for key in reversed(self._held_hold_order) if key not in new_set]
        if to_press:
            LOG_INFO(f"[按住] 配置变更-按下: {to_press}")
            for k in to_press:
                try:
                    if self.input_handler.hold_key(k):
                        self._held_hold_keys.add(k)
                        if k not in self._held_hold_order:
                            self._held_hold_order.append(k)
                except Exception as e:
                    LOG_ERROR(f"[按住] hold_key 失败 {k}: {e}")
        if to_release:
            LOG_INFO(f"[按住] 配置变更-释放: {to_release}")
            for k in to_release:
                try:
                    self.input_handler.release_key(k)
                    self._held_hold_keys.discard(k)
                    if k in self._held_hold_order:
                        self._held_hold_order.remove(k)
                except Exception as e:
                    LOG_ERROR(f"[按住] release_key 失败 {k}: {e}")

    # ===== 现有逻辑 =====
    def prepare_border_only(self):
        """仅准备边框区域，不启动循环捕获"""
        with self._config_lock:
            resource_config = self._global_config.get("resource_management", {})
            self.border_frame_manager.prepare_border(self._skills_config, resource_config)

    def start_capture_loop(self, interval_ms: int):
        """启动边框图循环捕获"""
        self.border_frame_manager.start_capture_loop(interval_ms)

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        self._is_paused = False
        self._boss_mode_active = False

        # 设置技能坐标并计算边框
        with self._config_lock:
            resource_config = self._global_config.get("resource_management", {})
            self.border_frame_manager.prepare_border(self._skills_config, resource_config)

        # 直接启动自主调度
        self._start_autonomous_scheduling()

        if self._is_macro_mode():
            self._start_ahk_macro()
        else:
            # 一次性按住配置中的按住键
            self._apply_hold_keys()

    def stop(self):
        if not self._is_running:
            return
        # 先停推进,再释放(含宏持键),避免调度线程竞态
        self._is_running = False
        self._is_paused = False
        self._release_hold_keys()
        self._stop_ahk_macro()

        # 停止自主调度
        self._stop_autonomous_scheduling()

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

        # 释放宏持键,防止紧急停止时键悬空
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
