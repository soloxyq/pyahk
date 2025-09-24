"""重构后的MacroEngine - 专注于状态管理和事件协调"""

import threading
import time
from typing import Dict, Any, Optional, Tuple

from .config_manager import ConfigManager
from .input_handler import InputHandler
from .skill_manager import SkillManager
from .event_bus import event_bus
from .states import MacroState
from ..utils.border_frame_manager import BorderFrameManager
from ..utils.hotkey_manager import CtypesHotkeyManager
from ..utils.sound_manager import SoundManager
from .pathfinding_manager import PathfindingManager
from ..utils.debug_log import LOG, LOG_ERROR, LOG_INFO


class MacroEngine:
    """重构后的宏引擎 - 专注于状态管理和事件协调"""

    VALID_TRANSITIONS = {
        MacroState.STOPPED: [MacroState.READY],
        MacroState.READY: [MacroState.RUNNING, MacroState.STOPPED],
        MacroState.RUNNING: [MacroState.PAUSED, MacroState.STOPPED],
        MacroState.PAUSED: [MacroState.RUNNING, MacroState.STOPPED],
    }

    def __init__(
        self, hotkey_manager=None, sound_manager=None, config_file: str = "default.json"
    ):
        self._state = MacroState.STOPPED
        self._prepared_mode = "none"  # 'none', 'combat', 'pathfinding'
        self._state_lock = threading.RLock()
        self._transition_lock = threading.Lock()
        self._skills_config: Dict[str, Any] = {}
        self._global_config: Dict[str, Any] = {}
        self.current_config_file = config_file
        self._is_debug_mode_active = False # 跟踪当前是否处于调试模式（由配置和状态决定）

        # 用于追踪当前注册的热键
        self._registered_stationary_hotkey = None
        self._registered_force_move_hotkey = None
        self._registered_pathfinding_hotkey = None

        # 原地模式状态（切换模式）
        self._stationary_mode_active = False
        # 强制移动状态（按住模式）
        self._force_move_active = False

        self.config_manager = ConfigManager()
        
        # Initialize DebugDisplayManager first, as others depend on it
        from .debug_display_manager import DebugDisplayManager
        from .unified_scheduler import UnifiedScheduler as DebugScheduler # Alias to avoid conflict
        debug_scheduler = DebugScheduler()
        self.debug_display_manager = DebugDisplayManager(event_bus, debug_scheduler)
        # 不自动启动DebugScheduler，只在需要时启动
        self.debug_scheduler = debug_scheduler

        self.input_handler = InputHandler(hotkey_manager=hotkey_manager, debug_display_manager=self.debug_display_manager)
        self.border_manager = BorderFrameManager()
        self.sound_manager = sound_manager or SoundManager()

        # 初始化ResourceManager
        from .resource_manager import ResourceManager
        self.resource_manager = ResourceManager(
            self.border_manager, self.input_handler, debug_display_manager=self.debug_display_manager
        )

        # Pass debug_display_manager to SkillManager
        self.skill_manager = SkillManager(self.input_handler, self, self.border_manager, self.resource_manager, debug_display_manager=self.debug_display_manager)

        from .simple_affix_reroll_manager import SimpleAffixRerollManager
        self.affix_reroll_manager = SimpleAffixRerollManager(
            self.border_manager, self.input_handler
        )
        self.pathfinding_manager = PathfindingManager(
            self.border_manager, self.input_handler
        )

        self.hotkey_manager = hotkey_manager or CtypesHotkeyManager()

        self._setup_event_subscriptions()
        self.load_config(self.current_config_file)
        self._setup_hotkeys()

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

    def _setup_hotkeys(self):
        self.hotkey_manager.register_key_event(
            "f8", on_press=self._handle_f8_press, suppress="always"
        )
        self.hotkey_manager.register_key_event(
            "z", on_press=self._on_z_key_press, suppress="conditional"
        )
        self.hotkey_manager.register_key_event(
            "f7", on_press=self._on_f7_key_press, suppress="conditional"
        )
        self.hotkey_manager.set_suppress_condition_callback(
            self._should_suppress_hotkey
        )
        if not self.hotkey_manager.start_listening():
            LOG_ERROR("热键管理器启动失败！")

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
                    LOG_ERROR(f"[状态转换] _on_state_enter 异常详情:\n{traceback.format_exc()}")
                    # 即使_on_state_enter失败，也要继续发布事件
                    pass

                try:
                    event_bus.publish("engine:state_changed", new_state, old_state)
                    # 在状态转换时，总是包含当前的原地模式状态，确保状态同步
                    self._publish_status_update(stationary_mode=self._stationary_mode_active)
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
        if state == MacroState.STOPPED:

            self.skill_manager.stop()
            self.pathfinding_manager.stop()
            self.resource_manager.stop()
            self.border_manager.stop()
            self.input_handler.cleanup()
            self._prepared_mode = "none"

        elif state == MacroState.READY:
            # 激活目标窗口并做准备动作
            self.input_handler.activate_target_window()
            self.input_handler.start()
            self.skill_manager.prepare_border_only()  # 预计算边框
            self.border_manager.enable_debug_save()

            # 收集资源区域配置，用于模板截取
            resource_regions = self._collect_resource_regions()
            self.border_manager.capture_once_for_debug_and_cache(
                self._global_config.get("capture_interval", 40),
                resource_regions
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

    def _publish_status_update(
        self,
        stationary_mode: Optional[bool] = None,
        force_move_active: Optional[bool] = None,
    ):
        status_info = {
            "state": self._state,
            "queue_length": self.input_handler.get_queue_length(),
        }
        
        # 🔥 关键修复：总是发送当前完整状态，确保状态同步
        # 使用内部状态变量，而不是参数值
        status_info["stationary_mode"] = self._stationary_mode_active
        status_info["force_move_active"] = self._force_move_active
        
        event_bus.publish("engine:status_updated", status_info)

    def _update_osd_visibility(self):
        """根据当前宏状态和调试模式配置，控制DEBUG OSD的显示/隐藏"""
        # Debug模式启用且程序在READY/RUNNING/PAUSED状态时才显示OSD
        should_show_debug_osd = (
            self._is_debug_mode_active and 
            self._state in [MacroState.READY, MacroState.RUNNING, MacroState.PAUSED]
        )
        
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
            LOG_INFO(f"[热键] F8按键处理开始，当前状态: {self._state}")
            with self._transition_lock:
                if self._state == MacroState.STOPPED:
                    LOG_INFO("[热键] F8 - 从STOPPED状态启动")
                    if full_config:
                        self._skills_config = full_config.get("skills", {})
                        self._global_config = full_config.get("global", {})
                        self.sound_manager.update_config(self._global_config)
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
            LOG_INFO(f"[热键] toggle_pause_resume 返回结果: {result}, 新状态: {self._state}")
        except Exception as e:
            LOG_ERROR(f"[热键] Z键处理异常: {e}")
            import traceback
            LOG_ERROR(f"[热键] Z键异常详情:\n{traceback.format_exc()}")

    def _on_z_key_press(self):
        event_bus.publish("hotkey:z_press")

    def _should_suppress_hotkey(self, key_name: str) -> bool:
        if key_name.lower() in ["f7", "f9"]:
            return True
        return self._state not in [MacroState.STOPPED]

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
        """响应配置更新，安全地更新所有可配置的热键和资源管理器。"""
        # 更新资源管理器配置
        resource_config = global_config.get("resource_management", {})
        if resource_config:
            self.resource_manager.update_config(resource_config)

        # 更新调试模式状态
        debug_mode_enabled = global_config.get("debug_mode", {}).get("enabled", False)
        self._is_debug_mode_active = debug_mode_enabled
        self.input_handler.set_dry_run_mode(debug_mode_enabled)
        LOG_INFO(f"[DEBUG MODE] _on_config_updated: 干跑模式已设置为 {debug_mode_enabled}")

        # 提取新的热键配置
        stationary_config = global_config.get("stationary_mode_config", {})
        pathfinding_config = global_config.get("pathfinding_config", {})

        new_hotkeys = {
            "stationary": stationary_config.get("hotkey", "").strip().lower(),
            "force_move": stationary_config.get("force_move_hotkey", "")
            .strip()
            .lower(),
            "pathfinding": pathfinding_config.get("hotkey", "").strip().lower(),
        }

        # 逐个安全地更新热键
        for hotkey_type, new_key in new_hotkeys.items():
            if not self._update_hotkey_safely(hotkey_type, new_key):
                # 如果更新失败，向UI发送通知
                event_bus.publish("ui:hotkey_update_failed", hotkey_type, new_key)

        # 更新OSD可见性
        self._update_osd_visibility()

    def _update_hotkey_safely(self, hotkey_type: str, new_key: str) -> bool:
        """安全地更新单个热键，实现原子化操作（先注册，后取消）。"""
        hotkey_map = {
            "stationary": {
                "getter": lambda: self._registered_stationary_hotkey,
                "setter": lambda key: setattr(
                    self, "_registered_stationary_hotkey", key
                ),
                "callback": self._on_stationary_key_press,
                "release_callback": self._on_stationary_key_release,
                "suppress": "conditional",
            },
            "force_move": {
                "getter": lambda: self._registered_force_move_hotkey,
                "setter": lambda key: setattr(
                    self, "_registered_force_move_hotkey", key
                ),
                "callback": self._on_force_move_key_press,
                "release_callback": self._on_force_move_key_release,
                "suppress": False,
            },
            "pathfinding": {
                "getter": lambda: self._registered_pathfinding_hotkey,
                "setter": lambda key: setattr(
                    self, "_registered_pathfinding_hotkey", key
                ),
                "callback": self._on_f9_key_press,
                "release_callback": None,
                "suppress": "conditional",
            },
        }

        if hotkey_type not in hotkey_map:
            return False

        config = hotkey_map[hotkey_type]
        old_key = config["getter"]()

        # 如果新旧热键相同，则无需操作
        if old_key == new_key:
            return True

        # 尝试注册新热键
        if new_key:
            try:
                self.hotkey_manager.register_key_event(
                    new_key,
                    on_press=config["callback"],
                    on_release=config.get("release_callback"),
                    suppress=config["suppress"],
                )
                LOG_INFO(f"[热键管理] 成功注册新热键 '{hotkey_type}': {new_key}")
            except Exception as e:
                LOG_ERROR(f"[热键管理] 注册热键 '{hotkey_type}' ({new_key}) 失败: {e}")
                # 注册失败，保持原状，返回False
                return False

        # 新热键注册成功（或新热键为空），现在可以安全地取消旧热键
        if old_key:
            try:
                self.hotkey_manager.unregister_hotkey(old_key)
                LOG_INFO(f"[热键管理] 成功取消旧热键 '{hotkey_type}': {old_key}")
            except Exception as e:
                LOG_ERROR(
                    f"[热键管理] 取消旧热键 '{hotkey_type}' ({old_key}) 失败: {e}"
                )

        # 更新状态变量
        config["setter"](new_key if new_key else None)
        return True

    def _unregister_pathfinding_hotkey(self):
        """取消注册寻路模式热键"""
        if self._registered_pathfinding_hotkey:
            try:
                self.hotkey_manager.unregister_hotkey(
                    self._registered_pathfinding_hotkey
                )
                LOG_INFO(
                    f"[热键管理] 已取消寻路模式热键: {self._registered_pathfinding_hotkey}"
                )
            except Exception as e:
                LOG_ERROR(f"[热键管理] 取消寻路模式热键失败: {e}")
            self._registered_pathfinding_hotkey = None

    def _unregister_stationary_hotkeys(self):
        """取消注册原地模式和交互模式热键"""
        if self._registered_stationary_hotkey:
            try:
                self.hotkey_manager.unregister_hotkey(
                    self._registered_stationary_hotkey
                )
                LOG_INFO(
                    f"[热键管理] 已取消原地模式热键: {self._registered_stationary_hotkey}"
                )
            except Exception as e:
                LOG_ERROR(f"[热键管理] 取消原地模式热键失败: {e}")
            self._registered_stationary_hotkey = None

        if self._registered_force_move_hotkey:
            try:
                self.hotkey_manager.unregister_hotkey(
                    self._registered_force_move_hotkey
                )
                LOG_INFO(
                    f"[热键管理] 已取消交互/强制移动热键: {self._registered_force_move_hotkey}"
                )
            except Exception as e:
                LOG_ERROR(f"[热键管理] 取消交互/强制移动热键失败: {e}")
            self._registered_force_move_hotkey = None

    def _on_stationary_key_press(self):
        """原地模式热键按下事件 - 切换模式"""
        # 无论当前状态如何，都允许切换原地模式
        self._stationary_mode_active = not self._stationary_mode_active
        self._publish_status_update(stationary_mode=self._stationary_mode_active)
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
        self._publish_status_update(force_move_active=True)
        LOG_INFO("[交互模式] 已激活")

    def _on_force_move_key_release(self):
        """交互/强制移动热键释放事件 - 松开取消"""
        self._force_move_active = False
        self._publish_status_update(force_move_active=False)
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
            LOG_ERROR(f"[状态转换] toggle_pause_resume 异常详情:\n{traceback.format_exc()}")
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
        }
        return {"skills": default_skills, "global": default_global}

    def cleanup(self):
        """分层清理机制，确保按依赖关系安全地释放所有资源。"""
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
            # Layer 3: 释放系统级钩子和监听器
            ("系统资源层", [self.hotkey_manager]),
            # Layer 4: 关闭事件总线
            ("事件总线层", [event_bus]),
        ]

        # 执行分层清理
        for layer_name, components in cleanup_layers:
            self._cleanup_layer(layer_name, components)

        # 最后，清理自身状态
        self._unregister_stationary_hotkeys()
        self._unregister_pathfinding_hotkey()
        LOG_INFO("[清理] 所有组件清理完毕。")

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
