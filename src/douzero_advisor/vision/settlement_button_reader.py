"""Recognize the two fixed-layout controls on the settlement screen.

The settlement controls are deliberately separate from the lifecycle/action
button reader.  They live lower on the client, use a different blue/yellow
skin, and must never be treated as a play or pre-play action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from douzero_advisor.vision.action_button_reader import ButtonBox, ButtonPalette


SettlementButtonName = Literal["reveal_start", "continue_game"]
SettlementStage = Literal["settlement", "unknown"]


@dataclass(frozen=True, slots=True)
class SettlementButtonTemplate:
    """Normalized geometry/color template for one settlement control."""

    name: SettlementButtonName
    label: str
    palette: ButtonPalette
    center_x: float
    center_y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class SettlementButton:
    name: SettlementButtonName
    label: str
    box: ButtonBox
    confidence: float


@dataclass(frozen=True, slots=True)
class SettlementButtonRead:
    stage: SettlementStage
    buttons: tuple[SettlementButton, ...]
    reason: str | None = None

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(button.label for button in self.buttons)

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.stage,
            tuple(
                (
                    button.name,
                    button.label,
                    button.box.left,
                    button.box.top,
                    button.box.right,
                    button.box.bottom,
                )
                for button in self.buttons
            ),
        )


# Calibrated from the supplied 2048x1152 settlement capture.  Values are
# normalized so the same fixed UI can be read at the 1455x819 capture size.
SETTLEMENT_BUTTON_TEMPLATES: tuple[SettlementButtonTemplate, ...] = (
    SettlementButtonTemplate(
        "reveal_start", "明牌开始×5", "blue", 0.415, 0.845, 0.132, 0.071
    ),
    SettlementButtonTemplate(
        "continue_game", "继续游戏", "orange", 0.588, 0.845, 0.132, 0.071
    ),
)


class SettlementButtonReader:
    """Read settlement controls by their dedicated fixed-layout templates."""

    def __init__(
        self,
        *,
        search_roi: tuple[float, float, float, float] = (0.28, 0.72, 0.72, 0.94),
        min_fill: float = 0.42,
        position_tolerance: float = 0.055,
        size_tolerance: float = 0.035,
    ) -> None:
        left, top, right, bottom = search_roi
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("search_roi must be ordered normalized bounds")
        if not 0 <= min_fill <= 1:
            raise ValueError("min_fill must be between zero and one")
        if position_tolerance <= 0 or size_tolerance <= 0:
            raise ValueError("template tolerances must be positive")
        self._search_roi = search_roi
        self._min_fill = float(min_fill)
        self._position_tolerance = float(position_tolerance)
        self._size_tolerance = float(size_tolerance)

    def read(self, frame: np.ndarray) -> SettlementButtonRead:
        _validate_frame(frame)
        height, width = frame.shape[:2]
        left, top, right, bottom = _pixel_roi(self._search_roi, width, height)
        roi = frame[top:bottom, left:right]
        blue, green, red = (
            channel.astype(np.int16, copy=False) for channel in cv2.split(roi)
        )
        masks = {
            "blue": (
                (blue > 165)
                & (green > 115)
                & (blue - red > 45)
                & (blue - green > 5)
            ).astype(np.uint8)
            * 255,
            "orange": (
                (red > 190)
                & (green > 135)
                & (red - blue > 105)
                & (green - blue > 75)
            ).astype(np.uint8)
            * 255,
        }
        candidates = [
            candidate
            for palette, mask in masks.items()
            for candidate in _find_boxes(
                mask, palette, left, top, width, height, self._min_fill
            )
        ]
        matches: list[SettlementButton] = []
        for template in SETTLEMENT_BUTTON_TEMPLATES:
            ranked = sorted(
                (
                    (_template_distance(template, candidate, width, height), candidate)
                    for candidate in candidates
                    if candidate.palette == template.palette
                ),
                key=lambda item: item[0],
            )
            if not ranked:
                continue
            distance, box = ranked[0]
            if distance > 1.0:
                continue
            matches.append(
                SettlementButton(
                    template.name,
                    template.label,
                    box,
                    round(max(0.0, 1.0 - distance), 4),
                )
            )
        matches.sort(key=lambda button: button.box.left)
        if not matches:
            return SettlementButtonRead("unknown", (), "no_settlement_buttons")
        if len(matches) < len(SETTLEMENT_BUTTON_TEMPLATES):
            return SettlementButtonRead(
                "settlement", tuple(matches), "partial_settlement_layout"
            )
        return SettlementButtonRead("settlement", tuple(matches))


def _validate_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be an HxWx3 BGR image")
    if frame.shape[0] <= 0 or frame.shape[1] <= 0:
        raise ValueError("frame dimensions must be non-empty")


def _pixel_roi(
    roi: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    left, top, right, bottom = roi
    return (
        int(width * left),
        int(height * top),
        max(int(width * right), int(width * left) + 1),
        max(int(height * bottom), int(height * top) + 1),
    )


def _find_boxes(
    mask: np.ndarray,
    palette: str,
    offset_x: int,
    offset_y: int,
    frame_width: int,
    frame_height: int,
    min_fill: float,
) -> list[ButtonBox]:
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 9), dtype=np.uint8))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed)
    boxes: list[ButtonBox] = []
    for index in range(1, count):
        x, y, box_width, box_height, area = (int(value) for value in stats[index])
        width_ratio = box_width / frame_width
        height_ratio = box_height / frame_height
        fill = area / float(max(1, box_width * box_height))
        if not 0.09 <= width_ratio <= 0.18:
            continue
        if not 0.045 <= height_ratio <= 0.105:
            continue
        if fill < min_fill:
            continue
        boxes.append(
            ButtonBox(
                offset_x + x,
                offset_y + y,
                offset_x + x + box_width - 1,
                offset_y + y + box_height - 1,
                palette,  # type: ignore[arg-type]
            )
        )
    return boxes


def _template_distance(
    template: SettlementButtonTemplate,
    box: ButtonBox,
    frame_width: int,
    frame_height: int,
) -> float:
    center_x = box.center[0] / frame_width
    center_y = box.center[1] / frame_height
    width = box.width / frame_width
    height = box.height / frame_height
    return (
        abs(center_x - template.center_x) / 0.055
        + abs(center_y - template.center_y) / 0.055
        + abs(width - template.width) / 0.035
        + abs(height - template.height) / 0.035
    ) / 4.0
