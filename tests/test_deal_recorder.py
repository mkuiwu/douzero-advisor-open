from __future__ import annotations

import json

import numpy as np

from douzero_advisor.recognition_service.deal_recorder import DealRecorder

# 验证一局内的每个有效捕获帧都会落盘，并在结算后写入可定位录像范围的清单。
def test_recorder_persists_every_frame_and_final_manifest(tmp_path) -> None:
    recorder = DealRecorder(tmp_path)

    path = recorder.start("deal-1", "2026-08-29T16:22:05.746Z")
    assert path is not None
    recorder.record_frame(np.zeros((16, 16, 3), dtype=np.uint8), "2026-08-29T16:22:05.846Z")
    recorder.record_frame(np.ones((16, 16, 3), dtype=np.uint8), "2026-08-29T16:22:05.946Z")
    finished = recorder.finish("settlement_detected", "2026-08-29T16:24:54.680Z")

    assert finished == path
    assert len(list(path.glob("frame_*.jpg"))) == 2
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "dealId": "deal-1",
        "recordingStatus": "settlement_detected",
        "startedAt": "2026-08-29T16:22:05.746Z",
        "endedAt": "2026-08-29T16:24:54.680Z",
        "frameCount": 2,
        "captureIntervalHintMs": 100,
    }

# 验证新局到来时不会混入上一局目录，旧局会以明确原因封存后再开始新局录制。
def test_new_deal_finalizes_the_previous_recording(tmp_path) -> None:
    recorder = DealRecorder(tmp_path)
    first = recorder.start("deal-1", "2026-08-29T16:22:05.746Z")
    recorder.record_frame(np.zeros((16, 16, 3), dtype=np.uint8), "2026-08-29T16:22:05.846Z")
    second = recorder.start("deal-2", "2026-08-29T16:25:05.746Z")

    assert first is not None and second is not None
    first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["recordingStatus"] == "superseded_by_new_deal"
    assert first_manifest["frameCount"] == 1
    assert recorder.active_deal_id == "deal-2"
