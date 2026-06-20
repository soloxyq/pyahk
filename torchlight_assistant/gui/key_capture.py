#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用按键录制 Mixin —— pynput 一次性捕获键盘/鼠标按键,UI 无关。

调用方提供回调,本 Mixin 负责监听器生命周期与按键名解析:捕获到第一个键即回调并停止。
键名返回小写普通键 / 标准鼠标键名(LButton/RButton/MButton),与 key_names 归一化兼容。

线程安全:pynput 监听运行在自己的工作线程,捕获结果经 QObject 信号(队列连接)切回 GUI
线程后再触发回调,避免在非 GUI 线程操作 Qt 控件(Codex 复审)。无 pynput 时优雅降级
(capture_key_once 返回 False)。
"""

from PySide6.QtCore import QTimer, QObject, Signal

from ..utils.debug_log import LOG_ERROR

try:
    from pynput import keyboard, mouse
    from pynput.mouse import Button

    PYNPUT_AVAILABLE = True
except ImportError:  # pragma: no cover - 环境相关
    PYNPUT_AVAILABLE = False


class _CaptureBridge(QObject):
    """把 pynput 工作线程的结果以队列连接切回 GUI 线程。"""

    captured = Signal(str)
    timed_out = Signal()


class KeyCaptureMixin:
    """为 QWidget 子类提供 capture_key_once();与具体 UI 解耦,通过回调交互。"""

    _kc_listening = False
    _kc_keyboard_listener = None
    _kc_mouse_listener = None
    _kc_bridge = None

    def capture_key_once(self, on_captured, timeout_ms: int = 5000, on_timeout=None) -> bool:
        """开始一次性监听。捕获到键盘/鼠标按键 → on_captured(key_name:str) 并自动停止。

        必须在 GUI 线程调用(通常来自按钮点击)。返回 True 表示成功启动;False 表示 pynput
        不可用或已在监听中。超时(默认 5s)未捕获则停止并调用 on_timeout(若提供)。
        """
        if not PYNPUT_AVAILABLE or self._kc_listening:
            return False
        # 在 GUI 线程创建 bridge,跨线程 emit 会自动走队列连接,槽在 GUI 线程执行
        bridge = _CaptureBridge()
        bridge.captured.connect(on_captured)
        if on_timeout is not None:
            bridge.timed_out.connect(on_timeout)
        self._kc_bridge = bridge
        self._kc_listening = True
        try:
            self._kc_keyboard_listener = keyboard.Listener(on_press=self._kc_on_key, suppress=False)
            self._kc_mouse_listener = mouse.Listener(on_click=self._kc_on_mouse, suppress=False)
            self._kc_keyboard_listener.start()
            self._kc_mouse_listener.start()
            QTimer.singleShot(timeout_ms, self._kc_timeout)  # GUI 线程,安全
            return True
        except Exception as e:
            LOG_ERROR(f"[按键录制] 启动失败: {e}")
            self.cancel_capture()
            return False

    def cancel_capture(self):
        """停止监听并清理监听器(不触发回调)。可从任意线程调用。"""
        if not self._kc_listening:
            return
        self._kc_listening = False
        try:
            if self._kc_keyboard_listener:
                self._kc_keyboard_listener.stop()
            if self._kc_mouse_listener:
                self._kc_mouse_listener.stop()
        except Exception as e:
            LOG_ERROR(f"[按键录制] 停止失败: {e}")
        finally:
            self._kc_keyboard_listener = None
            self._kc_mouse_listener = None
            self._kc_bridge = None

    def _kc_timeout(self):
        # 由 QTimer 在 GUI 线程触发
        if not self._kc_listening:
            return
        bridge = self._kc_bridge
        self.cancel_capture()
        if bridge is not None:
            bridge.timed_out.emit()

    def _kc_on_key(self, key):  # pynput 线程
        if not self._kc_listening:
            return
        name = self._pynput_key_name(key)
        if name:
            self._kc_emit(name)

    def _kc_on_mouse(self, x, y, button, pressed):  # pynput 线程
        if not self._kc_listening or not pressed:
            return
        name = self._pynput_button_name(button)
        if name:
            self._kc_emit(name)

    def _kc_emit(self, name: str):  # pynput 线程
        bridge = self._kc_bridge
        self.cancel_capture()  # 先停监听(置空 _kc_bridge),本地仍持 bridge 引用
        if bridge is not None:
            bridge.captured.emit(name)  # 线程安全;跨线程队列连接 → 槽在 GUI 线程执行

    @staticmethod
    def _pynput_key_name(key) -> str:
        try:
            if hasattr(key, "char") and key.char:
                return key.char.lower()
            if hasattr(key, "name"):
                mapping = {
                    "space": "space",
                    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
                    "shift_l": "shift", "shift_r": "shift",
                    "alt_l": "alt", "alt_r": "alt",
                    "tab": "tab", "esc": "esc", "enter": "enter",
                    "backspace": "backspace", "delete": "delete",
                }
                return mapping.get(key.name, key.name or "")
        except Exception as e:
            LOG_ERROR(f"[按键录制] 解析键名失败: {e}")
        return ""

    @staticmethod
    def _pynput_button_name(button) -> str:
        # 仅在 pynput 可用(监听已启动)时才会被调用,Button 必定已定义
        try:
            return {Button.left: "LButton", Button.right: "RButton", Button.middle: "MButton"}.get(
                button, ""
            )
        except Exception:
            return ""
