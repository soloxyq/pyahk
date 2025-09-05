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


class SimpleAffixRerollManager:
    """配置驱动的智能洗练管理器"""

    def __init__(self, border_manager, input_handler):
        self.border_manager = border_manager
        self.input_handler = input_handler
        self.config = SimpleAffixRerollConfig()
        self.status = SimpleAffixRerollStatus()
        self.ocr_manager = None  # Lazy-loaded OCR manager
        self._reroll_thread = None
        self._stop_event = threading.Event()
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
        if self.status.is_running:
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

        self.status = SimpleAffixRerollStatus(is_running=True)
        self._stop_event.clear()
        self._reroll_thread = threading.Thread(target=self._reroll_loop, daemon=True)
        self._reroll_thread.start()
        LOG_INFO(f"[配置驱动洗練管理器] 开始洗练，目标: {self.config.target_affixes}")
        self._publish_status_update()
        event_bus.publish("affix_reroll:hide_ui")
        return True

    def stop_reroll(self, reason: str = "用户手动停止"):
        if not self.status.is_running:
            return
        LOG_INFO(f"[配置驱动洗練管理器] 停止洗练: {reason}")
        self._stop_event.set()
        self.status.is_running = False
        if (
            self._reroll_thread
            and self._reroll_thread.is_alive()
            and threading.current_thread() != self._reroll_thread
        ):
            self._reroll_thread.join(timeout=2.0)
        self._publish_status_update()
        event_bus.publish("affix_reroll:show_ui")

    def _reroll_loop(self):
        try:
            while (
                not self._stop_event.is_set()
                and self.status.current_attempts < self.config.max_attempts
            ):
                self.status.current_attempts += 1
                LOG_INFO(f"[洗练循环] 第 {self.status.current_attempts} 次尝试")

                if self._process_reroll_cycle():
                    LOG_INFO(f"[洗练循环] 找到目标词缀: {self.status.matched_affix}")
                    self._notify_success()
                    break

                if self._stop_event.wait(self.config.click_delay / 1000.0):
                    break

            if self.status.current_attempts >= self.config.max_attempts:
                LOG_INFO(f"[洗练循环] 达到最大尝试次数: {self.config.max_attempts}")
                self.status.error_message = f"达到最大尝试次数"

        except Exception as e:
            LOG_ERROR(f"[洗练循环] 异常: {e}")
            self.status.error_message = f"循环异常: {str(e)}"
        finally:
            self.stop_reroll("循环结束")

    def _process_reroll_cycle(self) -> bool:
        """
        根据新的状态机逻辑执行单次洗练循环。
        如果找到目标词缀则返回 True，否则返回 False。
        """
        # 获取截图区域（只截取屏幕左边500像素宽度）
        if self._screen_region is None:
            try:
                from PIL import ImageGrab

                # 获取屏幕尺寸
                screen_size = ImageGrab.grab().size
                screen_width, screen_height = screen_size
                # region格式: (left, top, right, bottom)
                self._screen_region = (0, 0, 500, screen_height)
                LOG_INFO(
                    f"[洗练管理器] 初始化截图区域: {self._screen_region} (屏幕尺寸: {screen_size})"
                )
            except Exception as e:
                LOG_ERROR(f"[洗练管理器] 获取屏幕尺寸失败，使用默认区域: {e}")
                self._screen_region = (0, 0, 500, 1080)  # 默认区域

        frame = self.border_manager.capture_screen_for_reroll(
            region=self._screen_region
        )
        if frame is None:
            LOG_ERROR("[洗练循环] 获取屏幕截图失败")
            self.status.error_message = "截图失败"
            self._publish_status_update()
            return False

        all_text = self.ocr_manager.get_text_from_image(frame)
        if not all_text:
            LOG_INFO("[洗练循环] OCR未识别到任何文本")
            return False

        full_text_str = "".join(all_text)

        # 状态 1: 初始洗练界面
        if "随机词缀" in full_text_str:
            self.status.current_state = "初始界面"
            LOG_INFO("[状态机] 检测到初始界面 (随机词缀)，点击附魔按钮")
            self.input_handler.click_mouse_at(*self.config.enchant_button_coord)
            # 点击后等待200ms，并检查是否中断
            if self._stop_event.wait(0.2):
                return False
            return False

        # 状态 2: 替换后的确认弹窗
        elif "关闭" in full_text_str:
            self.status.current_state = "确认/关闭"
            LOG_INFO("[状态机] 检测到关闭按钮，点击关闭")
            self.input_handler.click_mouse_at(*self.config.close_button_coord)
            # 点击后等待200ms，并检查是否中断
            if self._stop_event.wait(0.2):
                return False
            return False

        # 状态 3: 默认为词缀选择界面
        else:
            self.status.current_state = "词缀选择"
            self.status.last_affixes = all_text
            LOG_INFO(f"[状态机] 检测到词缀选择界面，识别文本: {all_text}")

            # 检查是否有目标词缀
            for line in all_text:
                for target in self.config.target_affixes:
                    if target in line:
                        self.status.matched_affix = f"{line} (匹配: {target})"
                        LOG_INFO(f"[状态机] 找到目标词缀: {self.status.matched_affix}")
                        return True  # 成功找到目标词缀

            # 未找到目标词缀，执行替换操作
            LOG_INFO("[状态机] 未命中目标，执行替换操作")
            self.input_handler.click_mouse_at(*self.config.first_affix_button_coord)
            # 点击后等待200ms，并检查是否中断
            if self._stop_event.wait(0.2):
                return False
            self.input_handler.click_mouse_at(*self.config.replace_button_coord)
            # 点击后等待200ms，并检查是否中断
            if self._stop_event.wait(0.2):
                return False
            return False

    def _notify_success(self):
        self.status.is_running = False
        self._publish_status_update()
        # 发布成功事件，这会触发弹框显示
        event_bus.publish(
            "affix_reroll:success",
            {
                "matched_affix": self.status.matched_affix,
                "attempts": self.status.current_attempts,
            },
        )
        # 显示主界面（成功事件处理器会处理这个）
        # 注意：不需要在这里发布show_ui事件，因为success事件处理器会处理界面显示

    def _publish_status_update(self):
        status_data = self.status.__dict__
        event_bus.publish("affix_reroll:status_updated", status_data)
