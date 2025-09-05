"""
Window management utilities
"""

import time
from typing import Optional, Tuple, Dict
from .debug_log import LOG, LOG_ERROR, LOG_INFO

try:
    import psutil
    import win32gui
    import win32process
    import win32con
except ImportError:
    psutil = None
    win32gui = None
    LOG_ERROR(
        "Windows-specific modules not available - window detection will be limited"
    )


class WindowUtils:
    """Utilities for window detection and management"""
    
    # 性能优化：缓存常用的窗口查找结果
    _window_cache: Dict[str, Tuple[int, float]] = {}  # key -> (hwnd, timestamp)
    _cache_timeout = 2.0  # 缓存2秒
    _process_cache: Dict[str, Tuple[bool, float]] = {}  # 进程缓存
    _process_cache_timeout = 5.0  # 进程缓存5秒

    @staticmethod
    def find_window_by_title(title: str) -> Optional[int]:
        """Find window handle by title（优化版本）"""
        if not win32gui:
            return None

        # 检查缓存
        cache_key = f"title_{title.lower()}"
        if cache_key in WindowUtils._window_cache:
            hwnd, timestamp = WindowUtils._window_cache[cache_key]
            if time.time() - timestamp < WindowUtils._cache_timeout:
                try:
                    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                        return hwnd
                except:
                    pass
                del WindowUtils._window_cache[cache_key]

        try:
            hwnd = win32gui.FindWindow(None, title)
            if hwnd:
                # 更新缓存
                WindowUtils._window_cache[cache_key] = (hwnd, time.time())
            return hwnd
        except Exception:
            return None

    @staticmethod
    def find_window_by_class(class_name: str) -> Optional[int]:
        """Find window handle by class name（优化版本）"""
        if not win32gui:
            return None

        # 检查缓存
        cache_key = f"class_{class_name.lower()}"
        if cache_key in WindowUtils._window_cache:
            hwnd, timestamp = WindowUtils._window_cache[cache_key]
            if time.time() - timestamp < WindowUtils._cache_timeout:
                try:
                    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                        return hwnd
                except:
                    pass
                del WindowUtils._window_cache[cache_key]

        try:
            hwnd = win32gui.FindWindow(class_name, None)
            if hwnd:
                # 更新缓存
                WindowUtils._window_cache[cache_key] = (hwnd, time.time())
            return hwnd
        except Exception:
            return None

    @staticmethod
    def is_window_active(window_title: str) -> bool:
        """Check if a window with given title is active"""
        if not win32gui:
            return False

        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                window_text = win32gui.GetWindowText(hwnd)
                return window_title.lower() in window_text.lower()
        except Exception as e:
            LOG(f"WARNING: 检查活动窗口 '{window_title}' 时出错: {e}")

        return False

    @staticmethod
    def is_process_running(process_name: str) -> bool:
        """Check if a process is running（优化版本，带缓存）"""
        if not psutil:
            return False
        
        # 检查缓存
        cache_key = process_name.lower()
        if cache_key in WindowUtils._process_cache:
            is_running, timestamp = WindowUtils._process_cache[cache_key]
            if time.time() - timestamp < WindowUtils._process_cache_timeout:
                return is_running

        try:
            is_running = False
            for proc in psutil.process_iter(["name"]):
                if proc.info["name"].lower() == process_name.lower():
                    is_running = True
                    break
            
            # 更新缓存
            WindowUtils._process_cache[cache_key] = (is_running, time.time())
            return is_running
        except Exception as e:
            LOG(f"WARNING: 检查进程 '{process_name}' 是否正在运行时出错: {e}")
            return False

    @staticmethod
    def get_running_processes() -> list:
        """获取当前运行的所有进程名列表"""
        if not psutil:
            return []

        try:
            processes = set()
            for proc in psutil.process_iter(["name"]):
                try:
                    name = proc.info["name"]
                    if name and name.endswith(".exe"):
                        processes.add(name)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return sorted(list(processes))
        except Exception:
            return []

    @staticmethod
    def get_window_class_by_process(process_name: str) -> str:
        """根据进程名获取对应的窗口类名"""
        if not win32gui or not psutil:
            return ""

        try:
            # 遍历所有窗口，找到对应进程的窗口类名
            def enum_windows_proc(hwnd, lParam):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        # 获取窗口对应的进程ID
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        # 获取进程信息
                        process = psutil.Process(pid)
                        if process.name().lower() == process_name.lower():
                            # 获取窗口类名
                            class_name = win32gui.GetClassName(hwnd)
                            if class_name:
                                lParam.append(class_name)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                return True

            class_names = []
            win32gui.EnumWindows(enum_windows_proc, class_names)
            # 返回第一个找到的类名
            return class_names[0] if class_names else ""
        except Exception:
            return ""

    @staticmethod
    def get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        """Get window rectangle (left, top, right, bottom)"""
        if not win32gui or not hwnd:
            return None

        try:
            return win32gui.GetWindowRect(hwnd)
        except Exception:
            return None

    @staticmethod
    def set_window_topmost(hwnd: int, topmost: bool = True):
        """Set window to be topmost or not"""
        if not win32gui or not hwnd:
            return False

        try:
            flag = win32con.HWND_TOPMOST if topmost else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(
                hwnd, flag, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
            )
            return True
        except Exception:
            return False

    @staticmethod
    def wait_for_window(window_title: str, timeout: float = 10.0) -> bool:
        """Wait for a window to appear"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if WindowUtils.is_window_active(window_title):
                return True
            time.sleep(0.1)

        return False

    @staticmethod
    def get_active_window_title() -> str:
        """Get title of currently active window"""
        if not win32gui:
            return ""

        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                return win32gui.GetWindowText(hwnd)
        except Exception:
            pass

        return ""

    @staticmethod
    def minimize_window(hwnd: int) -> bool:
        """Minimize a window"""
        if not win32gui or not hwnd:
            return False

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        except Exception:
            return False

    @staticmethod
    def restore_window(hwnd: int) -> bool:
        """Restore a minimized window"""
        if not win32gui or not hwnd:
            return False

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return True
        except Exception:
            return False

    @staticmethod
    def clear_cache():
        """清理缓存（在需要强制刷新时调用）"""
        WindowUtils._window_cache.clear()
        WindowUtils._process_cache.clear()
        LOG("[窗口工具] 缓存已清理")

    @staticmethod
    def find_window_by_process_name(process_name: str) -> Optional[int]:
        """Find window handle by process name (exe file name)"""
        if not win32gui or not psutil:
            return None

        try:
            # 遍历所有窗口
            def enum_windows_proc(hwnd, lParam):
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        # 获取窗口对应的进程ID
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        # 获取进程信息
                        process = psutil.Process(pid)
                        if process.name().lower() == process_name.lower():
                            lParam.append(hwnd)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                return True

            windows = []
            win32gui.EnumWindows(enum_windows_proc, windows)
            return windows[0] if windows else None
        except Exception:
            return None

    @staticmethod
    def find_windows_by_criteria(
        ahk_class: str = None, ahk_exe: str = None, window_title: str = None
    ) -> list:
        """Find windows by multiple criteria (similar to AutoHotkey)

        Args:
            ahk_class: Window class name (e.g., 'Qt5156QWindowIcon')
            ahk_exe: Process executable name (e.g., 'MuMuNxDevice.exe')
            window_title: Window title to match

        Returns:
            List of window handles that match the criteria
        """
        if not win32gui:
            return []

        matching_windows = []

        def enum_windows_proc(hwnd, lParam):
            if not win32gui.IsWindowVisible(hwnd):
                return True

            try:
                # 检查窗口类名
                if ahk_class:
                    class_name = win32gui.GetClassName(hwnd)
                    if class_name != ahk_class:
                        return True

                # 检查进程名
                if ahk_exe:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    if psutil:
                        try:
                            process = psutil.Process(pid)
                            if process.name().lower() != ahk_exe.lower():
                                return True
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            return True

                # 检查窗口标题
                if window_title:
                    title = win32gui.GetWindowText(hwnd)
                    if window_title.lower() not in title.lower():
                        return True

                # 所有条件都满足，添加到结果列表
                matching_windows.append(hwnd)

            except Exception:
                pass

            return True

        try:
            win32gui.EnumWindows(enum_windows_proc, None)
        except Exception:
            pass

        return matching_windows

    @staticmethod
    def activate_window(hwnd: int) -> bool:
        """Activate and bring window to foreground using Win32 APIs"""
        if not win32gui or not hwnd:
            return False

        try:
            # 获取当前前台窗口
            current_hwnd = win32gui.GetForegroundWindow()

            # 检查窗口是否可见，如果不可见则显示
            if not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            # 检查窗口是否最小化，如果是则恢复
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.SendMessage(
                    hwnd, win32con.WM_SYSCOMMAND, win32con.SC_RESTORE, 0
                )

            # 激活窗口
            try:
                # 方法1: BringWindowToTop
                win32gui.BringWindowToTop(hwnd)

                # 方法2: SetForegroundWindow
                win32gui.SetForegroundWindow(hwnd)

                # 方法3: SetWindowPos置顶
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                )

            except Exception as e:
                pass

            # 验证激活结果
            time.sleep(0.1)
            final_foreground = win32gui.GetForegroundWindow()
            success = final_foreground == hwnd

            # 如果激活成功，进行临时置顶以保持焦点
            if success:
                try:
                    # 临时置顶
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        0,
                        0,
                        0,
                        0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                    )
                    time.sleep(0.1)
                    # 取消置顶
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_NOTOPMOST,
                        0,
                        0,
                        0,
                        0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
                    )
                except Exception as e:
                    pass

            return success

        except Exception as e:
            return False
