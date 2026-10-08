"""Detect fixed-skin play-stage action buttons from their rendered boxes.

This reader deliberately returns observed rectangles instead of fixed click
coordinates.  It is a visual probe, not an authorization gate: bidding uses
some of the same colors and geometry, so callers must first require the
existing ``phase == playing`` and local-turn-ready evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np


ActionButtonName = Literal["play", "pass", "hint", "cannot_beat"]
ActionButtonLayout = Literal[
    "cannot_beat_only",
    "play_hint",
    "play_pass_hint",
    "unknown",
]
ButtonPalette = Literal["orange", "blue"]


@dataclass(frozen=True, slots=True)
class ButtonBox:
    left: int
    top: int
    right: int
    bottom: int
    palette: ButtonPalette

    @property
    def width(self) -> int:
        return self.right - self.left + 1

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True, slots=True)
class ActionButtonRead:
    layout: ActionButtonLayout
    buttons: dict[ActionButtonName, ButtonBox]
    reason: str | None = None

    @property
    def centers(self) -> dict[ActionButtonName, tuple[int, int]]:
        return {name: box.center for name, box in self.buttons.items()}

    @property
    def actionable(self) -> bool:
        return self.layout != "unknown"


class ActionButtonReader:
    """Find one, two, or three wide play-stage controls in the current frame."""

    def __init__(
        self,
        *,
        search_roi: tuple[float, float, float, float] = (0.23, 0.54, 0.77, 0.72),
        min_width: int = 125,
        max_width: int = 160,
        min_height: int = 50,
        max_height: int = 72,
        min_fill: float = 0.72,
    ) -> None:
        left, top, right, bottom = search_roi
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("search_roi must be ordered normalized bounds")
        if min_width <= 0 or max_width < min_width:
            raise ValueError("button width bounds are invalid")
        if min_height <= 0 or max_height < min_height:
            raise ValueError("button height bounds are invalid")
        if not 0 <= min_fill <= 1:
            raise ValueError("min_fill must be between zero and one")
        self._search_roi = search_roi
        self._min_width = int(min_width)
        self._max_width = int(max_width)
        self._min_height = int(min_height)
        self._max_height = int(max_height)
        self._min_fill = float(min_fill)

    def read(self, frame: np.ndarray) -> ActionButtonRead:
        _validate_frame(frame)
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = _pixel_roi(
            self._search_roi,
            frame_width,
            frame_height,
        )
        roi = frame[top:bottom, left:right]
        blue, green, red = (
            channel.astype(np.int16, copy=False) for channel in cv2.split(roi)
        )
        orange_mask = (
            (red > 190) & (green > 120) & ((red - blue) > 70)
        ).astype(np.uint8) * 255
        blue_mask = (
            (blue > 205) & ((blue - green) > 45) & ((blue - red) > 70)
        ).astype(np.uint8) * 255
        boxes = sorted(
            self._boxes(orange_mask, "orange", left, top)
            + self._boxes(blue_mask, "blue", left, top),
            key=lambda box: box.left,
        )
        if not boxes:
            return ActionButtonRead("unknown", {}, "no_wide_buttons")
        if max(box.center[1] for box in boxes) - min(box.center[1] for box in boxes) > 8:
            return ActionButtonRead("unknown", {}, "buttons_not_horizontally_aligned")
        palettes = [box.palette for box in boxes]
        if palettes == ["blue"]:
            # The single blue control is the client-authored "要不起" state:
            # no legal response exists.  It is not the voluntary "不出" button
            # shown between "出牌" and "提示" when a response is available.
            return ActionButtonRead("cannot_beat_only", {"cannot_beat": boxes[0]})
        if palettes == ["orange", "blue"]:
            return ActionButtonRead(
                "play_hint",
                {"play": boxes[0], "hint": boxes[1]},
            )
        if palettes == ["orange", "blue", "blue"]:
            return ActionButtonRead(
                "play_pass_hint",
                {"play": boxes[0], "pass": boxes[1], "hint": boxes[2]},
            )
        return ActionButtonRead(
            "unknown",
            {},
            f"unexpected_count_or_color_order:{','.join(palettes)}",
        )

    def _boxes(
        self,
        mask: np.ndarray,
        palette: ButtonPalette,
        offset_x: int,
        offset_y: int,
    ) -> list[ButtonBox]:
        closed = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            np.ones((9, 7), dtype=np.uint8),
        )
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed)
        boxes: list[ButtonBox] = []
        for index in range(1, count):
            left, top, width, height, area = (
                int(value) for value in stats[index]
            )
            fill = area / float(width * height)
            if not self._min_width <= width <= self._max_width:
                continue
            if not self._min_height <= height <= self._max_height:
                continue
            if fill < self._min_fill:
                continue
            boxes.append(
                ButtonBox(
                    offset_x + left,
                    offset_y + top,
                    offset_x + left + width - 1,
                    offset_y + top + height - 1,
                    palette,
                )
            )
        return boxes


class ActionButtonStabilizer:
    """Release button centers only after repeated matching observations."""

    def __init__(self, *, required: int = 3, coordinate_tolerance: int = 4) -> None:
        if required <= 0:
            raise ValueError("required must be positive")
        if coordinate_tolerance < 0:
            raise ValueError("coordinate_tolerance must be non-negative")
        self._required = int(required)
        self._coordinate_tolerance = int(coordinate_tolerance)
        self._candidate: ActionButtonRead | None = None
        self._count = 0

    def observe(self, read: ActionButtonRead) -> ActionButtonRead:
        if read.layout == "unknown":
            self.reset()
            return read
        if self._candidate is not None and _same_read(
            self._candidate,
            read,
            tolerance=self._coordinate_tolerance,
        ):
            self._count += 1
        else:
            self._candidate = read
            self._count = 1
        if self._count >= self._required:
            self._candidate = read
            return read
        return ActionButtonRead(
            "unknown",
            {},
            f"awaiting_stability:{self._count}/{self._required}",
        )

    def reset(self) -> None:
        self._candidate = None
        self._count = 0


class ActionButtonGate:
    """Apply the existing play-phase/local-turn gates before stabilization."""

    def __init__(
        self,
        *,
        reader: ActionButtonReader | None = None,
        stabilizer: ActionButtonStabilizer | None = None,
    ) -> None:
        self._reader = reader or ActionButtonReader()
        self._stabilizer = stabilizer or ActionButtonStabilizer()

    def observe(
        self,
        frame: np.ndarray,
        *,
        phase: str,
        my_turn_ready: bool,
    ) -> ActionButtonRead:
        if phase != "playing":
            self._stabilizer.reset()
            return ActionButtonRead("unknown", {}, "phase_not_playing")
        if my_turn_ready is not True:
            self._stabilizer.reset()
            return ActionButtonRead("unknown", {}, "local_turn_not_ready")
        return self._stabilizer.observe(self._reader.read(frame))

    def reset(self) -> None:
        """Discard button observations that belong to an earlier turn."""
        self._stabilizer.reset()


def _same_read(
    first: ActionButtonRead,
    second: ActionButtonRead,
    *,
    tolerance: int,
) -> bool:
    if first.layout != second.layout or first.buttons.keys() != second.buttons.keys():
        return False
    for name, first_box in first.buttons.items():
        second_box = second.buttons[name]
        if first_box.palette != second_box.palette:
            return False
        if max(
            abs(first_box.left - second_box.left),
            abs(first_box.top - second_box.top),
            abs(first_box.right - second_box.right),
            abs(first_box.bottom - second_box.bottom),
        ) > tolerance:
            return False
    return True


def _pixel_roi(
    roi: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = roi
    return (
        int(width * left),
        int(height * top),
        min(width, max(1, int(width * right))),
        min(height, max(1, int(height * bottom))),
    )


def _validate_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be an HxWx3 BGR image")
    if frame.shape[0] <= 0 or frame.shape[1] <= 0:
        raise ValueError("frame dimensions must be non-empty")
