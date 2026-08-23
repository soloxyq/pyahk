"""智能装备词缀自动洗练管理器 - 基于配置驱动和区域OCR"""

import threading
import time
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from .event_bus import event_bus
from ..utils.debug_log import LOG_INFO, LOG_ERROR


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
        return cls(
            enabled=data.get("enabled", False),
            target_affixes=data.get("target_affixes", []),
            max_attempts=data.get("max_attempts", 100),
            click_delay=data.get("click_delay", 200),
            enchant_button_coord=(
                tuple(data.get("enchant_button_coord"))
                if data.get("enchant_button_coord")
                else None
            ),
            first_affix_button_coord=(
                tuple(data.get("first_affix_button_coord"))
                if data.get("first_affix_button_coord")
                else None
            ),
            replace_button_coord=(
                tuple(data.get("replace_button_coord"))
                if data.get("replace_button_coord")
                else None
            ),
            close_button_coord=(
                tuple(data.get("close_button_coord"))
                if data.get("close_button_coord")
                else None
            ),
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
        self._screen_region = None  # 缓存截图区域
        event_bus.subscribe("hotkey:affix_reroll_start", self._on_f7_pressed)
        event_bus.subscribe("engine:config_updated", self._on_config_updated)
        LOG_INFO("[配置驱动洗練管理器] 初始化完成")

    def _on_f7_pressed(self):
        if self.status.is_running:
            self.stop_reroll("用户按F7停止")
        else:
            self.start_reroll()

    def _on_config_updated(
        self, skills_config: Dict[str, Any], global_config: Dict[str, Any]
    ):
        config_data = global_config.get("affix_reroll", {})
        LOG_INFO(f"[洗练管理器] 接收到配置更新: {config_data}")
        self.config = SimpleAffixRerollConfig.from_dict(config_data)
        LOG_INFO(
            f"[洗练管理器] 配置更新完成: 启用={self.config.enabled}, 目标词缀={self.config.target_affixes}"
        )

    def start_reroll(self) -> bool:
        with self._run_lock:
            if self._active_run is not None or self.status.is_running:
                return False
        if (
            not self.config.enabled
            or not self.config.target_affixes
            or not self.config.enchant_button_coord
        ):
            LOG_ERROR("[配置驱动洗練管理器] 配置无效或不完整，无法启动")
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
                self._run_generation += 1
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
                if not self._set_runtime_gate(True, "启动"):
                    run.status.is_running = False
                    run.status.error_message = "AHK输入闸门打开失败"
                    self.status = run.status
                    # timeout 不代表 true 没执行；必须用 force 方向的 false
                    # 再做一次有界回滚，不能留下无 owner 的开闸。
                    self._set_runtime_gate(False, "启动失败回滚")
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
                    self._set_runtime_gate(False, "启动回滚")
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

    def stop_reroll(self, reason: str = "用户手动停止"):
        with self._run_lock:
            run = self._active_run
        if run is not None:
            self._stop_run(run, reason)

    def _set_runtime_gate(self, enabled: bool, context: str) -> bool:
        try:
            ok = bool(self.input_handler.set_accepting_actions(enabled))
        except Exception as e:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK 输入闸门异常: {e}")
            return False
        if not ok:
            LOG_ERROR(f"[洗练管理器] {context}时设置 AHK 输入闸门失败")
        return ok

    def _stop_run(
        self, run: _AffixRerollRun, reason: str, *, show_ui: bool = True
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
                gate_ok = self._set_runtime_gate(False, reason)
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

    def _update_run_status(self, run: _AffixRerollRun, **changes) -> bool:
        with self._run_lock:
            if self._active_run is not run or run.stop_event.is_set():
                return False
            for name, value in changes.items():
                setattr(run.status, name, value)
            self.status = run.status
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

        # 获取截图区域（只截取屏幕左边500像素宽度）
        if self._screen_region is None:
            try:
                from PIL import ImageGrab

                # 获取屏幕尺寸
                screen_size = ImageGrab.grab().size
                screen_width, screen_height = screen_size
                # region格式: (left, top, right, bottom)
                screen_region = (0, 0, 500, screen_height)
                LOG_INFO(
                    f"[洗练管理器] 初始化截图区域: {screen_region} (屏幕尺寸: {screen_size})"
                )
            except Exception as e:
                LOG_ERROR(f"[洗练管理器] 获取屏幕尺寸失败，使用默认区域: {e}")
                screen_region = (0, 0, 500, 1080)  # 默认区域
            with self._run_lock:
                if self._active_run is not run or run.stop_event.is_set():
                    return False
                if self._screen_region is None:
                    self._screen_region = screen_region

        frame = self.border_manager.capture_screen_for_reroll(
            region=self._screen_region
        )
        if not self._is_current_run(run):
            return False
        if frame is None:
            LOG_ERROR("[洗练循环] 获取屏幕截图失败")
            if self._update_run_status(run, error_message="截图失败"):
                self._publish_status_update(run.status)
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

    def _click_at(
        self, run: _AffixRerollRun, coordinate, label: str
    ) -> bool:
        """Send one click while serializing against stop/new-generation open."""
        publish_error = False
        with self._run_lock:
            if self._active_run is not run or run.stop_event.is_set():
                return False
            if not coordinate:
                run.status.error_message = f"{label}坐标未配置"
                LOG_ERROR(f"[洗练循环] {run.status.error_message}")
                run.stop_event.set()
                publish_error = True
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
                    publish_error = True
            self.status = run.status

        if publish_error:
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
