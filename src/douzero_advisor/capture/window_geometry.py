"""测量并调整窗口客户区，避免外框尺寸与内容尺寸混淆。"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Protocol


class WindowGeometryError(RuntimeError):
    """The game window geometry could not be inspected or adjusted."""


@dataclass(frozen=True, slots=True)
class WindowGeometrySnapshot:
    window_rect: tuple[int, int, int, int]
    client_rect: tuple[int, int, int, int]
    dpi: int
    maximized: bool
    minimized: bool = False


class WindowGeometryBackend(Protocol):
    def snapshot(self, handle: int) -> WindowGeometrySnapshot: ...

    def restore(self, handle: int) -> None: ...

    def resize(self, handle: int, width: int, height: int) -> None: ...


class WindowGeometryController:
    """Resize an existing window using measured WGC content-size feedback."""

    def __init__(self, backend: WindowGeometryBackend) -> None:
        self._backend = backend

    def resize_for_content(
        self,
        handle: int,
        current_size: tuple[int, int],
        target_size: tuple[int, int],
    ) -> WindowGeometrySnapshot:
        # SetWindowPos 接受的是外框尺寸；按当前外框与客户区差值修正，
        # 再由下一次 snapshot 验证实际客户区，而不是假设 DPI 下调整必然成功。
        _validate_handle(handle)
        _validate_size(current_size, "current_size")
        _validate_size(target_size, "target_size")

        before = self._backend.snapshot(handle)
        if before.maximized or before.minimized:
            self._backend.restore(handle)
            before = self._backend.snapshot(handle)

        left, top, right, bottom = _validate_rect(before.window_rect, "window_rect")
        corrected_width = right - left + target_size[0] - current_size[0]
        corrected_height = bottom - top + target_size[1] - current_size[1]
        if corrected_width <= 0 or corrected_height <= 0:
            raise WindowGeometryError("corrected outer size must contain two positive dimensions")

        self._backend.resize(handle, corrected_width, corrected_height)
        return self._backend.snapshot(handle)


class PyWin32WindowGeometry:
    """Win32 implementation used by the live WGC runtime."""

    def snapshot(self, handle: int) -> WindowGeometrySnapshot:
        _validate_handle(handle)
        try:
            import win32con
            import win32gui

            window_rect = tuple(win32gui.GetWindowRect(handle))
            client_rect = tuple(win32gui.GetClientRect(handle))
            placement = win32gui.GetWindowPlacement(handle)
            show_command = placement[1]
            minimized = show_command == win32con.SW_SHOWMINIMIZED
            dpi = _get_dpi_for_window(handle)
            if minimized and not _has_positive_rect(client_rect):
                client_rect = (0, 0, 0, 0)
            return WindowGeometrySnapshot(
                window_rect=_validate_rect(window_rect, "window_rect"),
                client_rect=(
                    client_rect
                    if minimized and client_rect == (0, 0, 0, 0)
                    else _validate_rect(client_rect, "client_rect")
                ),
                dpi=dpi,
                maximized=show_command == win32con.SW_SHOWMAXIMIZED,
                minimized=minimized,
            )
        except WindowGeometryError:
            raise
        except Exception as error:
            raise WindowGeometryError(
                f"could not inspect the game window geometry: {type(error).__name__}: {error}"
            ) from error

    def restore(self, handle: int) -> None:
        _validate_handle(handle)
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        except Exception as error:
            raise WindowGeometryError(
                f"could not restore the game window: {type(error).__name__}: {error}"
            ) from error

    def resize(self, handle: int, width: int, height: int) -> None:
        _validate_handle(handle)
        _validate_size((width, height), "outer_size")
        try:
            import win32con
            import win32gui

            flags = win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(handle, 0, 0, 0, width, height, flags)
        except Exception as error:
            raise WindowGeometryError(
                f"could not resize the game window: {type(error).__name__}: {error}"
            ) from error


def _get_dpi_for_window(handle: int) -> int:
    try:
        get_dpi = ctypes.windll.user32.GetDpiForWindow
        get_dpi.argtypes = [ctypes.c_void_p]
        get_dpi.restype = ctypes.c_uint
        dpi = int(get_dpi(handle))
    except (AttributeError, OSError):
        dpi = 96
    return dpi if dpi > 0 else 96


def _validate_handle(handle: int) -> None:
    if type(handle) is not int or handle <= 0:
        raise WindowGeometryError("window handle must be a positive integer")


def _validate_size(size: tuple[int, int], name: str) -> None:
    if (
        not isinstance(size, tuple)
        or len(size) != 2
        or any(type(value) is not int or value <= 0 for value in size)
    ):
        raise WindowGeometryError(f"{name} must contain two positive integers")


def _validate_rect(
    rect: tuple[int, ...],
    name: str,
) -> tuple[int, int, int, int]:
    if (
        not isinstance(rect, tuple)
        or len(rect) != 4
        or any(type(value) is not int for value in rect)
    ):
        raise WindowGeometryError(f"{name} must contain four integers")
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        raise WindowGeometryError(f"{name} must have positive width and height")
    return left, top, right, bottom


def _has_positive_rect(rect: tuple[int, ...]) -> bool:
    return (
        isinstance(rect, tuple)
        and len(rect) == 4
        and all(type(value) is int for value in rect)
        and rect[2] > rect[0]
        and rect[3] > rect[1]
    )
