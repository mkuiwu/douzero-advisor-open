"""离线图像源：不接触桌面窗口，用于可重复的识别/监听回放。"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path

import cv2
import numpy as np

from douzero_advisor.capture.base import Frame


class FixtureSource:
    """Read a local PNG fixture without any desktop or window capability."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def capture(self) -> Frame:
        try:
            encoded = self._path.read_bytes()
        except FileNotFoundError as error:
            raise ValueError(f"fixture does not exist: {self._path}") from error
        pixels = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if pixels is None:
            raise ValueError(f"fixture is not a decodable image: {self._path}")
        height, width = pixels.shape[:2]
        return Frame(
            pixels=pixels,
            source=self._path.name,
            width=width,
            height=height,
            sha256=hashlib.sha256(encoded).hexdigest(),
        )


class FixtureSequenceSource:
    """Consume each captured PNG exactly once for deterministic offline replay."""

    def __init__(self, paths: Sequence[str | Path]) -> None:
        self._paths = tuple(Path(path) for path in paths)
        if len(set(self._paths)) != len(self._paths):
            raise ValueError("fixture sequence paths must be distinct")
        self._next_index = 0

    def capture(self) -> Frame:
        if self._next_index >= len(self._paths):
            raise StopIteration
        path = self._paths[self._next_index]
        self._next_index += 1
        try:
            encoded = path.read_bytes()
        except FileNotFoundError as error:
            raise ValueError(f"fixture does not exist: {path}") from error
        pixels = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if pixels is None:
            raise ValueError(f"fixture is not a decodable image: {path}")
        height, width = pixels.shape[:2]
        return Frame(
            pixels=pixels,
            source=path.name,
            width=width,
            height=height,
            sha256=hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest(),
        )
