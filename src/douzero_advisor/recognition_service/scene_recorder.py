"""任务期间把每一帧写入暂存；超时或识别失败时保留现场。"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from douzero_advisor.recognition_service.protocol import RecognitionCommand


class RecognitionSceneRecorder:
    """按 requestId 暂存 JPEG；成功或取消删除，失败则改名为可回看目录。"""

    def __init__(self, root: Path | None) -> None:
        self._root = root
        self._staging: dict[str, Path] = {}
        self._index: dict[str, int] = {}
        self._frames: dict[str, list[dict[str, str]]] = {}

    def begin(self, request_id: str) -> None:
        if self._root is None or request_id in self._staging:
            return
        path = self._root / ".staging" / request_id
        path.mkdir(parents=True, exist_ok=True)
        self._staging[request_id] = path
        self._index[request_id] = 0
        self._frames[request_id] = []

    def record_frame(self, request_ids: frozenset[str], pixels: Any, observed_at: str) -> None:
        if self._root is None or not _is_bgr_frame(pixels):
            return
        import cv2

        for request_id in request_ids:
            self.begin(request_id)
            staging = self._staging.get(request_id)
            if staging is None:
                continue
            index = self._index[request_id] + 1
            self._index[request_id] = index
            name = f"frame_{index:04d}.jpg"
            cv2.imwrite(str(staging / name), pixels, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            self._frames[request_id].append({"file": name, "observedAt": observed_at})

    def finish(self, command: RecognitionCommand, result: dict[str, Any]) -> Path | None:
        request_id = command.request_id
        staging = self._staging.pop(request_id, None)
        frames = self._frames.pop(request_id, [])
        self._index.pop(request_id, None)
        if staging is None:
            return None
        status = result.get("status")
        error_code = result.get("errorCode")
        if status == "OK" or error_code in {"TASK_CANCELLED", "DUPLICATE_REQUEST_ID"}:
            shutil.rmtree(staging, ignore_errors=True)
            return None
        assert self._root is not None
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        final = self._root / f"{stamp}-{command.task_type.value}-{request_id}"
        self._root.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
        (final / "manifest.json").write_text(
            json.dumps(
                {
                    "requestId": request_id,
                    "taskType": command.task_type.value,
                    "dealId": command.deal_id,
                    "generation": command.generation,
                    "status": status,
                    "errorCode": error_code,
                    "errorMessage": result.get("errorMessage"),
                    "frameCount": len(frames),
                    "frames": frames,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return final


def _is_bgr_frame(pixels: Any) -> bool:
    return (
        isinstance(pixels, np.ndarray)
        and pixels.ndim == 3
        and pixels.shape[2] == 3
    )
