"""Win32 鼠标后端；所有坐标均以 DPI 一致的物理像素表达。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import time
from collections.abc import Callable
from typing import Protocol

from douzero_advisor.capture.dpi_awareness import DpiAwarenessError, enable_per_monitor_dpi_awareness


# 保留原模块导出，避免已有运行入口和测试因 DPI 能力下沉到 capture 层而改变导入路径。
__all__ = [
    "CURSOR_PARK_POINT",
    "ClientClickError",
    "DpiAwarenessError",
    "Win32ClientMouse",
    "enable_per_monitor_dpi_awareness",
]


# 固定标定客户区内的中部安全停靠点；位于操作按钮下方、手牌上方，仅用于移开光标而不点击。
CURSOR_PARK_POINT = (730, 575)
_POST_CLICK_PARK_DELAY_SECONDS = 0.05


class ClientClickError(RuntimeError):
    """The calibrated game client could not be clicked safely."""




class ClientClickBackend(Protocol):
    def find_window(self, window_class: str) -> int: ...

    def client_size(self, hwnd: int) -> tuple[int, int]: ...

    def client_to_screen(
        self,
        hwnd: int,
        point: tuple[int, int],
    ) -> tuple[int, int]: ...

    def window_at(self, screen_point: tuple[int, int]) -> int: ...

    def move(self, screen_point: tuple[int, int]) -> None: ...

    def left_click(self, screen_point: tuple[int, int], hold_seconds: float) -> None: ...

    def left_click_in_window(
        self,
        screen_point: tuple[int, int],
        hold_seconds: float,
        *,
        not_before: float,
        not_after: float,
    ) -> float: ...

    def is_left_button_pressed(self) -> bool:
        """检测物理鼠标左键当前是否被用户按下。"""
        ...


class Win32ClientMouse:
    """Click a dynamic client coordinate only on the calibrated Unity window."""

    def __init__(
        self,
        *,
        backend: ClientClickBackend | None = None,
        window_class: str = "UnityWndClass",
        expected_size: tuple[int, int] = (1455, 819),
        maximum_size_drift: int = 16,
        click_hold_seconds: float = 0.06,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not window_class:
            raise ValueError("window_class must not be empty")
        if (
            len(expected_size) != 2
            or any(type(value) is not int or value <= 0 for value in expected_size)
        ):
            raise ValueError("expected_size must contain two positive integers")
        if type(maximum_size_drift) is not int or maximum_size_drift < 0:
            raise ValueError("maximum_size_drift must be a non-negative integer")
        if click_hold_seconds < 0:
            raise ValueError("click_hold_seconds must be non-negative")
        self._backend = (
            backend if backend is not None else CtypesWin32MouseBackend(clock=clock)
        )
        self._window_class = window_class
        self._expected_size = expected_size
        self._maximum_size_drift = maximum_size_drift
        self._click_hold_seconds = click_hold_seconds
        self._sleep = sleep

    def click(self, point: tuple[int, int]) -> None:
        screen_point = self._resolve_screen_point(point)
        self._backend.left_click(screen_point, self._click_hold_seconds)

    def move(self, point: tuple[int, int]) -> None:
        """只将光标移到已校验的客户区坐标，不产生任何鼠标按键事件。"""

        screen_point = self._resolve_screen_point(point)
        self._backend.move(screen_point)

    def park(self) -> None:
        """把光标停到已校验的中部安全区，避免覆盖后续动作识别区域。"""

        self.move(CURSOR_PARK_POINT)

    def park_after_click(self) -> None:
        """鼠标左键释放后留出 50ms 的 UI 响应时间，再将光标移到安全区。"""

        self._sleep(_POST_CLICK_PARK_DELAY_SECONDS)
        self.park()

    def move_after_click(self, point: tuple[int, int]) -> None:
        """选牌点击释放后等待 50ms，再悬停到用户可提交的动作按钮。"""

        self._sleep(_POST_CLICK_PARK_DELAY_SECONDS)
        self.move(point)

    def is_user_left_button_pressed(self) -> bool:
        """检测用户物理按下鼠标左键，用于自动化执行期间的接管检测。"""
        return self._backend.is_left_button_pressed()

    def click_in_window(
        self,
        point: tuple[int, int],
        *,
        not_before: float,
        not_after: float,
    ) -> float:
        """Submit a click only if mouse-down still falls inside the absolute window."""
        if not_before > not_after:
            raise ClientClickError("click window is invalid")
        screen_point = self._resolve_screen_point(point)
        return self._backend.left_click_in_window(
            screen_point,
            self._click_hold_seconds,
            not_before=not_before,
            not_after=not_after,
        )

    def _resolve_screen_point(self, point: tuple[int, int]) -> tuple[int, int]:
        if (
            len(point) != 2
            or any(type(value) is not int for value in point)
        ):
            raise ClientClickError("click point must contain two integers")
        hwnd = self._backend.find_window(self._window_class)
        if not hwnd:
            raise ClientClickError(
                f"{self._window_class} window was not found"
            )
        x, y = point
        width, height = self._expected_size
        if not 0 <= x < width or not 0 <= y < height:
            raise ClientClickError(
                f"click point {point} is outside the client area {self._expected_size}"
            )
        client_size = self._backend.client_size(hwnd)
        if any(
            abs(actual - expected) > self._maximum_size_drift
            for actual, expected in zip(client_size, self._expected_size, strict=True)
        ):
            raise ClientClickError(
                "game client size changed: "
                f"expected captured frame {self._expected_size}, got client {client_size}"
            )
        client_width, client_height = client_size
        client_point = (
            round(x * client_width / width),
            round(y * client_height / height),
        )
        screen_point = self._backend.client_to_screen(hwnd, client_point)
        if self._backend.window_at(screen_point) != hwnd:
            raise ClientClickError("click point is covered by another window")
        return screen_point


class CtypesWin32MouseBackend:
    """Small ctypes wrapper kept behind an injectable test boundary."""

    _LEFT_DOWN = 0x0002
    _LEFT_UP = 0x0004

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        if sys.platform != "win32":
            raise OSError("Win32 mouse input is available only on Windows")
        self._clock = clock
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        self._user32.FindWindowW.restype = wintypes.HWND
        self._user32.GetClientRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self._user32.GetClientRect.restype = wintypes.BOOL
        self._user32.ClientToScreen.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        )
        self._user32.ClientToScreen.restype = wintypes.BOOL
        self._user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        self._user32.WindowFromPoint.restype = wintypes.HWND
        self._user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self._user32.SetCursorPos.restype = wintypes.BOOL
        self._user32.mouse_event.argtypes = (
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t,
        )
        self._user32.mouse_event.restype = None
        self._user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

    def find_window(self, window_class: str) -> int:
        return int(self._user32.FindWindowW(window_class, None) or 0)

    def client_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise _last_win32_error("GetClientRect")
        return rect.right - rect.left, rect.bottom - rect.top

    def client_to_screen(
        self,
        hwnd: int,
        point: tuple[int, int],
    ) -> tuple[int, int]:
        converted = wintypes.POINT(*point)
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(converted)):
            raise _last_win32_error("ClientToScreen")
        return converted.x, converted.y

    def left_click(self, screen_point: tuple[int, int], hold_seconds: float) -> None:
        x, y = screen_point
        if not self._user32.SetCursorPos(x, y):
            raise _last_win32_error("SetCursorPos")
        self._user32.mouse_event(self._LEFT_DOWN, 0, 0, 0, 0)
        try:
            time.sleep(hold_seconds)
        finally:
            self._user32.mouse_event(self._LEFT_UP, 0, 0, 0, 0)

    def move(self, screen_point: tuple[int, int]) -> None:
        """仅移动光标，不产生鼠标按键事件。"""

        x, y = screen_point
        if not self._user32.SetCursorPos(x, y):
            raise _last_win32_error("SetCursorPos")

    def left_click_in_window(
        self,
        screen_point: tuple[int, int],
        hold_seconds: float,
        *,
        not_before: float,
        not_after: float,
    ) -> float:
        x, y = screen_point
        if not self._user32.SetCursorPos(x, y):
            raise _last_win32_error("SetCursorPos")
        mouse_down_at = self._clock()
        if mouse_down_at < not_before:
            raise ClientClickError("mouse-down would occur before the click window")
        if mouse_down_at > not_after:
            raise ClientClickError("mouse-down would occur after the click window")
        self._user32.mouse_event(self._LEFT_DOWN, 0, 0, 0, 0)
        try:
            time.sleep(hold_seconds)
        finally:
            self._user32.mouse_event(self._LEFT_UP, 0, 0, 0, 0)
        return mouse_down_at

    def window_at(self, screen_point: tuple[int, int]) -> int:
        return int(self._user32.WindowFromPoint(wintypes.POINT(*screen_point)) or 0)

    def is_left_button_pressed(self) -> bool:
        """GetAsyncKeyState 最高位为 1 表示左键当前被按下。"""
        return bool(self._user32.GetAsyncKeyState(0x01) & 0x8000)


def _last_win32_error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{operation} failed")
