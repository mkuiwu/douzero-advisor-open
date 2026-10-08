"""Windows Graphics Capture 的同步外观与窗口稳定性闸门。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
from threading import Condition
import time
from typing import Protocol

import numpy as np

from douzero_advisor.capture.base import Frame
from douzero_advisor.capture.window_geometry import WindowGeometrySnapshot
from douzero_advisor.capture.errors import (
    BlackFrameError,
    CaptureError,
    CaptureNotReadyError,
    CaptureSizeError,
    LowContrastFrameError,
)
from douzero_advisor.capture.window import WINDOW_CLASS, WindowFinder


EXPECTED_WGC_SIZE = (1455, 819)
NEAR_BLACK_MEAN_LUMINANCE = 5.0
MINIMUM_LUMINANCE_STANDARD_DEVIATION = 2.0


class WgcCaptureClosedError(CaptureError):
    """The WGC session closed or timed out before delivering a frame."""


class WgcDependencyError(CaptureError):
    """The pinned Windows Graphics Capture package is unavailable."""


class CaptureGeometryError(CaptureError):
    """The target WGC geometry could not be reached within its recovery budget."""


class CaptureGeometryState(str, Enum):
    WAITING_FOR_WINDOW = "waiting_for_window"
    RESIZING = "resizing"
    STABILIZING = "stabilizing"
    READY = "ready"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class CaptureGeometryStatus:
    state: CaptureGeometryState
    target_size: tuple[int, int]
    actual_size: tuple[int, int] | None
    stable_frames: int
    required_stable_frames: int
    generation: int
    resize_attempts: int


@dataclass(frozen=True, slots=True)
class WgcSurface:
    """One WGC callback with its native content dimensions."""

    pixels: np.ndarray
    width: int
    height: int
    timespan: int


class GeometryController(Protocol):
    def resize_for_content(
        self,
        handle: int,
        current_size: tuple[int, int],
        target_size: tuple[int, int],
    ) -> WindowGeometrySnapshot: ...


class WgcSession(Protocol):
    def start(
        self,
        on_frame: Callable[[WgcSurface], None],
        on_closed: Callable[[], None],
    ) -> None: ...

    def close(self) -> None: ...


WgcSessionFactory = Callable[[int], WgcSession]


class WgcWindowCapture:
    """仅暴露稳定且符合标定尺寸的 WGC 帧；黑帧不能作为识别证据。"""

    def __init__(
        self,
        window_finder: WindowFinder,
        *,
        expected_size: tuple[int, int] = EXPECTED_WGC_SIZE,
        geometry_controller: GeometryController | None = None,
        session_factory: WgcSessionFactory | None = None,
        timeout_seconds: float = 2.0,
        stable_size_frames: int = 5,
        geometry_timeout_seconds: float = 5.0,
        max_resize_attempts: int = 4,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(expected_size) != 2 or any(value <= 0 for value in expected_size):
            raise ValueError("expected_size must contain two positive dimensions")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if stable_size_frames < 1:
            raise ValueError("stable_size_frames must be positive")
        if geometry_timeout_seconds <= 0:
            raise ValueError("geometry_timeout_seconds must be positive")
        if max_resize_attempts < 1:
            raise ValueError("max_resize_attempts must be positive")
        self._window_finder = window_finder
        self._expected_size = expected_size
        self._geometry_controller = geometry_controller
        self._session_factory = session_factory or _create_windows_capture_session
        self._timeout_seconds = timeout_seconds
        self._stable_size_frames = stable_size_frames
        self._geometry_timeout_seconds = geometry_timeout_seconds
        self._max_resize_attempts = max_resize_attempts
        self._monotonic = monotonic
        self._condition = Condition()
        self._session: WgcSession | None = None
        self._handle: int | None = None
        self._latest_surface: WgcSurface | None = None
        self._ready_surface: WgcSurface | None = None
        self._sequence = 0
        self._consumed_sequence = 0
        self._generation = 0
        self._state = CaptureGeometryState.WAITING_FOR_WINDOW
        self._actual_size: tuple[int, int] | None = None
        self._stable_frames = 0
        self._resize_attempts = 0
        self._recovery_started: float | None = None
        self._last_geometry: WindowGeometrySnapshot | None = None
        self._closed = False

    @property
    def status(self) -> CaptureGeometryStatus:
        with self._condition:
            return self._status_locked()

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def capture(self) -> Frame:
        # WGC 回调在独立线程到达，这里只等待“新帧 + 尺寸稳定”条件，
        # 不把旧帧伪装成当前画面；超时由调用方按 capture fault 处理。
        with self._condition:
            if self._closed:
                raise WgcCaptureClosedError("WGC capture session is closed")
        self._ensure_started()
        with self._condition:
            delivered = self._condition.wait_for(
                lambda: self._sequence > self._consumed_sequence or self._closed,
                timeout=self._timeout_seconds,
            )
            if not delivered:
                raise WgcCaptureClosedError("WGC capture timed out waiting for a frame")
            if self._closed or self._latest_surface is None:
                raise WgcCaptureClosedError("WGC capture session closed before a frame")
            self._consumed_sequence = self._sequence
            surface = self._latest_surface

        self._validate_surface(surface)
        surface_size = (surface.width, surface.height)
        if surface_size != self._expected_size:
            if self._geometry_controller is None:
                width, height = self._expected_size
                raise CaptureSizeError(
                    f"expected WGC surface {width}x{height}, got {surface.width}x{surface.height}"
                )
            self._resize_and_detach(surface_size)
            raise CaptureNotReadyError(self.status, "resizing")

        with self._condition:
            if self._state is not CaptureGeometryState.READY or self._ready_surface is None:
                status = self._status_locked()
                reason = f"stabilizing_{status.stable_frames}_of_{status.required_stable_frames}"
                raise CaptureNotReadyError(status, reason)
            ready_surface = self._ready_surface
        return self._to_frame(ready_surface)

    def close(self) -> None:
        with self._condition:
            session = self._session
            self._session = None
            self._handle = None
            self._generation += 1
            self._state = CaptureGeometryState.CLOSED
            self._closed = True
            self._condition.notify_all()
        if session is not None:
            session.close()

    def _ensure_started(self) -> None:
        with self._condition:
            if self._session is not None:
                return
        handle = self._window_finder.find_window(WINDOW_CLASS)
        if type(handle) is not int or handle <= 0:
            with self._condition:
                self._state = CaptureGeometryState.WAITING_FOR_WINDOW
                status = self._status_locked()
            raise CaptureNotReadyError(status, "waiting_for_game")
        session = self._session_factory(handle)
        with self._condition:
            if self._closed:
                session.close()
                raise WgcCaptureClosedError("WGC capture session is closed")
            self._generation += 1
            generation = self._generation
            self._handle = handle
            self._session = session
            self._latest_surface = None
            self._ready_surface = None
            self._stable_frames = 0
            self._state = CaptureGeometryState.STABILIZING
        try:
            session.start(
                lambda surface: self._on_frame(generation, surface),
                lambda: self._on_closed(generation),
            )
        except Exception:
            with self._condition:
                if generation == self._generation:
                    self._session = None
                    self._handle = None
                    self._generation += 1
                    self._state = CaptureGeometryState.WAITING_FOR_WINDOW
            session.close()
            raise

    def _on_frame(self, generation: int, surface: WgcSurface) -> None:
        if (
            isinstance(surface, WgcSurface)
            and isinstance(surface.pixels, np.ndarray)
            and surface.pixels.ndim == 3
            and surface.pixels.shape[2] >= 3
        ):
            stored = WgcSurface(
                pixels=np.ascontiguousarray(surface.pixels[..., :3]).copy(),
                width=surface.width,
                height=surface.height,
                timespan=surface.timespan,
            )
        else:
            stored = surface
        with self._condition:
            if generation != self._generation or self._closed:
                return
            self._latest_surface = stored
            if isinstance(stored, WgcSurface):
                self._actual_size = (stored.width, stored.height)
                if self._actual_size == self._expected_size:
                    self._stable_frames = min(
                        self._stable_frames + 1,
                        self._stable_size_frames,
                    )
                    if self._stable_frames >= self._stable_size_frames:
                        self._state = CaptureGeometryState.READY
                        self._ready_surface = stored
                        self._resize_attempts = 0
                        self._recovery_started = None
                    else:
                        self._state = CaptureGeometryState.STABILIZING
                else:
                    self._stable_frames = 0
                    self._ready_surface = None
                    self._state = CaptureGeometryState.RESIZING
            self._sequence += 1
            self._condition.notify_all()

    def _on_closed(self, generation: int) -> None:
        with self._condition:
            if generation != self._generation or self._closed:
                return
            self._session = None
            self._handle = None
            self._generation += 1
            self._ready_surface = None
            self._stable_frames = 0
            self._state = CaptureGeometryState.WAITING_FOR_WINDOW
            self._condition.notify_all()

    def _resize_and_detach(self, actual_size: tuple[int, int]) -> None:
        controller = self._geometry_controller
        if controller is None:
            raise AssertionError("geometry controller is required for automatic resize")
        now = self._monotonic()
        with self._condition:
            if self._recovery_started is None:
                self._recovery_started = now
            elapsed = now - self._recovery_started
            if (
                self._resize_attempts >= self._max_resize_attempts
                or elapsed >= self._geometry_timeout_seconds
            ):
                error = self._geometry_error_locked(actual_size, elapsed)
                self._resize_attempts = 0
                self._recovery_started = None
                raise error
            handle = self._handle
            if handle is None:
                status = self._status_locked()
                raise CaptureNotReadyError(status, "waiting_for_game")
            self._resize_attempts += 1

        snapshot = controller.resize_for_content(handle, actual_size, self._expected_size)
        with self._condition:
            self._last_geometry = snapshot
            session = self._session
            self._session = None
            self._handle = None
            self._generation += 1
            self._latest_surface = None
            self._ready_surface = None
            self._stable_frames = 0
            self._state = CaptureGeometryState.RESIZING
            self._condition.notify_all()
        if session is not None:
            session.close()

    def _geometry_error_locked(
        self,
        actual_size: tuple[int, int],
        elapsed: float,
    ) -> CaptureGeometryError:
        target = f"{self._expected_size[0]}x{self._expected_size[1]}"
        actual = f"{actual_size[0]}x{actual_size[1]}"
        geometry = self._last_geometry
        details = ""
        if geometry is not None:
            details = (
                f", window={geometry.window_rect}, client={geometry.client_rect}, "
                f"dpi={geometry.dpi}"
            )
        return CaptureGeometryError(
            f"geometry recovery exhausted: target={target}, actual={actual}, "
            f"attempts={self._resize_attempts}, elapsed={elapsed:.3f}s{details}"
        )

    def _status_locked(self) -> CaptureGeometryStatus:
        return CaptureGeometryStatus(
            state=self._state,
            target_size=self._expected_size,
            actual_size=self._actual_size,
            stable_frames=self._stable_frames,
            required_stable_frames=self._stable_size_frames,
            generation=self._generation,
            resize_attempts=self._resize_attempts,
        )

    def _validate_surface(self, surface: WgcSurface) -> None:
        if not isinstance(surface, WgcSurface):
            raise CaptureError("WGC did not return a native frame envelope")
        pixels = surface.pixels
        if not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8:
            raise CaptureError("WGC did not return a uint8 BGR frame")
        if (
            type(surface.width) is not int
            or type(surface.height) is not int
            or surface.width <= 0
            or surface.height <= 0
            or pixels.shape != (surface.height, surface.width, 3)
        ):
            raise CaptureError("declared WGC size does not match its valid BGR pixel buffer")

    @staticmethod
    def _to_frame(surface: WgcSurface) -> Frame:
        pixels = surface.pixels
        luminance = (
            0.114 * pixels[..., 0].astype(np.float32)
            + 0.587 * pixels[..., 1].astype(np.float32)
            + 0.299 * pixels[..., 2].astype(np.float32)
        )
        if float(luminance.mean()) < NEAR_BLACK_MEAN_LUMINANCE:
            raise BlackFrameError("captured WGC frame is black or nearly black")
        if float(luminance.std()) < MINIMUM_LUMINANCE_STANDARD_DEVIATION:
            raise LowContrastFrameError("captured WGC frame has insufficient luminance variation")
        return Frame(
            pixels=pixels,
            source=f"wgc:{WINDOW_CLASS}",
            width=surface.width,
            height=surface.height,
            sha256=hashlib.sha256(pixels.tobytes()).hexdigest(),
        )


class _WindowsCaptureSession:
    def __init__(self, handle: int) -> None:
        try:
            from windows_capture import WindowsCapture
        except ImportError as error:
            raise WgcDependencyError(
                "windows-capture==2.0.1 is required for calibrated live capture"
            ) from error
        self._capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=handle,
        )
        self._control = None

    def start(
        self,
        on_frame: Callable[[WgcSurface], None],
        on_closed: Callable[[], None],
    ) -> None:
        closed_callback = on_closed

        @self._capture.event
        def on_frame_arrived(frame, _control) -> None:
            on_frame(
                WgcSurface(
                    pixels=np.ascontiguousarray(frame.frame_buffer[..., :3]),
                    width=frame.width,
                    height=frame.height,
                    timespan=frame.timespan,
                )
            )

        @self._capture.event
        def on_closed() -> None:
            closed_callback()

        self._control = self._capture.start_free_threaded()

    def close(self) -> None:
        if self._control is None:
            return
        control = self._control
        self._control = None
        control.stop()
        control.wait()


def _create_windows_capture_session(handle: int) -> WgcSession:
    return _WindowsCaptureSession(handle)
