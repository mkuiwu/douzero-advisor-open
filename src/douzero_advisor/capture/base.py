"""捕获层的不可变帧协议；上层只消费已校验的 BGR 像素。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Frame:
    """An immutable BGR image captured from an explicitly named source."""

    pixels: np.ndarray
    source: str
    width: int
    height: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.pixels, np.ndarray) or self.pixels.ndim != 3:
            raise ValueError("frame pixels must be a BGR numpy array")
        if self.pixels.shape != (self.height, self.width, 3):
            raise ValueError("frame dimensions do not match its BGR pixels")
        immutable_pixels = np.frombuffer(
            self.pixels.tobytes(order="C"), dtype=self.pixels.dtype
        ).reshape(self.height, self.width, 3)
        object.__setattr__(self, "pixels", immutable_pixels)


class FrameSource(Protocol):
    """统一桌面捕获与离线 fixture，调用方无需知道数据来源。"""

    def capture(self) -> Frame: ...
