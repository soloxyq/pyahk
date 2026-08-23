"""
AHK命令发送器
负责将Python的决策转换为AHK命令并发送
"""

import threading
import time

from hold_client import (
    send_ahk_cmd_ex,
    SEND_ERROR,
    SEND_NO_WINDOW,
    SEND_TIMEOUT,
)
from torchlight_assistant.config.ahk_commands import (
    CMD_PING, CMD_SET_TARGET, CMD_ACTIVATE, CMD_ENQUEUE,
    CMD_CLEAR_QUEUE, CMD_PAUSE, CMD_RESUME,
    CMD_HOOK_REGISTER, CMD_HOOK_UNREGISTER,
    CMD_SET_SEND_MODE,
    CMD_SET_MACRO_STEPS, CMD_START_MACRO, CMD_STOP_MACRO,
    get_command_name
)
from torchlight_assistant.utils.debug_log import LOG_INFO, LOG_ERROR


class AHKCommandSender:
    """
    AHK命令发送器
    
    职责:
    - 将动作加入AHK队列
    - 管理Hook注册
    - 控制队列暂停/恢复
    - 处理原地模式的shift修饰符
    """

    # timeout 只说明发送方在预算内没有等到返回，消息可能已经进入 AHK 队列。
    # 留出安静窗口再探测，避免 GUI/游戏短暂停顿时连续制造 500ms 等待。
    TRANSPORT_PROBE_COOLDOWN_SECONDS = 2.0
    
    def __init__(self, window_title: str = "HoldServer_Window_UniqueName_12345"):
        self.window_title = window_title
        self._stationary_mode_active = False
        self._stationary_mode_type = "shift_modifier"
        # 失败类型必须按发送线程保存。GUI 与调度线程可能同时发命令,若共用一个字段,
        # 线程 A 超时后会被线程 B 的成功结果覆盖,_check_send 就漏报真正的挂死。
        self._send_state = threading.local()
        # 首次 timeout 后普通命令立即失败,避免生产侧继续堆积更多 500ms 等待。
        # no_window 是微秒级的可恢复发现失败,不打开永久熔断。安全清理命令显式
        # force=True 绕过熔断,保证 STOPPED 仍有一次真实的止血机会。
        self._transport_failure_kind = ""
        self._transport_circuit_lock = threading.Lock()
        self._next_transport_probe_at = 0.0
        self._check_connection()

    @property
    def last_failure_kind(self) -> str:
        return getattr(self._send_state, "last_failure_kind", "")

    @last_failure_kind.setter
    def last_failure_kind(self, kind: str):
        self._send_state.last_failure_kind = kind

    def mark_transport_unavailable(self, kind: str):
        """熔断普通命令；保留安全清理与 shutdown 的强制发送机会。"""
        if kind:
            with self._transport_circuit_lock:
                self._transport_failure_kind = kind
                self._next_transport_probe_at = max(
                    self._next_transport_probe_at,
                    time.monotonic() + self.TRANSPORT_PROBE_COOLDOWN_SECONDS,
                )

    @property
    def transport_unavailable(self) -> bool:
        """普通命令是否正被 timeout 熔断。"""
        return bool(self._transport_failure_kind)

    def recover_transport(self) -> bool:
        """在冷却期后串行探测通信，成功才解除普通命令熔断。

        探测只发送无副作用的 PING，并且不会重放触发故障的业务命令。锁使用
        non-blocking 获取：GUI 与调度线程同时请求恢复时只有一个线程最多等待
        一次 500ms，其余调用立即失败，维持有界等待和 fail-closed 语义。
        """
        if not self.transport_unavailable:
            return True

        if time.monotonic() < self._next_transport_probe_at:
            self.last_failure_kind = self._transport_failure_kind
            return False

        if not self._transport_circuit_lock.acquire(blocking=False):
            self.last_failure_kind = self._transport_failure_kind
            return False

        try:
            # 获取锁前后的时间可能跨过较长调度间隙，必须在锁内再次检查。
            if not self._transport_failure_kind:
                return True
            now = time.monotonic()
            if now < self._next_transport_probe_at:
                self.last_failure_kind = self._transport_failure_kind
                return False

            # 在实际发送前先推进下一探测时间。即使 ctypes 层发生异常，也不会
            # 让随后每条业务命令立刻再次探测并各等待 500ms。
            self._next_transport_probe_at = (
                now + self.TRANSPORT_PROBE_COOLDOWN_SECONDS
            )
            try:
                ok, kind = send_ahk_cmd_ex(self.window_title, CMD_PING, "")
            except Exception as e:
                LOG_ERROR(f"[AHKCommandSender] 通信恢复探测异常: {e}")
                ok, kind = False, SEND_ERROR

            self.last_failure_kind = "" if ok else kind
            if not ok:
                # 保留原 timeout 熔断原因。探测时暂时找不到窗口不代表可以重新
                # 放行业务命令；用户重启 AHK 后下一次冷却探测仍可恢复。
                return False

            self._transport_failure_kind = ""
            self._next_transport_probe_at = 0.0
            LOG_INFO("[AHKCommandSender] AHK 通信探测成功，普通命令熔断已解除")
            return True
        finally:
            self._transport_circuit_lock.release()

    def probe_transport(self) -> bool:
        """兼容别名；新调用方使用 recover_transport。"""
        return self.recover_transport()

    def _send(self, cmd_id, param: str = "", *, force: bool = False) -> bool:
        """统一发送包装器:所有 AHK 命令必须经此发出,不得直接调 hold_client。

        - **永不抛异常**(send_ahk_cmd_ex 已保证,这里是第二道防线):
          调用方普遍写 `_check_send(sender.xxx())`,异常在参数求值阶段逃逸
          会整个绕过 _check_send 里的死亡/挂死探测。
        - 记录最近一次失败类型,供上层区分故障(timeout/no_window/rejected)。
        """
        terminal_kind = getattr(self, "_transport_failure_kind", "")
        if terminal_kind and not force:
            self.last_failure_kind = terminal_kind
            return False

        try:
            ok, kind = send_ahk_cmd_ex(self.window_title, cmd_id, param)
        except Exception as e:  # 防御性兜底,正常情况下 send_ahk_cmd_ex 不抛
            LOG_ERROR(f"[AHKCommandSender] 发送命令异常(cmd={cmd_id}): {e}")
            ok, kind = False, SEND_ERROR
        self.last_failure_kind = "" if ok else kind
        if not ok and kind == SEND_TIMEOUT:
            self.mark_transport_unavailable(kind)
        return ok

    def _check_connection(self):
        """检查AHK连接"""
        if not self._send(CMD_PING):
            raise ConnectionError(
                "无法连接到AHK服务器！\n"
                "请确保 hold_server_extended.ahk 正在运行。\n"
                f"窗口标题: {self.window_title}"
            )
    
    # ========================================================================
    # 窗口管理
    # ========================================================================

    def set_target_window(self, target: str) -> bool:
        """设置AHK的目标窗口标识符 (例如 'ahk_exe notepad++.exe')"""
        return self._send(CMD_SET_TARGET, target)
    
    def set_send_mode(self, mode: str) -> bool:
        """设置按键发送模式
        
        Args:
            mode: 发送模式
                - "direct": 直接发送 (SendInput), 需要窗口激活
                - "control": 控件发送 (ControlSend), 后台发送不需要激活
        
        Returns:
            是否成功
        """
        if mode not in ["direct", "control"]:
            raise ValueError(f"Invalid send mode: {mode}. Must be 'direct' or 'control'")
        return self._send(CMD_SET_SEND_MODE, mode)

    def activate_window(self) -> bool:
        """请求AHK激活当前设置的目标窗口"""
        return self._send(CMD_ACTIVATE)

    # ========================================================================
    # 原地模式管理
    # ========================================================================
    
    def set_stationary_mode(
        self,
        active: bool,
        mode_type: str = "shift_modifier",
        *,
        force: bool = False,
    ):
        """设置原地模式状态；关闭方向可作为停机安全命令强制发送。"""
        self._stationary_mode_active = active
        self._stationary_mode_type = mode_type
        
        # 发送命令到AHK
        from torchlight_assistant.config.ahk_commands import CMD_SET_STATIONARY
        param = f"{'true' if active else 'false'}:{mode_type}"
        return self._send(CMD_SET_STATIONARY, param, force=force)
    
    def set_force_move_key(self, key: str):
        """设置强制移动键"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_KEY
        return self._send(CMD_SET_FORCE_MOVE_KEY, key)
    
    def set_force_move_state(self, active: bool, *, force: bool = False):
        """设置强制移动状态；关闭方向可由上层作为安全命令强制发送。"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_STATE
        param = "true" if active else "false"
        return self._send(CMD_SET_FORCE_MOVE_STATE, param, force=force)
    
    def set_force_move_replacement_key(self, key: str):
        """设置强制移动替换键"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_FORCE_MOVE_REPLACEMENT_KEY
        return self._send(CMD_SET_FORCE_MOVE_REPLACEMENT_KEY, key)

    def set_force_move_passthrough_keys(self, keys) -> bool:
        """设置强制移动期间不被替换的白名单键(位移技能,如 RButton 闪现)

        Args:
            keys: 键名列表(可迭代),AHK 端会小写化匹配,空列表/None 清空白名单
        """
        from torchlight_assistant.config.ahk_commands import (
            CMD_SET_FORCE_MOVE_PASSTHROUGH_KEYS,
        )
        param = ",".join(str(k).strip() for k in (keys or []) if str(k).strip())
        return self._send(CMD_SET_FORCE_MOVE_PASSTHROUGH_KEYS, param)
    
    def clear_all_configurable_hooks(self, *, force: bool = False) -> bool:
        """清空所有可配置的Hook（保留 F8/F7/F9 永久根热键）"""
        from torchlight_assistant.config.ahk_commands import CMD_CLEAR_HOOKS
        return self._send(CMD_CLEAR_HOOKS, "", force=force)
    
    def set_python_window_state(self, state: str) -> bool:
        """设置Python窗口状态
        
        Args:
            state: "main" 或 "osd"
        """
        from torchlight_assistant.config.ahk_commands import CMD_SET_PYTHON_WINDOW_STATE
        return self._send(CMD_SET_PYTHON_WINDOW_STATE, state)
    
    def batch_update_config(self, config_dict: dict) -> bool:
        """批量更新配置（Master方案学习）
        
        Args:
            config_dict: 配置字典，如 {"hp_key": "1", "mp_key": "2", "stationary_type": "shift_modifier"}
        """
        from torchlight_assistant.config.ahk_commands import CMD_BATCH_UPDATE_CONFIG
        
        try:
            # 构建参数字符串： "hp_key:1,mp_key:2,stationary_type:shift_modifier"
            config_items = []
            for key, value in config_dict.items():
                # 数值 0 是有效配置(例如 special_key_resume_delay_ms=0 用于清除
                # 上一个配置的保护窗口),不能按 falsy 丢掉。None/空字符串才表示未配置。
                if value is not None and value != "":
                    config_items.append(f"{key}:{value}")
            
            if not config_items:
                return True  # 没有配置需要更新
            
            param = ",".join(config_items)
            result = self._send(CMD_BATCH_UPDATE_CONFIG, param)
            
            return result
        except Exception as e:
            LOG_INFO(f"【AHKCommandSender】 批量更新配置失败: {e}")
            import traceback
            LOG_INFO(f"【AHKCommandSender】 异常详情:\n{traceback.format_exc()}")
            return False
        
    def is_stationary_mode_active(self) -> bool:
        """检查原地模式是否激活"""
        return self._stationary_mode_active
    
    def set_managed_key_config(
        self, key: str, target: str, delay: int, hold_ms: int = 0
    ):
        """设置管理按键配置"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_MANAGED_KEY_CONFIG
        param = f"{key}:{target}:{delay}:{hold_ms}"
        return self._send(CMD_SET_MANAGED_KEY_CONFIG, param)

    # ========================================================================
    # AHK 端通用宏
    # ========================================================================

    @staticmethod
    def serialize_macro_steps(steps) -> str:
        """将 macro_steps 编码成 AHK 端 line protocol: ``type:data`` per line。"""
        lines = []
        if not isinstance(steps, list):
            return ""
        for step in steps:
            if not isinstance(step, dict):
                continue
            stype = step.get("type")
            if stype == "delay":
                try:
                    data = str(max(int(step.get("ms", 0)), 0))
                except (TypeError, ValueError):
                    continue
            elif stype in ("down", "up", "press"):
                data = str(step.get("key", "")).strip()
                if not data:
                    continue
            else:
                continue
            data = data.replace("\r", "").replace("\n", "")
            lines.append(f"{stype}:{data}")
        return "\n".join(lines)

    def set_macro_steps(self, steps) -> bool:
        """设置 AHK 端宏步骤列表。"""
        return self._send(CMD_SET_MACRO_STEPS, self.serialize_macro_steps(steps))

    def start_macro(self) -> bool:
        """启动 AHK 端宏循环。"""
        return self._send(CMD_START_MACRO, "")

    def stop_macro(self, *, force: bool = False) -> bool:
        """停止 AHK 端宏循环并释放宏持键。"""
        return self._send(CMD_STOP_MACRO, "", force=force)

    # ========================================================================
    # 队列操作
    # ========================================================================
    
    def enqueue(self, action: str, priority: int = 2) -> bool:
        """
        将动作加入AHK队列
        
        Args:
            action: 动作字符串，格式: "type:data"
            priority: 优先级 (0=emergency, 1=high, 2=normal, 3=low)
        
        Returns:
            是否成功
        """
        param = f"{priority}:{action}"
        return self._send(CMD_ENQUEUE, param)
    
    def send_key(self, key: str, priority: int = 2) -> bool:
        """
        发送按键
        
        Args:
            key: 按键名称 (如 "1", "q", "space")
            priority: 优先级
        """
        return self.enqueue(f"press:{key}", priority)
    

    
    def send_sequence(self, sequence: str, priority: int = 2) -> bool:
        """
        发送按键序列
        
        Args:
            sequence: 序列字符串 (如 "delay50,q,delay100,w")
            priority: 优先级
        """
        return self.enqueue(f"sequence:{sequence}", priority)
    
    def send_mouse_click(self, button: str = "left", priority: int = 2) -> bool:
        """
        发送鼠标点击
        
        Args:
            button: 按钮 ("left", "right", "middle")
            priority: 优先级
        """
        return self.enqueue(f"mouse_click:{button}", priority)

    def send_mouse_click_at(
        self, x: int, y: int, hold_ms: int = 0, priority: int = 2
    ) -> bool:
        """把坐标点击作为队列动作发送。

        协议格式为 ``mouse_click_at:x,y,hold_ms``。继续复用 CMD_ENQUEUE，
        使坐标点击与普通动作共享运行时闸门、优先级和过载控制。
        """
        return self.enqueue(f"mouse_click_at:{x},{y},{hold_ms}", priority)
    
    # 注意:TriggerMode=2 的持久按住键**不再**通过队列的 hold:/release: 动作实现,
    # 改用声明式 set_skill_hold_keys(见下方)。原因:入队成功 ≠ 已按下,
    # 且 normal 级 hold: 会被管理按键的 ClearNonEmergencyQueues 清掉,
    # 导致 Python 账本与实际状态分叉。队列里的 hold:/release: 现在只由
    # AHK 端管理按键的 hold_ms 配置内部生成。
    @staticmethod
    def serialize_skill_hold_keys(keys) -> str:
        """把期望持键集合序列化成 AHK 行协议(每行一个键名,顺序=按下顺序)。"""
        return "\n".join(
            str(k).strip() for k in (keys or []) if str(k).strip()
        )

    def set_skill_hold_keys(self, keys, *, force: bool = False) -> bool:
        """声明式下发 TriggerMode=2 期望持键的**完整集合**(空集合=释放全部)。

        AHK 端独占维护"实际已按下"状态并做差量同步,因此本命令幂等:
        重复下发同一集合不会产生额外按键。
        """
        from torchlight_assistant.config.ahk_commands import CMD_SET_SKILL_HOLD_KEYS

        return self._send(
            CMD_SET_SKILL_HOLD_KEYS,
            self.serialize_skill_hold_keys(keys),
            force=force,
        )

    def set_accepting_actions(self, enabled: bool, *, force: bool = False) -> bool:
        """运行时闸门。关闸 = AHK 端原子停止屏障(清场+封住所有输入生产路径)。

        用于"按了 F8/Z 之后绝不再有键打进游戏":Python 侧 join 调度线程只等 2 秒,
        超时后在飞回调仍可能下发命令,这道闸门是最后防线。AHK 端封锁点:
        EnqueueAction/MacroTick/HandleManagedKey/START_MACRO/非空持键声明;
        清队/停宏/空持键声明/释放等安全清理命令不受影响。
        """
        from torchlight_assistant.config.ahk_commands import CMD_SET_ACCEPTING_ACTIONS

        return self._send(
            CMD_SET_ACCEPTING_ACTIONS,
            "true" if enabled else "false",
            force=force,
        )

    def arm_main_mode(self) -> bool:
        """声明主状态机进入 READY；AHK 先原子关闸清场，再设置 armed 标记。"""
        from torchlight_assistant.config.ahk_commands import CMD_SET_ACCEPTING_ACTIONS

        return self._send(CMD_SET_ACCEPTING_ACTIONS, "arm_main")

    def shutdown(self) -> bool:
        """请求 AHK 自行释放全部持键后退出。

        必须优先于 terminate():Windows 上 Popen.terminate() 是 TerminateProcess,
        不会触发 AHK 的 OnExit,持键会残留在游戏里。
        """
        from torchlight_assistant.config.ahk_commands import CMD_SHUTDOWN

        return self._send(CMD_SHUTDOWN, force=True)


    # ========================================================================
    # 队列控制
    # ========================================================================
    
    def pause(self) -> bool:
        """暂停队列处理"""
        return self._send(CMD_PAUSE)
    
    def resume(self) -> bool:
        """恢复队列处理"""
        return self._send(CMD_RESUME)
    
    def clear_queue(self, priority: int = -1, *, force: bool = False) -> bool:
        """
        清空队列

        Args:
            priority: 要清空的队列
                -1 = 全部(包括 emergency)
                -2 = 仅非紧急(high/normal/low,保留 emergency 救命药剂)
                0-3 = 指定单个优先级队列
        """
        return self._send(CMD_CLEAR_QUEUE, str(priority), force=force)
    
    # ========================================================================
    # Hook管理
    # ========================================================================
    
    # 永久根热键(F8 主控 / F7 洗练 / F9 寻路) — 与 AHKInputHandler 同形,深度防御
    # 任何调用方(包括误用底层 sender)用 register_hook 注册 F8/F7/F9 都会被拒绝;
    # 永久根热键注册请走 register_root_hook。
    RESERVED_ROOT_KEYS = frozenset({"f8", "f7", "f9"})

    def _send_hook_register(self, key: str, mode: str) -> bool:
        """实际发送注册命令到 AHK,无保留键检查 — 仅 register_hook / register_root_hook 内部使用。"""
        param = f"{key}:{mode}"
        return self._send(CMD_HOOK_REGISTER, param)

    def register_root_hook(self, key: str) -> bool:
        """注册永久根热键(F8/F7/F9 专用,intercept 模式)。绕过保留键检查。"""
        return self._send_hook_register(key, "intercept")

    def register_hook(self, key: str, mode: str = "intercept") -> bool:
        """
        注册业务 Hook

        Args:
            key: 按键名称
            mode: 模式 ("intercept"=拦截, "priority"=管理, "special"=特殊, "monitor"=监控, "block"=阻止)

        Returns:
            注册是否成功;若 key 是保留根热键(F8/F7/F9),返回 False
        """
        # 🔧 深度防御:即使有调用方绕过 AHKInputHandler 直接拿 command_sender,这里也拒绝保留键
        if key and key.lower() in self.RESERVED_ROOT_KEYS:
            LOG_ERROR(
                f"[AHKCommandSender.register_hook] 拒绝注册保留根热键 '{key}' (mode={mode})。"
                f"F8/F7/F9 是永久根热键,业务配置不可覆盖;永久注册请用 register_root_hook。"
            )
            return False
        return self._send_hook_register(key, mode)
    
    def unregister_hook(self, key: str) -> bool:
        """
        取消Hook
        
        Args:
            key: 按键名称
        """
        return self._send(CMD_HOOK_UNREGISTER, key)
    
    # 统计信息不提供请求接口:AHK 端每秒主动推送 "stats:" 事件(SendStatsToPython),
    # 旧的 get_stats() 把字符串当命令 ID 发送,从未工作过,已删除。

    # ========================================================================
    # 便捷方法
    # ========================================================================
    
    def send_emergency(self, key: str) -> bool:
        """发送紧急按键 (优先级0)"""
        return self.send_key(key, priority=0)
    
    def send_high_priority(self, key: str) -> bool:
        """发送高优先级按键 (优先级1)"""
        return self.send_key(key, priority=1)
    
    def send_normal(self, key: str) -> bool:
        """发送普通按键 (优先级2)"""
        return self.send_key(key, priority=2)
    
    def send_low_priority(self, key: str) -> bool:
        """发送低优先级按键 (优先级3)"""
        return self.send_key(key, priority=3)
