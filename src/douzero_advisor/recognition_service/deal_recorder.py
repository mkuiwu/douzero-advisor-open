"""按牌局持久化捕获帧，供事后逐帧回放与诊断。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np


class DealRecorder:
    """始终保留当前牌局的所有有效 BGR 捕获帧。"""

    def __init__(self, root: Path | None) -> None:
        self._root = root
        self._deal_id: str | None = None
        self._path: Path | None = None
        self._frame_count = 0
        self._started_at: str | None = None

    @property
    def active_deal_id(self) -> str | None:
        return self._deal_id

    def start(self, deal_id: str, observed_at: str) -> Path | None:
        """开始一局录制；收到新 dealId 时先封存仍在进行的上一局。"""
        if self._root is None:
            return None
        if deal_id == self._deal_id:
            return self._path
        self.finish("superseded_by_new_deal", observed_at)
        self._root.mkdir(parents=True, exist_ok=True)
        stamp = _utc_stamp(observed_at)
        base = self._root / f"{stamp}-{_safe_component(deal_id)}"
        path = base
        suffix = 1
        while path.exists():
            suffix += 1
            path = self._root / f"{base.name}-{suffix}"
        path.mkdir(parents=True)
        self._deal_id = deal_id
        self._path = path
        self._frame_count = 0
        self._started_at = observed_at
        self._write_manifest("in_progress", None)
        return path

    def record_frame(self, pixels: Any, observed_at: str) -> None:
        """把每次有效 capture 原样写入当前牌局目录。"""
        if self._path is None or not _is_bgr_frame(pixels):
            return
        import cv2

        self._frame_count += 1
        name = f"frame_{self._frame_count:06d}_{_utc_stamp(observed_at)}.jpg"
        written = cv2.imwrite(
            str(self._path / name),
            pixels,
            [int(cv2.IMWRITE_JPEG_QUALITY), 90],
        )
        if not written:
            self._frame_count -= 1

    def finish(self, reason: str, observed_at: str | None = None) -> Path | None:
        """封存当前局；已经落盘的帧即使异常退出也不会删除。"""
        path = self._path
        if path is None:
            return None
        self._write_manifest(reason, observed_at or _utc_now_text())
        self._deal_id = None
        self._path = None
        self._frame_count = 0
        self._started_at = None
        return path

    def _write_manifest(self, status: str, ended_at: str | None) -> None:
        assert self._path is not None
        (self._path / "manifest.json").write_text(
            json.dumps(
                {
                    "dealId": self._deal_id,
                    "recordingStatus": status,
                    "startedAt": self._started_at,
                    "endedAt": ended_at,
                    "frameCount": self._frame_count,
                    "captureIntervalHintMs": 100,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _is_bgr_frame(pixels: Any) -> bool:
    return (
        isinstance(pixels, np.ndarray)
        and pixels.ndim == 3
        and pixels.shape[2] == 3
    )


def _safe_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return safe or "deal"


def _utc_stamp(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace(".", "").replace("Z", "")


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
