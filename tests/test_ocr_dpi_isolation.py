#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OCR 隔离与 DPI 批次的回归测试。

覆盖三件事:
1. 帧快照所有权:get_current_frame 必须返回锁内复制的独立快照
   (C++ 零拷贝视图会被后续取帧原地覆写 → 跨线程持有必然撕裂/混帧);
   同一复用窗口内共享同一快照(同 tick 检测帧一致),stop 后快照作废。
2. DPI 逻辑→物理坐标换算:非 100% 缩放下鼠标逻辑坐标必须 × DPR
   才能对准 DXGI 物理帧,并夹紧到帧边界。
3. OCR 单例线程安全:PaddleOCRManager 双检锁单例 + full/rec 两把独立锁;
   Tesseract 单例创建加锁。
"""

import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import pytest
except ImportError:
    pytest = None

import numpy as np

from torchlight_assistant.gui.region_selection_dialog import logical_rect_to_physical


# ---------------------------------------------------------------------------
# 1. DPI 坐标换算(纯函数)
# ---------------------------------------------------------------------------

def test_dpr_identity_at_100_percent():
    assert logical_rect_to_physical(10, 20, 110, 220, 1.0, 1920, 1080) == (
        10, 20, 110, 220
    )


def test_dpr_scales_and_rounds():
    # 150% 缩放:逻辑 (100,100)-(200,200) → 物理 (150,150)-(300,300)
    assert logical_rect_to_physical(100, 100, 200, 200, 1.5, 2880, 1620) == (
        150, 150, 300, 300
    )
    # 125% 缩放的舍入:101*1.25=126.25 → 126;103*1.25=128.75 → 129
    assert logical_rect_to_physical(101, 0, 103, 0, 1.25, 2400, 1350)[0] == 126
    assert logical_rect_to_physical(101, 0, 103, 0, 1.25, 2400, 1350)[2] == 129


def test_dpr_uses_floor_ceil_not_round():
    """终点向上取整:消费者把 x2 当开区间端点用(frame[y1:y2, x1:x2]、width=x2-x1),
    四舍五入会让物理选区比用户拖出的范围少 dpr 个像素 —— 150% 下 8 逻辑像素的
    冷却图标缩成 10 物理像素(round 还叠加银行家舍入),检测方块比图标小一圈。"""
    px1, py1, px2, py2 = logical_rect_to_physical(100, 100, 107, 107, 1.5, 2880, 1620)
    assert (px1, px2) == (150, 161)   # round 会给 (150, 160)
    assert (py1, py2) == (150, 161)
    # 100% 缩放下与旧行为逐像素一致(存量配置不受影响)
    assert logical_rect_to_physical(100, 100, 107, 107, 1.0, 2880, 1620) == (
        100, 100, 107, 107
    )


def test_dpr_normalizes_reversed_and_clamps():
    # 反向拖拽自动归一;越界夹紧到物理帧边界
    assert logical_rect_to_physical(200, 220, 100, 120, 2.0, 350, 300) == (
        200, 240, 350, 300
    )


def test_dpr_zero_or_none_falls_back_to_1():
    assert logical_rect_to_physical(5, 6, 7, 8, 0, 100, 100) == (5, 6, 7, 8)
    assert logical_rect_to_physical(5, 6, 7, 8, None, 100, 100) == (5, 6, 7, 8)


# --- 取色对话框:逻辑坐标 → 物理像素采样 --------------------------------------

from torchlight_assistant.gui.color_picker_dialog import ColorPickingDialog


class _FakeImage:
    """记录被采样的物理坐标(不依赖真实 Qt 屏幕)。"""

    def __init__(self, w, h):
        self._w, self._h = w, h
        self.sampled = []

    def width(self):
        return self._w

    def height(self):
        return self._h

    def pixelColor(self, x, y):
        self.sampled.append((x, y))
        return ("color", x, y)


def _fake_picker(dpr, w=1920, h=1080):
    dlg = ColorPickingDialog.__new__(ColorPickingDialog)
    dlg._image = _FakeImage(w, h)
    dlg._dpr = dpr
    return dlg


def test_color_sampling_converts_logical_to_physical():
    """150% 缩放:点逻辑 (1200,600) 必须采物理 (1800,900),而非 (1200,600)。
    旧代码直接拿逻辑坐标索引物理图 → 取到目标点左上方 1/1.5 处的颜色,
    错误 HSV 会被写进 HP/MP 配置。"""
    dlg = _fake_picker(1.5)
    dlg._sample_logical(1200, 600)
    assert dlg._image.sampled == [(1800, 900)]


def test_color_sampling_identity_at_100_percent():
    dlg = _fake_picker(1.0)
    dlg._sample_logical(640, 480)
    assert dlg._image.sampled == [(640, 480)]


def test_color_sampling_clamps_to_image_bounds():
    """越界(放大镜边缘/屏幕边角)夹紧到有效像素,不抛异常也不留空白。"""
    dlg = _fake_picker(1.5)
    dlg._sample_logical(-10, -10)
    dlg._sample_logical(99999, 99999)
    assert dlg._image.sampled == [(0, 0), (1919, 1079)]


# --- 构造函数完整性(必须真正走 __init__,不能用 __new__ 绕过) -----------------

def _qt_app():
    """无头 Qt 应用。PySide6 不可用时返回 None。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    return QApplication.instance() or QApplication([])


def test_color_picker_real_constructor_wires_paint_attributes():
    """回归护栏:_sample_logical 曾被误插进 __init__ 中间,把 magnifier_size /
    zoom_factor / setMouseTracking / setFocusPolicy 挤到 return 之后变成死代码,
    对话框首次绘制即 AttributeError。

    上面那批用例走 __new__ 造假对象,天生绕过 __init__,所以完全没看见这个回归 ——
    这里必须真正构造一次并真正绘制一次。"""
    app = _qt_app()
    if app is None:
        print("SKIP: PySide6 不可用")
        return

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    dlg = ColorPickingDialog()
    try:
        assert hasattr(dlg, "magnifier_size"), "magnifier_size 未初始化(__init__ 被截断?)"
        assert hasattr(dlg, "zoom_factor"), "zoom_factor 未初始化(__init__ 被截断?)"
        assert dlg.magnifier_size > 0 and dlg.zoom_factor > 0
        assert dlg.hasMouseTracking(), "setMouseTracking 未生效"
        assert dlg.focusPolicy() == Qt.StrongFocus, "setFocusPolicy 未生效"
        assert hasattr(dlg, "_image") and hasattr(dlg, "_dpr")
        assert dlg._dpr > 0

        # 真正跑一次 paintEvent(render 提供有效绘制设备);缺字段会在这里炸
        dlg.resize(240, 180)
        dlg.render(QPixmap(240, 180))
    finally:
        dlg.close()
        dlg.deleteLater()


def test_color_picker_rejects_null_screenshot():
    """截图失败(锁屏/安全桌面/部分 RDP)时不得发出颜色:采样会夹紧到 (0,0) 拿到
    无效 QColor,getRgb() 给出 (0,0,0),一个纯黑 HSV 会被静默写进 HP/MP 配置。"""
    app = _qt_app()
    if app is None:
        print("SKIP: PySide6 不可用")
        return

    from PySide6.QtCore import Qt as _Qt, QPointF, QEvent
    from PySide6.QtGui import QImage, QMouseEvent

    dlg = ColorPickingDialog()
    try:
        dlg._image = QImage()  # 空截图
        got = []
        dlg.color_picked.connect(lambda r, g, b: got.append((r, g, b)))
        ev = QMouseEvent(
            QEvent.MouseButtonPress, QPointF(10, 10),
            _Qt.LeftButton, _Qt.LeftButton, _Qt.NoModifier,
        )
        dlg.mousePressEvent(ev)
        assert got == [], f"空截图仍发出了颜色 {got} —— 纯黑会被写进 HP/MP 配置"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_gui_dialogs_have_no_unreachable_code():
    """同类回归的静态护栏:函数体内 return 之后还有语句 = 死代码。
    (把新方法插进已有函数中间就是这个形态,单元测试不一定覆盖到那几行。)"""
    import ast

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    targets = [
        os.path.join(repo, "torchlight_assistant", "gui", "color_picker_dialog.py"),
        os.path.join(repo, "torchlight_assistant", "gui", "region_selection_dialog.py"),
    ]
    for path in targets:
        with open(path, "r", encoding="utf-8") as fp:
            tree = ast.parse(fp.read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for i, stmt in enumerate(node.body[:-1]):
                if isinstance(stmt, ast.Return):
                    raise AssertionError(
                        f"{os.path.basename(path)}:{node.body[i + 1].lineno} "
                        f"在 {node.name}() 的 return(第 {stmt.lineno} 行)之后仍有语句 —— 死代码"
                    )


# ---------------------------------------------------------------------------
# 2. 帧快照所有权(BorderFrameManager.get_current_frame)
# ---------------------------------------------------------------------------

from torchlight_assistant.utils.border_frame_manager import BorderFrameManager


class FakeZeroCopyCapture:
    """模拟 C++ 零拷贝捕获:get_latest_frame 返回**同一块**可变缓冲区的视图。"""

    def __init__(self, h=4, w=4):
        self.buffer = np.zeros((h, w, 4), dtype=np.uint8)
        self.calls = 0
        self.cleaned = False

    def get_latest_frame(self):
        self.calls += 1
        return self.buffer  # 视图语义:后续捕获会原地覆写

    def cleanup(self):
        self.cleaned = True

    def pause_capture(self):
        pass

    def resume_capture(self):
        pass


def _make_bfm(reuse_window=0.05):
    bfm = BorderFrameManager.__new__(BorderFrameManager)
    bfm._capture_lock = threading.RLock()
    bfm.running = True
    bfm.paused = False
    bfm.graphics_capture = FakeZeroCopyCapture()
    bfm._capture_config = None
    bfm._frame_snapshot = None
    bfm._frame_snapshot_at = 0.0
    bfm._snapshot_reuse_window = reuse_window
    return bfm


def test_snapshot_is_independent_copy():
    """返回的帧必须与 C++ 缓冲区解耦:缓冲区被覆写后快照不得变化。"""
    bfm = _make_bfm()
    fake = bfm.graphics_capture
    fake.buffer[:] = 7

    snap = bfm.get_current_frame()
    assert snap is not fake.buffer
    assert snap[0, 0, 0] == 7

    fake.buffer[:] = 99  # 模拟下一帧覆写 C++ 缓冲区
    assert snap[0, 0, 0] == 7, "快照被外部覆写污染 —— 不是独立副本"


def test_snapshot_shared_within_reuse_window_and_refreshed_after():
    """复用窗口内共享同一快照(不再触发取帧);窗口过后取新帧。"""
    bfm = _make_bfm(reuse_window=10.0)  # 大窗口:第二次调用必须复用
    fake = bfm.graphics_capture

    first = bfm.get_current_frame()
    second = bfm.get_current_frame()
    assert first is second, "复用窗口内应共享同一快照"
    assert fake.calls == 1, "复用窗口内不应再次触发 C++ 取帧"

    # 强制窗口过期 → 重新取帧复制
    bfm._frame_snapshot_at = 0.0
    fake.buffer[:] = 42
    third = bfm.get_current_frame()
    assert third is not first
    assert third[0, 0, 0] == 42
    assert fake.calls == 2


def test_snapshot_invalidated_on_stop():
    """stop 后快照作废:快速重启不得在复用窗口内拿到上个会话的旧帧。"""
    bfm = _make_bfm(reuse_window=10.0)
    assert bfm.get_current_frame() is not None
    bfm.stop()
    assert bfm._frame_snapshot is None
    assert bfm.running is False
    assert bfm.get_current_frame() is None  # 未运行 → None,而非旧快照


def test_not_running_returns_none():
    bfm = _make_bfm()
    bfm.running = False
    assert bfm.get_current_frame() is None


def test_paused_returns_none_not_stale_snapshot():
    """PAUSED = 完全停下。底层已不再产出新帧,若只看 running,
    复用窗口内的在途检测会继续拿到**暂停前**的旧帧去做判定(如误判血量低而喝药)。"""
    bfm = _make_bfm(reuse_window=10.0)
    assert bfm.get_current_frame() is not None
    bfm.paused = True
    assert bfm.get_current_frame() is None


def test_pause_capture_invalidates_snapshot():
    """暂停时快照即作废:恢复后第一次取帧必须是新画面,不能命中暂停前的复用窗口。"""
    bfm = _make_bfm(reuse_window=10.0)
    fake = bfm.graphics_capture
    fake.buffer[:] = 7
    first = bfm.get_current_frame()
    assert first[0, 0, 0] == 7

    bfm.pause_capture()
    assert bfm._frame_snapshot is None
    assert bfm._frame_snapshot_at == 0.0

    bfm.resume_capture()
    fake.buffer[:] = 55  # 暂停期间画面已变
    again = bfm.get_current_frame()
    assert again is not first
    assert again[0, 0, 0] == 55, "恢复后仍命中暂停前的快照"


def test_reuse_window_follows_configured_interval():
    """复用窗口必须跟随本次实际生效的捕获间隔(用户配置只经 interval_ms 传入,
    self.capture_interval 是构造默认值,不同步会让窗口永远停在默认值)。"""
    bfm = _make_bfm(reuse_window=0.02)
    bfm._get_target_window_handle = lambda: 12345
    bfm._capture_config = None

    class _FakeMgr:
        def __init__(self, cfg):
            pass

        def start_capture(self):
            return True

        def cleanup(self):
            pass

    import torchlight_assistant.utils.border_frame_manager as bfm_mod
    from unittest.mock import patch

    # start_capture_loop 内部才 import NativeGraphicsCaptureManager,patch 其来源模块
    import torchlight_assistant.utils.native_graphics_capture_manager as ngcm
    with patch.object(ngcm, "NativeGraphicsCaptureManager", _FakeMgr):
        bfm.running = False
        bfm.start_capture_loop(interval_ms=100)

    assert bfm.capture_interval == 0.1
    assert bfm._snapshot_reuse_window == 0.05  # 100ms/2

    # 极小间隔:窗口不得**超过**捕获间隔本身,否则用户把间隔调小反而拿到更旧的帧
    with patch.object(ngcm, "NativeGraphicsCaptureManager", _FakeMgr):
        bfm.running = False
        bfm.start_capture_loop(interval_ms=2)
    assert bfm._snapshot_reuse_window == 0.002


def test_reuse_window_never_exceeds_capture_interval():
    """窗口 = min(max(10ms, 间隔/2), 间隔):10ms 下限不得越过间隔上限。"""
    f = BorderFrameManager._compute_reuse_window
    assert f(0.040) == 0.020          # 常规:间隔一半
    assert f(0.030) == 0.015
    assert f(0.016) == 0.010          # 8ms 被 10ms 下限抬高,仍未超过间隔
    assert f(0.005) == 0.005          # 5ms:下限会越过间隔,必须被上限压回
    assert f(0.002) == 0.002          # 窗口不得跨越 5 个捕获周期
    assert f(0) == 0.010              # 无间隔信息 → 退回下限
    assert f(None) == 0.010


def test_snapshot_is_read_only():
    """快照在窗口内被多个消费者共享,必须只读:任何一方原地改写都会污染
    别人的检测输入。设成只读让违约当场报错,而不是变成一次静默误判。"""
    bfm = _make_bfm(reuse_window=10.0)
    snap = bfm.get_current_frame()
    assert snap.flags.writeable is False
    try:
        snap[0, 0, 0] = 1
    except ValueError:
        pass
    else:
        raise AssertionError("快照可写 —— 共享只读契约没有被强制")
    # 消费者需要可写数组时自行 copy(),copy 必须是可写的
    assert snap.copy().flags.writeable is True


# ---------------------------------------------------------------------------
# 3. OCR 单例线程安全(paddle 依赖较重,不可用时跳过)
# ---------------------------------------------------------------------------

try:
    from torchlight_assistant.utils.paddle_ocr_manager import (
        PaddleOCRManager,
        get_paddle_ocr_manager,
    )

    _PADDLE_ERR = None
except Exception as e:  # paddleocr 未安装/导入失败
    PaddleOCRManager = None
    _PADDLE_ERR = e


def test_paddle_singleton_is_race_free():
    if PaddleOCRManager is None:
        print(f"SKIP: paddleocr 不可用: {_PADDLE_ERR}")
        return
    results = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        results.append(get_paddle_ocr_manager())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(r) for r in results}) == 1, "并发首次获取产生了多个实例"
    mgr = results[0]
    # 实例必须已完成 _init_internal(锁存在)
    assert hasattr(mgr, "_full_lock") and hasattr(mgr, "_rec_lock")


def test_paddle_full_and_rec_locks_are_independent():
    """rec-only(运行时热路径)不得与 full OCR(配置期,秒级)共锁 ——
    否则 GUI 校准会把调度线程的 HP/MP 资源检测堵住数秒。"""
    if PaddleOCRManager is None:
        print(f"SKIP: paddleocr 不可用: {_PADDLE_ERR}")
        return
    mgr = get_paddle_ocr_manager()
    assert mgr._full_lock is not mgr._rec_lock

    # 持有 full 锁时,rec 锁必须仍可立即获取(非阻塞验证)
    with mgr._full_lock:
        acquired = mgr._rec_lock.acquire(timeout=0.5)
        assert acquired, "持有 full 锁时 rec 锁被阻塞 —— 锁未真正拆分"
        mgr._rec_lock.release()


class _FakeRec:
    """假的 TextRecognition:记录构建次数,predict 返回固定合法读数。"""

    built = []

    def __init__(self, model_name=None, device=None):
        _FakeRec.built.append((model_name, device))
        self.model_name = model_name

    def predict(self, roi):
        return [{"rec_text": "10/20", "rec_score": 0.99}]


def _with_fake_rec(mgr):
    """替换 paddleocr.TextRecognition 并清空 rec 缓存;返回 (patcher, 还原函数)。"""
    import paddleocr
    from unittest.mock import patch

    _FakeRec.built = []
    saved = mgr._rec_models
    mgr._rec_models = {}

    def restore():
        mgr._rec_models = saved

    return patch.object(paddleocr, "TextRecognition", _FakeRec), restore


def test_rec_models_cached_per_key_without_eviction():
    """HP/MP 可以配成不同模型,GUI 测试按钮也可能用另一套参数。单槽实现下
    这些调用互相驱逐 —— 每个 200ms tick 都要重建一次模型(秒级),资源检测停摆。"""
    if PaddleOCRManager is None:
        print(f"SKIP: paddleocr 不可用: {_PADDLE_ERR}")
        return
    mgr = get_paddle_ocr_manager()
    patcher, restore = _with_fake_rec(mgr)
    try:
        with patcher:
            a1 = mgr._get_rec_model("model_A", "cpu")
            b1 = mgr._get_rec_model("model_B", "cpu")
            a2 = mgr._get_rec_model("model_A", "cpu")   # B 用过之后 A 必须还在
            b2 = mgr._get_rec_model("model_B", "cpu")
        assert a1 is a2 and b1 is b2, "槽位被另一个 key 驱逐了"
        assert a1 is not b1
        assert _FakeRec.built == [("model_A", "cpu"), ("model_B", "cpu")], (
            f"发生了重复构建: {_FakeRec.built}"
        )
    finally:
        restore()


def test_recognize_uses_local_model_reference():
    """ensure→predict 之间放锁会留下 TOCTOU:另一线程构建失败把 rec_model 置 None,
    调度线程随后 AttributeError,被吞掉后资源检测兜底成'充足' —— 该喝的药不喝。
    predict 必须用局部引用,不回头读字段。"""
    if PaddleOCRManager is None:
        print(f"SKIP: paddleocr 不可用: {_PADDLE_ERR}")
        return
    mgr = get_paddle_ocr_manager()
    patcher, restore = _with_fake_rec(mgr)
    roi = np.zeros((8, 16, 4), dtype=np.uint8)
    try:
        with patcher:
            assert mgr._get_rec_model("model_A", "cpu") is not None
            mgr.rec_model = None  # 模拟并发失败清空字段
            cur, mx, pct = mgr.recognize_and_parse(roi, "model_A", "cpu", min_score=0.5)
        assert (cur, mx) == (10, 20), f"读数丢失({cur},{mx}) —— predict 仍在读共享字段"
        assert abs(pct - 50.0) < 1e-6
    finally:
        restore()


def test_ensure_writable_copies_readonly_input():
    """帧快照是只读共享的,第三方推理库可能对输入做原地预处理 —— 递进去之前
    必须按需复制,否则要么报错,要么污染其他消费者的检测输入。"""
    if PaddleOCRManager is None:
        print(f"SKIP: paddleocr 不可用: {_PADDLE_ERR}")
        return
    ro = np.zeros((4, 4, 3), dtype=np.uint8)
    ro.setflags(write=False)
    out = PaddleOCRManager._ensure_writable(ro)
    assert out is not ro and out.flags.writeable is True

    rw = np.zeros((4, 4, 3), dtype=np.uint8)
    assert PaddleOCRManager._ensure_writable(rw) is rw  # 可写输入不复制

    # BGRA 只读 ROI 走完整规范化后同样必须可写
    bgra = np.zeros((4, 4, 4), dtype=np.uint8)
    bgra.setflags(write=False)
    assert PaddleOCRManager._normalize_image(bgra).flags.writeable is True


def test_tesseract_singleton_is_race_free():
    try:
        from torchlight_assistant.utils.tesseract_ocr_manager import (
            get_tesseract_ocr_manager,
            reset_tesseract_ocr_manager,
        )
    except Exception as e:
        print(f"SKIP: pytesseract 不可用: {e}")
        return

    reset_tesseract_ocr_manager()
    results = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        results.append(get_tesseract_ocr_manager({}))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(r) for r in results}) == 1, "并发首次获取产生了多个实例"


if __name__ == "__main__":
    tests = sorted(
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    )
    for name, fn in tests:
        fn()
        print("PASS", name)
    print(f"ALL {len(tests)} PASSED")
