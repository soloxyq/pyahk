"""智能装备词缀自动洗练管理器 - 基于配置驱动和区域OCR"""

import threading
import time
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from .event_bus import event_bus
from ..utils.debug_log import LOG_INFO, LOG_ERROR
from ..utils.config_values import config_int


@dataclass
class SimpleAffixRerollConfig:
    """洗练功能的完整配置"""

    enabled: bool = False
    target_affixes: List[str] = field(default_factory=list)
    max_attempts: int = 100
    click_delay: int = 200
    enchant_button_coord: Optional[Tuple[int, int]] = None
    first_affix_button_coord: Optional[Tuple[int, int]] = None
    replace_button_coord: Optional[Tuple[int, int]] = None
    close_button_coord: Optional[Tuple[int, int]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SimpleAffixRerollConfig":
        if not isinstance(data, dict):
            return cls()

        def parse_int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = config_int(data.get(name, default))
            except ValueError:
                return default
            return min(max(value, minimum), maximum)

        def parse_coord(name: str) -> Optional[Tuple[int, int]]:
            raw = data.get(name)
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                return None
            try:
                x, y = config_int(raw[0]), config_int(raw[1])
            except ValueError:
                return None
            if not (-2147483648 <= x <= 2147483647):
                return None
            if not (-2147483648 <= y <= 2147483647):
                return None
            return x, y

        raw_targets = data.get("target_affixes", [])
        target_affixes = (
            [value.strip() for value in raw_targets if isinstance(value, str) and value.strip()]
            if isinstance(raw_targets, list)
            else []
        )
        return cls(
            enabled=data.get("enabled") is True,
            target_affixes=target_affixes,
            max_attempts=parse_int("max_attempts", 100, 1, 1000),
            click_delay=parse_int("click_delay", 200, 0, 5000),
            enchant_button_coord=parse_coord("enchant_button_coord"),
            first_affix_button_coord=parse_coord("first_affix_button_coord"),
            replace_button_coord=parse_coord("replace_button_coord"),
            close_button_coord=parse_coord("close_button_coord"),
        )


@dataclass
class SimpleAffixRerollStatus:
    """洗练状态"""

    is_running: bool = False
    current_attempts: int = 0
    current_state: str = "idle"
    last_affixes: List[str] = field(default_factory=list)
    matched_affix: str = ""
    error_message: str = ""


@dataclass(eq=False)
class _AffixRerollRun:
    """One immutable-identity reroll generation.

    A timed-out worker may outlive its public stop operation. Keeping its status,
    config and stop event here prevents that worker from mutating a later run.
    """

    generation: int
    config: SimpleAffixRerollConfig
    status: SimpleAffixRerollStatus
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None


class SimpleAffixRerollManager:
    """配置驱动的智能洗练管理器"""

    WORKER_JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(self, border_manager, input_handler):
        self.border_manager = border_manager
        self.input_handler = input_handler
        self.config = SimpleAffixRerollConfig()
        self.status = SimpleAffixRerollStatus()
        self.ocr_manager = None  # Lazy-loaded OCR manager
        self._reroll_thread = None
        self._run_lock = threading.RLock()
        self._run_generation = 0
        self._active_run: Optional[_AffixRerollRun] = None
        self._gate_owner_generation: Optional[int] = None
        self._cleanup_done = False
        event_bus.subscribe("hotkey:affix_reroll_start", self._on_f7_pressed)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)
        LOG_INFO("[配置驱动洗練管理器] 初始化完成")

    def _on_f7_pressed(self, runtime_epoch: Optional[int] = None):
        if getattr(self, "_cleanup_done", False):
            return
        if self.status.is_running:
            self.stop_reroll("用户按F7停止")
        else:
            self.start_reroll(runtime_epoch=runtime_epoch)

    def _on_config_updated(
        self, skills_config: Dict[str, Any], global_config: Dict[str, Any]
    ):
        if getattr(self, "_cleanup_done", False):
            return
        config_data = (
            global_config.get("affix_reroll", {})
            if isinstance(global_config, dict)
            else {}
        )
        LOG_INFO(f"[洗练管理器] 接收到配置更新: {config_data}")
        self.config = SimpleAffixRerollConfig.from_dict(config_data)
        LOG_INFO(
            f"[洗练管理器] 配置更新完成: 启用={self.config.enabled}, 目标词缀={self.config.target_affixes}"
        )

    def start_reroll(self, runtime_epoch: Optional[int] = None) -> bool:
        with self._run_lock:
            if self._active_run is not None or self.status.is_running:
                return False
        required_coordinates = (
            self.config.enchant_button_coord,
            self.config.first_affix_button_coord,
            self.config.replace_button_coord,
            self.config.close_button_coord,
        )
        if (
            not self.config.enabled
            or not self.config.target_affixes
            or any(coordinate is None for coordinate in required_coordinates)
        ):
            LOG_ERROR("[配置驱动洗練管理器] 配置无效或不完整，无法启动")
            return False

        # 坐标点击绝不允许沿用 TargetWin="" 的“当前前台窗口”兼容语义。
        # 该检查也覆盖绕过 MacroEngine、直接发布 F7 事件的调用方。
        from ..utils.window_utils import WindowUtils

        window_config = getattr(
            self.border_manager, "window_activation_config", {}
        ) or {}
        target = WindowUtils.build_ahk_target(window_config)
        if not target:
            LOG_ERROR("[洗练管理器] 未配置显式目标窗口，拒绝坐标点击模式")
            self.status.error_message = "洗练需要配置目标窗口"
            self._publish_status_update()
            return False
        try:
            target_synced = bool(self.input_handler.set_target_window(target))
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] 同步目标窗口异常: {e}")
            target_synced = False
        if not target_synced:
            self.status.error_message = "目标窗口同步失败"
            self._publish_status_update()
            return False

        # 获取全局OCR管理器实例
        if self.ocr_manager is None:
            try:
                from ..utils.paddle_ocr_manager import get_paddle_ocr_manager

                self.ocr_manager = get_paddle_ocr_manager()
                LOG_INFO("[洗练管理器] 获取全局PaddleOCR管理器成功")
            except Exception as e:
                LOG_ERROR(f"[洗练管理器] 获取PaddleOCR管理器失败: {e}")
                self.status.error_message = "OCR模块获取失败"
                self._publish_status_update()
                return False

        # 检查OCR是否就绪，如果没有就等待
        if not self.ocr_manager.is_ready():
            LOG_INFO("[洗练管理器] OCR引擎正在初始化，等待完成...")
            if not self.ocr_manager.wait_for_initialization(timeout=15.0):
                LOG_ERROR("[洗练管理器] OCR引擎初始化超时")
                self.status.error_message = "OCR引擎初始化超时"
                self._publish_status_update()
                return False

        # 洗练是 STOPPED 之外的独立输入模式，必须显式拥有 AHK 闸门。
        # 创建 generation 和开闸受同一把锁保护：超时未退出的旧 worker
        # 无法在新一轮开闸后夹入迟到点击。
        with self._run_lock:
            if self._active_run is not None or self.status.is_running:
                return False

            recover = getattr(self.input_handler, "recover_transport", None)
            if callable(recover) and not recover():
                self.status.error_message = "AHK通信尚未恢复"
                LOG_ERROR("[洗练管理器] AHK通信不可用，拒绝启动")
                publish_failure = True
                run = None
            else:
                try:
                    requested_epoch = int(runtime_epoch or 0)
                except (TypeError, ValueError, OverflowError):
                    requested_epoch = 0
                self._run_generation = max(
                    self._run_generation + 1, requested_epoch
                )
                run = _AffixRerollRun(
                    generation=self._run_generation,
                    config=SimpleAffixRerollConfig(
                        enabled=self.config.enabled,
                        target_affixes=list(self.config.target_affixes),
                        max_attempts=self.config.max_attempts,
                        click_delay=self.config.click_delay,
                        enchant_button_coord=self.config.enchant_button_coord,
                        first_affix_button_coord=self.config.first_affix_button_coord,
                        replace_button_coord=self.config.replace_button_coord,
                        close_button_coord=self.config.close_button_coord,
                    ),
                    status=SimpleAffixRerollStatus(is_running=True),
                )
                publish_failure = False
                owner_ready = self._set_runtime_owner(run.generation, "启动")
                if not owner_ready or not self._set_runtime_gate(
                    True, "启动", run.generation
                ):
                    run.status.is_running = False
                    run.status.error_message = (
                        "AHK运行所有权设置失败"
                        if not owner_ready
                        else "AHK输入闸门打开失败"
                    )
                    self.status = run.status
                    # timeout 不代表 owner/true 没执行；用一条原子 RESET 回滚，
                    # 不能留下迟到开闸或半建立的 owner。
                    self._reset_runtime("启动失败回滚")
                    run = None
                    publish_failure = True

            if run is not None:
                self._active_run = run
                self._gate_owner_generation = run.generation
                self.status = run.status
                try:
                    run.thread = threading.Thread(
                        target=self._reroll_loop, args=(run,), daemon=True
                    )
                    self._reroll_thread = run.thread
                    run.thread.start()
                except Exception as e:
                    LOG_ERROR(f"[洗练管理器] 启动工作线程失败: {e}")
                    run.stop_event.set()
                    run.status.is_running = False
                    run.status.error_message = "洗练线程启动失败"
                    self._active_run = None
                    self._gate_owner_generation = None
                    self._reset_runtime("启动回滚")
                    publish_failure = True
                    run = None

                if run is not None:
                    # 线程会先在 _run_lock 上等待。running 状态和 hide_ui 在释放
                    # generation 锁前完成；同步订阅者若在 status 回调中停掉本轮，
                    # 复核失败后绝不能再发一条迟到 hide 覆盖它的 show_ui。
                    LOG_INFO(
                        f"[配置驱动洗練管理器] 开始洗练，目标: "
                        f"{run.config.target_affixes}"
                    )
                    self._publish_status_update(run.status)
                    if self._active_run is run and not run.stop_event.is_set():
                        event_bus.publish("affix_reroll:hide_ui")

        if publish_failure:
            self._publish_status_update()
            return False
        return True

    def stop_reroll(
        self,
        reason: str = "用户手动停止",
        *,
        cleanup_runtime: bool = True,
    ):
        with self._run_lock:
            run = self._active_run
        if run is not None:
            self._stop_run(run, reason, cleanup_runtime=cleanup_runtime)

    def _set_runtime_owner(self, generation: int, context: str) -> bool:
        try:
            ok = bool(self.input_handler.set_runtime_owner("affix", generation))
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK owner 异常: {e}")
            return False
        if not ok:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK owner 失败")
        return ok

    def _set_runtime_gate(
        self, enabled: bool, context: str, generation: int
    ) -> bool:
        try:
            ok = bool(
                self.input_handler.set_accepting_actions(
                    enabled, owner="affix", epoch=generation
                )
            )
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK 输入闸门异常: {e}")
            return False
        if not ok:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK 输入闸门失败")
        return ok

    def _reset_runtime(self, context: str) -> bool:
        try:
            ok = bool(self.input_handler.reset_runtime())
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] {context}时原子复位 AHK 异常: {e}")
            return False
        if not ok:
            LOG_ERROR(f"[洗练管理器] {context}时原子复位 AHK 失败")
        return ok

    def _stop_run(
        self,
        run: _AffixRerollRun,
        reason: str,
        *,
        show_ui: bool = True,
        cleanup_runtime: bool = True,
    ) -> bool:
        """Stop exactly one generation; stale workers are strict no-ops."""
        with self._run_lock:
            if self._active_run is not run:
                return False

            run.stop_event.set()
            self._active_run = None
            gate_ok = True
            if self._gate_owner_generation == run.generation:
                self._gate_owner_generation = None
                # 状态对外变成 stopped 前先完成 AHK 原子关闸。
                if cleanup_runtime:
                    gate_ok = self._reset_runtime(reason)
            run.status.is_running = False
            self.status = run.status
            thread = run.thread

            # stopped/status/show_ui must be one generation-atomic publication.
            # Releasing the lock before these notifications lets a new run start
            # in between, after which the old worker can overwrite the new OSD
            # and reopen the main window.  A synchronous subscriber may re-enter
            # this manager (the lock is an RLock), so recheck before show_ui too.
            self._publish_status_update(run.status)
            if show_ui and self._active_run is None:
                event_bus.publish("affix_reroll:show_ui")

        LOG_INFO(f"[配置驱动洗練管理器] 停止洗练: {reason}")
        if (
            thread
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=self.WORKER_JOIN_TIMEOUT_SECONDS)
        return gate_ok

    def cleanup(self):
        """停止洗练并对称解除全局 EventBus 订阅。"""
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        try:
            self.stop_reroll("应用清理")
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] 清理时停止失败: {e}")
        finally:
            event_bus.unsubscribe("hotkey:affix_reroll_start", self._on_f7_pressed)
            event_bus.unsubscribe("engine:config_updated", self._on_config_updated)

    def _is_current_run(self, run: _AffixRerollRun) -> bool:
        with self._run_lock:
            return self._active_run is run and not run.stop_event.is_set()

    def _update_run_status(
        self, run: _AffixRerollRun, *, publish: bool = False, **changes
    ) -> bool:
        with self._run_lock:
            if self._active_run is not run or run.stop_event.is_set():
                return False
            for name, value in changes.items():
                setattr(run.status, name, value)
            self.status = run.status
            if publish:
                # 验证世代、写状态、发送通知必须在同一临界区完成；否则 stop/
                # restart 可插在写入和通知之间，让旧截图错误覆盖新一轮 OSD。
                self._publish_status_update(run.status)
            return True

    def _reroll_loop(self, run: _AffixRerollRun):
        succeeded = False
        try:
            while self._is_current_run(run):
                with self._run_lock:
                    if (
                        self._active_run is not run
                        or run.stop_event.is_set()
                        or run.status.current_attempts >= run.config.max_attempts
                    ):
                        break
                    run.status.current_attempts += 1
                    attempt = run.status.current_attempts
                LOG_INFO(f"[洗练循环] 第 {attempt} 次尝试")

                if self._process_reroll_cycle(run):
                    if not self._is_current_run(run):
                        break
                    LOG_INFO(f"[洗练循环] 找到目标词缀: {run.status.matched_affix}")
                    succeeded = self._notify_success(run)
                    break

                if run.stop_event.wait(run.config.click_delay / 1000.0):
                    break

            if (
                self._is_current_run(run)
                and run.status.current_attempts >= run.config.max_attempts
            ):
                LOG_INFO(f"[洗练循环] 达到最大尝试次数: {run.config.max_attempts}")
                self._update_run_status(run, error_message="达到最大尝试次数")

        except Exception as e:
            LOG_ERROR(f"[洗练循环] 异常: {e}")
            self._update_run_status(run, error_message=f"循环异常: {str(e)}")
        finally:
            # join 超时的旧 worker 只能尝试停止自己的 generation。
            self._stop_run(run, "循环结束", show_ui=not succeeded)

    def _process_reroll_cycle(self, run: _AffixRerollRun) -> bool:
        """
        根据新的状态机逻辑执行单次洗练循环。
        如果找到目标词缀则返回 True，否则返回 False。
        """
        if not self._is_current_run(run):
            return False

        screen_region = self._resolve_ocr_region()
        if screen_region is None:
            self._update_run_status(
                run, error_message="目标窗口不可截图", publish=True
            )
            return False

        frame = self.border_manager.capture_screen_for_reroll(
            region=screen_region
        )
        if not self._is_current_run(run):
            return False

        if frame is None:
            LOG_ERROR("[洗练循环] 获取屏幕截图失败")
            self._update_run_status(run, error_message="截图失败", publish=True)
            return False

        all_text = self.ocr_manager.get_text_from_image(frame)
        # OCR 是最可能超过 join 预算的阻塞点。返回后首先验证
        # generation，在此之前不能改状态或发点击。
        if not self._is_current_run(run):
            return False
        if not all_text:
            LOG_INFO("[洗练循环] OCR未识别到任何文本")
            return False

        full_text_str = "".join(all_text)

        # 状态 1: 初始洗练界面
        if "随机词缀" in full_text_str:
            if not self._update_run_status(run, current_state="初始界面"):
                return False
            LOG_INFO("[状态机] 检测到初始界面 (随机词缀)，点击附魔按钮")
            if not self._click_at(run, run.config.enchant_button_coord, "附魔按钮"):
                return False
            # 点击后等待200ms，并检查是否中断
            if run.stop_event.wait(0.2):
                return False
            return False

        # 状态 2: 替换后的确认弹窗
        elif "关闭" in full_text_str:
            if not self._update_run_status(run, current_state="确认/关闭"):
                return False
            LOG_INFO("[状态机] 检测到关闭按钮，点击关闭")
            if not self._click_at(run, run.config.close_button_coord, "关闭按钮"):
                return False
            # 点击后等待200ms，并检查是否中断
            if run.stop_event.wait(0.2):
                return False
            return False

        # 状态 3: 默认为词缀选择界面
        else:
            if not self._update_run_status(
                run, current_state="词缀选择", last_affixes=all_text
            ):
                return False
            LOG_INFO(f"[状态机] 检测到词缀选择界面，识别文本: {all_text}")

            # 检查是否有目标词缀
            for line in all_text:
                for target in run.config.target_affixes:
                    if target in line:
                        matched = f"{line} (匹配: {target})"
                        if not self._update_run_status(run, matched_affix=matched):
                            return False
                        LOG_INFO(f"[状态机] 找到目标词缀: {matched}")
                        return True  # 成功找到目标词缀

            # 未找到目标词缀，执行替换操作
            LOG_INFO("[状态机] 未命中目标，执行替换操作")
            if not self._click_at(run, run.config.first_affix_button_coord, "第一条词缀"):
                return False
            # 点击后等待200ms，并检查是否中断
            if run.stop_event.wait(0.2):
                return False
            if not self._click_at(run, run.config.replace_button_coord, "替换按钮"):
                return False
            # 点击后等待200ms，并检查是否中断
            if run.stop_event.wait(0.2):
                return False
            return False

    def _resolve_ocr_region(self) -> Optional[Tuple[int, int, int, int]]:
        """Resolve the target client's left OCR strip in desktop coordinates."""
        try:
            import win32gui
            from ..utils.window_utils import WindowUtils

            window_config = getattr(
                self.border_manager, "window_activation_config", {}
            ) or {}
            hwnd = WindowUtils.find_target_window(window_config)
            if not hwnd or not win32gui.IsWindow(hwnd):
                return None
            # ImageGrab sees the composed desktop, not an occluded background
            # window. Requiring foreground matches the AHK click safety gate.
            if win32gui.GetForegroundWindow() != hwnd:
                return None
            client_left, client_top, client_right, client_bottom = (
                win32gui.GetClientRect(hwnd)
            )
            left, top = win32gui.ClientToScreen(
                hwnd, (client_left, client_top)
            )
            right, bottom = win32gui.ClientToScreen(
                hwnd, (client_right, client_bottom)
            )
            right = min(int(right), int(left) + 500)
            region = (int(left), int(top), right, int(bottom))
            if region[2] <= region[0] or region[3] <= region[1]:
                return None
            return region
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] 解析目标 OCR 区域失败: {e}")
            return None

    def _click_at(
        self, run: _AffixRerollRun, coordinate, label: str
    ) -> bool:
        """Send one click while serializing against stop/new-generation open."""
        with self._run_lock:
            if self._active_run is not run or run.stop_event.is_set():
                return False
            if not coordinate:
                run.status.error_message = f"{label}坐标未配置"
                LOG_ERROR(f"[洗练循环] {run.status.error_message}")
                run.stop_event.set()
                ok = False
            else:
                try:
                    # 持锁直到有界发送返回。stop/new run 不能在“验证旧 run”
                    # 和“发点击”之间打开一个新闸门。
                    ok = bool(self.input_handler.click_mouse_at(*coordinate))
                except Exception as e:
                    LOG_ERROR(f"[洗练循环] {label}点击异常: {e}")
                    ok = False
                if not ok:
                    run.status.error_message = f"{label}点击发送失败"
                    LOG_ERROR(
                        f"[洗练循环] {run.status.error_message}，停止本轮洗练"
                    )
                    run.stop_event.set()
            self.status = run.status
            if not ok:
                # generation 验证、状态写入和发布必须同属一个 RLock 临界区。
                # 否则旧 worker 可在解锁后、发布前被 stop/new start 越过，
                # 再用旧 stopped/error 覆盖新一轮 OSD。
                self._publish_status_update(run.status)
            return ok

    def _notify_success(self, run: _AffixRerollRun) -> bool:
        # 通知也是 generation 副作用。持 RLock 发布并在两条事件之间
        # 再验证一次，同步订阅者即使停机也不会放出第二条旧事件。
        with self._run_lock:
            if self._active_run is not run or run.stop_event.is_set():
                return False
            self._publish_status_update(run.status)
            if self._active_run is not run or run.stop_event.is_set():
                return False
            event_bus.publish(
                "affix_reroll:success",
                {
                    "matched_affix": run.status.matched_affix,
                    "attempts": run.status.current_attempts,
                },
            )
            return True

    def _publish_status_update(
        self, status: Optional[SimpleAffixRerollStatus] = None
    ):
        status_data = dict((status or self.status).__dict__)
        event_bus.publish("affix_reroll:status_updated", status_data)
