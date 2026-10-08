from __future__ import annotations

import numpy as np

from douzero_advisor.recognition_service.protocol import parse_command
from douzero_advisor.recognition_service.scene_recorder import RecognitionSceneRecorder


def _command(request_id: str = "req-1"):
    return parse_command(
        {
            "contractVersion": "recognition.v1",
            "messageType": "SUBMIT",
            "taskType": "LOCAL_TURN",
            "requestId": request_id,
            "dealId": "deal-1",
            "generation": 1,
            "deadlineMs": 5000,
            "localSeat": "landlord",
            "requiredActors": ["landlord_down"],
            "turnEntryMode": "accept_current_stable_turn",
        }
    )


def test_recorder_keeps_frames_on_observation_failure(tmp_path) -> None:
    recorder = RecognitionSceneRecorder(tmp_path)
    command = _command()
    recorder.begin(command.request_id)
    pixels = np.zeros((16, 16, 3), dtype=np.uint8)
    recorder.record_frame(frozenset({command.request_id}), pixels, "2026-08-29T09:00:00.000Z")
    saved = recorder.finish(
        command,
        {
            "status": "FAILED",
            "errorCode": "OBSERVATION_UNCERTAIN",
            "errorMessage": "required actor action unreadable: landlord_down",
        },
    )
    assert saved is not None
    assert (saved / "frame_0001.jpg").is_file()
    assert (saved / "manifest.json").is_file()


def test_recorder_discards_frames_on_success(tmp_path) -> None:
    recorder = RecognitionSceneRecorder(tmp_path)
    command = _command("req-ok")
    recorder.begin(command.request_id)
    recorder.record_frame(
        frozenset({command.request_id}),
        np.zeros((16, 16, 3), dtype=np.uint8),
        "2026-08-29T09:00:00.000Z",
    )
    saved = recorder.finish(command, {"status": "OK"})
    assert saved is None
    staging = tmp_path / ".staging" / command.request_id
    assert not staging.exists()


def test_recorder_ignores_non_image_pixels(tmp_path) -> None:
    recorder = RecognitionSceneRecorder(tmp_path)
    command = _command("req-dict")
    recorder.begin(command.request_id)
    recorder.record_frame(
        frozenset({command.request_id}),
        {"ready": True},
        "2026-08-29T09:00:00.000Z",
    )
    saved = recorder.finish(
        command,
        {"status": "FAILED", "errorCode": "DEADLINE_EXCEEDED", "errorMessage": "deadline"},
    )
    assert saved is not None
    assert list(saved.glob("frame_*.jpg")) == []
