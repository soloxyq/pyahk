"""
AHK输入处理器
统一的输入接口，兼容原InputHandler的API
"""

import subprocess
import os
import time
from typing import Optional

from PySide6.QtCore import Qt

from torchlight_assistant.core.ahk_command_sender import AHKCommandSender
# AHKEventReceiver已移除，使用WM_COPYDATA通信
from torchlight_assistant.config.ahk_config import AHKConfig
from torchlight_assistant.core.signal_bridge import ahk_signal_bridge # 导入信号桥
from torchlight_assistant.utils.debug_log import LOG_INFO, LOG, LOG_ERROR


class AHKInputHandler:
    """
    基于AHK的完整输入处理器
    """

    # 永久根热键(F8 主控 / F7 洗练 / F9 寻路) — 业务配置侧禁止注册,避免覆盖 STOPPED 时的状态机 handler
    RESERVED_ROOT_KEYS = frozenset({"f8", "f7", "f9"})

    def __init__(self, event_bus=None, debug_display_manager=None):
        self.event_bus = event_bus
        self.debug_display_manager = debug_display_manager
        
        if not AHKConfig.validate():
            raise RuntimeError("AHK配置验证失败")
        
        self.ahk_window = AHKConfig.WINDOW_TITLE
        self.ahk_path = AHKConfig.AHK_PATH
        self.server_script = AHKConfig.SERVER_SCRIPT
        
        self.command_sender: Optional[AHKCommandSender] = None
        # event_receiver已移除，使用WM_COPYDATA通信
        self.ahk_process: Optional[subprocess.Popen] = None
        
        self.dry_run_mode = False
        # 当特殊按键激活时，丢弃所有非紧急（HP/MP以外）的入队请求
        self._drop_non_emergency = False
        # AHK 子进程死亡探测:命令发送失败时检查存活,首次发现进程退出即告警并上报(只报一次)
        self._ahk_death_notified = False
        # AHK 传输失效探测(进程活着但命令超时):同样只告警上报一次
        self._ahk_transport_failure_notified = False
        
        self._init_ahk_system()
        
        LOG_INFO("[AHK输入] 初始化完成")
    
    def _init_ahk_system(self):
        """初始化AHK系统"""
        if not self._start_ahk_server():
            raise RuntimeError("无法启动AHK服务器")
        
        try:
            self.command_sender = AHKCommandSender(self.ahk_window)
        except ConnectionError as e:
            raise RuntimeError(f"无法连接到AHK服务器: {e}")
        
        # 设置目标窗口
        if AHKConfig.WINDOW_EXE:
            target_str = f"ahk_exe {AHKConfig.WINDOW_EXE}"
            self.command_sender.set_target_window(target_str)

        # WM_COPYDATA 原生窗口过程必须尽快返回。READY 初始化包含捕获/OCR/磁盘操作,
        # 若用同线程直连,AHK 的 50ms SendMessageTimeoutW 会把正常 F8 处理误判为失败。
        # 显式队列连接把业务处理移到下一轮 Qt 事件循环,同时保持事件 FIFO 顺序。
        ahk_signal_bridge.ahk_event.connect(
            self._on_ahk_event,
            Qt.QueuedConnection,
        )

    def _on_ahk_event(self, event: str):
        """这个方法现在总是在主GUI线程中被调用"""
        if not self.event_bus:
            return
        
        parts = event.split(':', 1)
        event_type = parts[0]
        data = parts[1] if len(parts) > 1 else ""
        
        # 🎯 特殊处理special_key_pause事件
        if event_type == "special_key_pause":
            self.event_bus.publish(event_type, action=data)
        else:
            self.event_bus.publish(event_type, key=data)

    def _start_ahk_server(self) -> bool:
        """启动AHK服务器"""
        if not os.path.exists(self.server_script):
            LOG_INFO(f"[AHK输入] 脚本不存在: {self.server_script}")
            return False
        
        if not os.path.exists(self.ahk_path):
            LOG_INFO(f"[AHK输入] AHK不存在: {self.ahk_path}")
            return False
        
        try:
            self.ahk_process = subprocess.Popen(
                [self.ahk_path, self.server_script],
                cwd=os.getcwd(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
            LOG_INFO(f"[AHK输入] AHK服务器已启动 (PID: {self.ahk_process.pid})")
            
            time.sleep(AHKConfig.AHK_STARTUP_WAIT)
            
            return True
            
        except Exception as e:
            LOG_INFO(f"[AHK输入] 启动AHK失败: {e}")
            return False

    def is_ahk_alive(self) -> bool:
        """AHK 子进程是否仍存活(已被 stop() 主动清理或非预期退出都返回 False)。"""
        proc = self.ahk_process
        return proc is not None and proc.poll() is None

    def check_ahk_alive(self) -> bool:
        """命令发送失败时调用:探测 AHK 子进程是否已意外退出。

        - 进程已被 stop() 主动清理(ahk_process=None)→ 不视为崩溃,静默返回 False。
        - 进程对象仍在但已退出(有 returncode)→ 视为崩溃:首次发现时 LOG_ERROR,并经
          ahk_signal_bridge(队列连接)切回 GUI 线程发布 'ahk_process_died',由 MacroEngine
          接管(强制 STOPPED + 告警)。命令发送多在调度器 worker 线程,故必须经信号桥切回主线程。
        返回 AHK 是否存活。
        """
        proc = self.ahk_process
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        if not self._ahk_death_notified:
            self._ahk_death_notified = True
            LOG_ERROR(
                f"[AHK输入] ⚠️ AHK 子进程已意外退出 (returncode={proc.returncode})!"
                f"所有按键命令将静默失效,且可能残留卡死的持久按住键。正在请求停止主功能。"
            )
            try:
                ahk_signal_bridge.ahk_event.emit("ahk_process_died:")
            except Exception as e:
                LOG_ERROR(f"[AHK输入] 上报 AHK 死亡事件失败: {e}")
        return False

    def _check_send(self, ok: bool) -> bool:
        """命令发送返回假值时探测故障原因。透传原返回值。

        两类发送失败,分开处置:
        - 进程已退出 → check_ahk_alive 一次性发布 ahk_process_died;
        - 进程活着但 timeout → 一次性发布 ahk_transport_failed。
          失联后不强杀 AHK:它可能还压着物理按键,强杀会跳过 OnExit 释放,
          留给用户"重启应用"这一条明确出路。
        no_window 不熔断也不锁 F8:FindWindow 失败本身是微秒级且可能因脚本重启恢复,
        下一条命令会重新发现窗口。进入 READY 的闸门命令失败仍会回退 STOPPED。
        普通业务拒绝(rejected,如闸门关闭/未知命令)不属于故障,不触发上报。
        """
        if not ok:
            failure_kind = self._last_transport_failure_kind()
            alive = self.check_ahk_alive()
            if alive and failure_kind:
                self._notify_transport_failed(failure_kind)
        return ok

    def _last_transport_failure_kind(self) -> str:
        """返回最近一次致命传输失败类型；业务拒绝和本地异常不在此处停机。"""
        from hold_client import SEND_TIMEOUT

        sender = self.command_sender
        if sender is None:
            return ""
        kind = sender.last_failure_kind
        return kind if kind == SEND_TIMEOUT else ""

    def _notify_transport_failed(self, failure_kind: str = "timeout"):
        """AHK 传输失效的一次性告警 + 上报(经信号桥切回 GUI 线程)。"""
        if self._ahk_transport_failure_notified:
            return
        self._ahk_transport_failure_notified = True
        if self.command_sender is not None:
            self.command_sender.mark_transport_unavailable(failure_kind)
        reason = "500ms 无响应"
        LOG_ERROR(
            f"[AHK输入] ⚠️ 命令发送失败({reason}):AHK 进程仍在,但通信通道已失效。"
            "后续按键命令将全部失效。不会自动重启或强杀 AHK(可能残留按住的键),"
            "正在停止主功能 —— 请重启本应用。"
        )
        try:
            ahk_signal_bridge.ahk_event.emit(f"ahk_transport_failed:{failure_kind}")
        except Exception as e:
            LOG_ERROR(f"[AHK输入] 上报 AHK 传输失效事件失败: {e}")

    def send_key(self, key_str: str) -> bool:
        """
        发送按键
        """
        LOG(f"[AHK输入][DEBUG] send_key called with: {key_str}")
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Key:{key_str}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True
        
        # 特殊按键激活时，丢弃所有非紧急入队（send_key 视为非紧急）
        if self._drop_non_emergency:
            return False
        
        if "," in key_str:
            return self._check_send(self.command_sender.send_sequence(key_str, priority=2))
        else:
            return self._check_send(self.command_sender.send_key(key_str, priority=2))
    
    def activate_target_window(self):
        """请求AHK激活目标窗口"""
        LOG_INFO(f"[AHK输入] 正在请求AHK激活窗口...")
        return self._check_send(self.command_sender.activate_window())

    def set_target_window(self, target: str):
        """设置AHK的目标窗口"""
        LOG_INFO(f"[AHK输入] 正在设置AHK目标窗口: {target}")
        return self._check_send(self.command_sender.set_target_window(target))

    def set_send_mode(self, mode: str) -> bool:
        """设置 AHK 按键发送模式。"""
        return self._check_send(self.command_sender.set_send_mode(mode))

    def click_mouse(self, button: str = "left", hold_time: Optional[float] = None) -> bool:
        """
        点击鼠标
        """
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"Mouse:{button}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True

        # 特殊按键激活时，丢弃非紧急入队（鼠标点击视为非紧急）
        if self._drop_non_emergency:
            return False

        return self._check_send(self.command_sender.send_mouse_click(button, priority=2))

    def click_mouse_at(self, x: int, y: int, hold_time: Optional[float] = None) -> bool:
        """点击屏幕指定坐标

        ⚠️ 暂未实现 - 当前 AHK 命令协议中没有定义带坐标的鼠标点击命令。
        洗练(SimpleAffixRerollManager)和寻路(PathfindingManager)调用本方法,
        在新增 AHK 命令支持前会返回 False 并记录错误,而不会抛 AttributeError。

        TODO: 新增 CMD_MOUSE_CLICK_AT 命令,AHK 端用 `Click x, y` 实现。
        """
        LOG_ERROR(
            f"[AHK输入] click_mouse_at 暂未实现 (x={x}, y={y}, hold_time={hold_time})。"
            f"洗练/寻路功能需要新增 AHK 命令才能正常工作。"
        )
        return False
    
    def execute_skill_normal(self, key: str):
        # 🔧 BUG修复: 配置中 Key 字段允许序列(如 "delay50,1,delay100,2"),
        # 之前直接 send_normal 会把整串当作单个按键名 press: 出去导致无效。
        # 现在检测逗号自动走 send_sequence(AHK 端在 EnqueueAction 入口展开为原子动作)。
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self._check_send(self.command_sender.send_sequence(key, priority=2))
        else:
            self._check_send(self.command_sender.send_normal(key))

    def execute_skill_high(self, key: str):
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self._check_send(self.command_sender.send_sequence(key, priority=1))
        else:
            self._check_send(self.command_sender.send_high_priority(key))

    def execute_utility(self, key: str):
        if not key or self._drop_non_emergency:
            return
        if "," in key:
            self._check_send(self.command_sender.send_sequence(key, priority=3))
        else:
            self._check_send(self.command_sender.send_low_priority(key))
    
    def execute_hp_potion(self, key: str):
        if key:
            self._check_send(self.command_sender.send_emergency(key))
    
    def execute_mp_potion(self, key: str):
        if key:
            self._check_send(self.command_sender.send_emergency(key))

    def set_skill_hold_keys(self, keys) -> bool:
        """声明式下发 TriggerMode=2 期望持键的**完整集合**(取代旧 hold_key/release_key)。

        Python 只声明"配置期望按住哪些键";AHK 端独占维护"实际已按下哪些键"并做
        幂等差量同步。因此不再存在"入队成功但动作被清掉"导致的账本分叉。

        干跑规则(与 stop_macro/clear_queue 同类):
        - **空集合 = 安全清理命令**,即使干跑也必须真实下发,否则会留下卡键;
        - 非空集合会产生真实按键,干跑时只记 OSD 不下发。
        """
        key_list = [str(k).strip() for k in (keys or []) if str(k).strip()]
        if key_list and self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"SkillHold:{','.join(key_list)}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True
        return self._check_send(
            self.command_sender.set_skill_hold_keys(
                key_list,
                force=not key_list,
            )
        )

    def set_macro_steps(self, steps) -> bool:
        """把通用宏步骤下发给 AHK 端解释器。"""
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action(f"MacroSteps:{len(steps or [])}")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True
        return self._check_send(self.command_sender.set_macro_steps(steps))

    def start_macro(self) -> bool:
        """启动 AHK 端通用宏循环。"""
        if self.dry_run_mode:
            if self.debug_display_manager:
                try:
                    self.debug_display_manager.add_action("MacroStart")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
            return True
        return self._check_send(self.command_sender.start_macro())

    def stop_macro(self) -> bool:
        """停止 AHK 端通用宏循环并释放宏持键。

        🔧 **安全清理命令,永不被干跑拦截**。AHK 端宏是跨进程的持久状态:
        若干跑标志在宏运行期间被打开而这里静默吞掉命令,AHK 会继续真实发键、
        且 down 步骤的键永久卡住(F8 也停不下来)。与 clear_queue 同类处理。
        """
        if self.dry_run_mode and self.debug_display_manager:
            try:
                self.debug_display_manager.add_action("MacroStop")
            except Exception as e:
                LOG_INFO(f"[AHK输入] 添加调试动作失败: {e}")
        return self._check_send(self.command_sender.stop_macro(force=True))

    def set_accepting_actions(self, enabled: bool) -> bool:
        """运行时闸门。关闸 = AHK 端原子停止屏障。

        关闸在 AHK 端同一条消息内完成 清队(-1)+停宏+释放全部持键,并封住所有
        输入生产路径(EnqueueAction/MacroTick/HandleManagedKey/START_MACRO/
        非空持键声明);安全清理命令(清队/停宏/空持键声明/释放)始终放行。
        🔧 **本命令自身也是安全命令,永不被干跑拦截**(只会减少按键,不会产生按键)。
        进入 STOPPED/PAUSED 时第一件事就关掉:UnifiedScheduler.stop() 只 join 2 秒,
        超时的在飞回调之后仍可能下发命令,而那时 Python 侧停止流程已经走完。
        """
        return self._check_send(
            self.command_sender.set_accepting_actions(
                enabled,
                force=not enabled,
            )
        )

    def clear_queue(self):
        """清空所有队列(含 emergency)。用于 PAUSED 状态完全停下。"""
        return self._check_send(self.command_sender.clear_queue(-1, force=True))

    def clear_non_emergency_queue(self):
        """只清非紧急队列,保留 emergency。用于管理按键期间保留 HP/MP 救命动作。"""
        return self._check_send(self.command_sender.clear_queue(-2, force=True))

    def get_queue_stats(self) -> dict:
        return {"wm_copydata_mode": True}
    
    def register_root_hook(self, key: str):
        """注册永久根热键(F8/F7/F9 专用,intercept 模式)。

        绕过两层 register_hook 的保留键检查,仅供 MacroEngine._setup_primary_hotkey 调用。
        """
        return self._check_send(self.command_sender.register_root_hook(key))

    def register_hook(self, key: str, mode: str = "intercept"):
        """
        注册业务热键 Hook

        Args:
            key: 按键名（使用AHK标准名称，如 "RButton", "space"）
            mode: Hook模式

        Returns:
            注册是否成功;若 key 是保留根热键(F8/F7/F9),返回 False
        """
        # 🔧 保留键防御:F8/F7/F9 是永久根热键,业务侧禁止注册
        # 即使 AHK 端 RegisterHook 不会把它们记入 RegisteredHooks,Hotkey 绑定仍会被覆盖,
        # 导致 STOPPED 时按 F8/F7/F9 走错 handler。永久注册请用 register_root_hook。
        if key and key.lower() in self.RESERVED_ROOT_KEYS:
            LOG_ERROR(
                f"[register_hook] 拒绝注册保留根热键 '{key}' (mode={mode})。"
                f"F8/F7/F9 是永久根热键,业务配置不可覆盖。"
            )
            return False
        return self._check_send(self.command_sender.register_hook(key, mode))
    
    def unregister_hook(self, key: str):
        return self._check_send(self.command_sender.unregister_hook(key))

    def pause_queue(self):
        return self._check_send(self.command_sender.pause())

    def resume_queue(self):
        return self._check_send(self.command_sender.resume())
    
    def set_force_move_state(self, active: bool) -> bool:
        """设置强制移动状态"""
        return self._check_send(self.command_sender.set_force_move_state(active))
    
    def set_force_move_key(self, key: str) -> bool:
        """设置强制移动键"""
        return self._check_send(self.command_sender.set_force_move_key(key))
    
    def set_force_move_replacement_key(self, key: str) -> bool:
        """设置强制移动替换键"""
        return self._check_send(self.command_sender.set_force_move_replacement_key(key))

    def set_force_move_passthrough_keys(self, keys) -> bool:
        """设置强制移动白名单(位移技能,如 RButton 闪现)"""
        return self._check_send(self.command_sender.set_force_move_passthrough_keys(keys))

    def set_stationary_mode(self, active: bool, mode_type: str = "shift_modifier") -> bool:
        """设置原地模式状态。"""
        return self._check_send(
            self.command_sender.set_stationary_mode(active, mode_type)
        )

    def set_managed_key_config(
        self, key: str, target: str, delay: int, hold_ms: int = 0
    ) -> bool:
        """设置管理按键配置。"""
        return self._check_send(
            self.command_sender.set_managed_key_config(key, target, delay, hold_ms)
        )

    def batch_update_config(self, config_dict: dict) -> bool:
        """批量更新 AHK 端缓存配置。"""
        return self._check_send(self.command_sender.batch_update_config(config_dict))

    def clear_all_configurable_hooks(self) -> bool:
        """清空所有可配置的Hook（保留 F8/F7/F9 永久根热键）"""
        return self._check_send(
            self.command_sender.clear_all_configurable_hooks(force=True)
        )
    
    def set_python_window_state(self, state: str) -> bool:
        """设置Python窗口状态
        
        Args:
            state: "main" 或 "osd"
        """
        return self._check_send(self.command_sender.set_python_window_state(state))
    
    def set_drop_non_emergency(self, enabled: bool):
        """在特殊按键激活时启用，丢弃所有非紧急入队"""
        self._drop_non_emergency = enabled
    
    def start(self):
        pass
    
    def cleanup(self):
        self.stop()
    
    def stop(self):
        if not self.ahk_process:
            return

        LOG_INFO("[AHK输入] 正在停止...")

        # 事件接收由 ahk_event_filter.AHKEventFilter 全局过滤器处理,无需在此清理

        process = self.ahk_process
        self.ahk_process = None

        if process.poll() is None:
            # 1) 优雅关闭:Windows 上 terminate() 是 TerminateProcess,**不会**触发 AHK 的
            #    OnExit,持键会残留在游戏里。所以先请 AHK 自己释放持键再退出。
            graceful = False
            try:
                if self.command_sender.shutdown():
                    process.wait(timeout=2)
                    graceful = True
                    LOG_INFO("[AHK输入] AHK已优雅退出(持键已由AHK自行释放)")
            except subprocess.TimeoutExpired:
                LOG_ERROR("[AHK输入] AHK未在2秒内响应优雅关闭,改用强制终止")
            except Exception as e:
                LOG_ERROR(f"[AHK输入] 请求AHK优雅关闭失败: {e}")

            # 2) 兜底:优雅关闭失败才强制终止(此时持键可能残留,无法避免)
            if not graceful:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                    LOG_INFO("[AHK输入] AHK进程已终止")
                except Exception as e:
                    LOG_INFO(f"[AHK输入] 终止AHK进程失败: {e}")
                    try:
                        process.kill()
                    except Exception as e:
                        LOG_INFO(f"[AHK输入] 强制终止AHK进程失败: {e}")
        else:
            LOG_INFO("[AHK输入] AHK进程已退出")
        
        LOG_INFO("[AHK输入] 已停止")
    
    def set_dry_run_mode(self, enabled: bool):
        self.dry_run_mode = enabled
        # 注意:不要把含反斜杠的转义串写进 f-string 的 {} 表达式 —— Python<3.12 会在 import 期
        # 抛 SyntaxError(PEP 701 才放开)。把三元表达式抽到普通赋值,兼容所有版本。
        state = "开启" if enabled else "关闭"
        LOG_INFO(f"[AHK输入] 干跑模式已 {state}")
    
    def __del__(self):
        try:
            self.stop()
        except Exception as e:
            LOG_INFO(f"[AHK输入] 清理资源失败: {e}")
