from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from douzero_advisor.capture.window_geometry import (
    PyWin32WindowGeometry,
    WindowGeometryController,
)
from douzero_advisor.capture.window import WINDOW_CLASS, PyWin32WindowFinder, WindowFinder


EXPECTED_CLIENT_SIZE = (1455, 819)


@dataclass(frozen=True, slots=True)
class GameWindowGeometry:
    """桌面界面可展示的游戏客户区与 DPI 标定状态。"""

    found: bool
    current_size: tuple[int, int] | None = None
    target_size: tuple[int, int] = EXPECTED_CLIENT_SIZE
    dpi: int | None = None
    error: str | None = None
    actionable_error: bool = False

    @property
    def effective_size(self) -> tuple[int, int] | None:
        """将旧 DPI 虚拟化尺寸换算为可与 WGC 比较的物理像素尺寸。"""
        if self.current_size is None:
            return None
        if self._within_calibration(self.current_size):
            return self.current_size
        if self.dpi is None or self.dpi <= 0:
            return self.current_size
        scale = self.dpi / 96
        return tuple(round(value * scale) for value in self.current_size)

    @property
    def match_mode(self) -> str:
        """说明当前尺寸是直接物理命中、DPI 等效命中还是仍需调整。"""
        if not self.found or self.error is not None or self.current_size is None:
            return "unavailable"
        if self._within_calibration(self.current_size):
            return "physical_pixels"
        if self.dpi is not None and self.dpi != 96 and self._within_calibration(
            self.effective_size
        ):
            return "dpi_virtualized"
        return "mismatch"

    @property
    def matches(self) -> bool:
        return self.match_mode in {"physical_pixels", "dpi_virtualized"}

    def _within_calibration(self, size: tuple[int, int] | None) -> bool:
        # Reader 的 ROI 是按标定截图的绝对坐标读取，不能把几像素误差伪装成命中。
        return size == self.target_size


class GeometryReader(Protocol):
    def snapshot(self, handle: int): ...


def game_window_available(finder: WindowFinder | None = None) -> bool:
    """Return whether the real Unity game window currently exists."""
    if finder is None:
        try:
            finder = PyWin32WindowFinder()
        except Exception:
            return False
    try:
        handle = finder.find_window(WINDOW_CLASS)
    except Exception:
        return False
    return type(handle) is int and handle > 0


class GameWindowGeometryService:
    """检查游戏窗口；只有显式调用 ``adjust`` 才会调整客户区。"""

    def __init__(
        self,
        finder: WindowFinder | None = None,
        geometry: GeometryReader | None = None,
        *,
        target_size: tuple[int, int] = EXPECTED_CLIENT_SIZE,
    ) -> None:
        self._finder = finder
        self._geometry = geometry
        self._target_size = target_size

    def inspect(self) -> GameWindowGeometry:
        finder = self._resolve_finder()
        if finder is None:
            return GameWindowGeometry(found=False, target_size=self._target_size)
        try:
            handle = finder.find_window(WINDOW_CLASS)
            if type(handle) is not int or handle <= 0:
                return GameWindowGeometry(found=False, target_size=self._target_size)
            snapshot = self._resolve_geometry().snapshot(handle)
            rect = snapshot.client_rect
            current_size = (rect[2] - rect[0], rect[3] - rect[1])
            return GameWindowGeometry(
                found=True,
                current_size=current_size,
                target_size=self._target_size,
                dpi=snapshot.dpi,
            )
        except Exception as error:
            return GameWindowGeometry(
                found=True,
                target_size=self._target_size,
                error=f"{type(error).__name__}: {error}",
                actionable_error=False,
            )

    def adjust(self) -> GameWindowGeometry:
        """按一次明确的用户请求调整客户区，并回读结果验证。

        此方法不会被检查路径或运行时自动调用；调用方必须在 UI 中完成
        "帮助调整客户端"按钮与二次确认后才可进入这里。
        """
        finder = self._resolve_finder()
        if finder is None:
            return GameWindowGeometry(found=False, target_size=self._target_size)
        try:
            handle = finder.find_window(WINDOW_CLASS)
            if type(handle) is not int or handle <= 0:
                return GameWindowGeometry(found=False, target_size=self._target_size)
            geometry = self._resolve_geometry()
            before = geometry.snapshot(handle)
            rect = before.client_rect
            current_size = (rect[2] - rect[0], rect[3] - rect[1])
            current = GameWindowGeometry(
                found=True,
                current_size=current_size,
                target_size=self._target_size,
                dpi=before.dpi,
            )
            if current.matches:
                return current
            WindowGeometryController(geometry).resize_for_content(
                handle, current_size, self._target_size
            )
            after = geometry.snapshot(handle)
            after_rect = after.client_rect
            return GameWindowGeometry(
                found=True,
                current_size=(after_rect[2] - after_rect[0], after_rect[3] - after_rect[1]),
                target_size=self._target_size,
                dpi=after.dpi,
            )
        except Exception as error:
            current = self.inspect()
            return GameWindowGeometry(
                found=current.found,
                current_size=current.current_size,
                target_size=self._target_size,
                dpi=current.dpi,
                error=f"{type(error).__name__}: {error}",
                actionable_error=True,
            )

    def _resolve_finder(self) -> WindowFinder | None:
        if self._finder is not None:
            return self._finder
        try:
            self._finder = PyWin32WindowFinder()
        except Exception:
            return None
        return self._finder

    def _resolve_geometry(self) -> GeometryReader:
        if self._geometry is None:
            self._geometry = PyWin32WindowGeometry()
        return self._geometry
