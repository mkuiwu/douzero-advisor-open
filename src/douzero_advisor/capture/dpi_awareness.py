"""Windows 物理像素坐标所需的每显示器 DPI 感知配置。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


class DpiAwarenessError(RuntimeError):
    """当前进程无法可靠使用与 WGC 一致的物理像素坐标。"""


_DPI_AWARENESS_PER_MONITOR = 2
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_ERROR_ACCESS_DENIED = 5


def enable_per_monitor_dpi_awareness() -> str:
    """在创建 Win32 或 WGC 对象前切换到每显示器物理像素坐标系。"""
    if sys.platform != "win32":
        return "not_applicable"
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = user32.SetProcessDpiAwarenessContext
        get_thread_context = user32.GetThreadDpiAwarenessContext
        get_awareness = user32.GetAwarenessFromDpiAwarenessContext
    except (AttributeError, OSError) as error:
        raise DpiAwarenessError(
            "per-monitor DPI awareness is unavailable; physical capture coordinates are unsafe"
        ) from error

    set_context.argtypes = (ctypes.c_void_p,)
    set_context.restype = wintypes.BOOL
    get_thread_context.argtypes = ()
    get_thread_context.restype = ctypes.c_void_p
    get_awareness.argtypes = (ctypes.c_void_p,)
    get_awareness.restype = ctypes.c_int

    if set_context(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
        return "per_monitor_v2"

    error_code = ctypes.get_last_error()
    awareness = int(get_awareness(get_thread_context()))
    if error_code == _ERROR_ACCESS_DENIED and awareness == _DPI_AWARENESS_PER_MONITOR:
        return "preconfigured_per_monitor"
    raise DpiAwarenessError(
        "could not enable per-monitor DPI awareness "
        f"(Win32 error {error_code}, awareness {awareness})"
    )
