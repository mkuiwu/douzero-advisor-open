from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from douzero_advisor.recognition_service.runtime_assembly import (
    build_live_observation_reader,
)
from douzero_advisor.vision.turn_ready_reader import has_local_turn_indicator

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.private_assets


def test_runtime_uses_only_the_pass_reader_for_live_markers_and_actions() -> None:
    """Role-badge matching is not part of the live observation path."""
    reader = build_live_observation_reader(
        ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    )

    assert reader._marker_reader is reader._action_result_reader._pass_reader


def test_runtime_reads_bottom_cards_through_the_doubling_overlay() -> None:
    """A multiplier overlay must not remove the Q template or bottom geometry."""
    manifest_path = ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample = next(
        item
        for item in manifest["three_cards_calibration"]
        if item["session_id"] == "bottom-calibration"
    )
    frame = cv2.imread(str(Path(manifest["capture_root"]) / sample["path"]), cv2.IMREAD_COLOR)
    reader = build_live_observation_reader(manifest_path)

    result = reader._three_cards_reader.read_with_diagnostics(frame)

    assert result.value is not None
    assert result.value.cards == ("K", "Q", "Q")


def test_runtime_loads_the_optional_local_turn_affordance_gate() -> None:
    manifest_path = ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reader = build_live_observation_reader(manifest_path)
    settlement = next(
        item
        for item in manifest["turn_ready_calibration"]
        if item["region"] == "my_play" and item["category"] == "settlement"
    )
    ready = next(
        item
        for item in manifest["turn_ready_calibration"]
        if item["region"] == "my_play" and item["category"] == "ready"
    )
    turn_ready_root = Path(manifest["turn_ready_root"])
    if not turn_ready_root.is_absolute():
        turn_ready_root = ROOT / turn_ready_root
    settlement_frame = cv2.imread(
        str(turn_ready_root / settlement["path"]),
        cv2.IMREAD_COLOR,
    )
    ready_frame = cv2.imread(
        str(turn_ready_root / ready["path"]),
        cv2.IMREAD_COLOR,
    )

    assert reader._turn_ready_reader is not None
    assert reader.is_my_turn_ready(settlement_frame) is False
    assert reader.is_my_turn_ready(ready_frame) is True


def test_calibrated_turn_gate_rejects_an_orange_non_play_control() -> None:
    """Catches a doubling button bypassing the calibrated play-stage decision."""
    reader = build_live_observation_reader(
        ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    )
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[500:540, 620:660] = (0, 128, 255)

    assert has_local_turn_indicator(frame) is True
    assert reader._turn_ready_reader.is_ready(frame, "my_play") is False
    assert reader.is_my_turn_ready(frame) is False


def test_self_turn_snapshot_reads_only_the_two_opponent_table_regions() -> None:
    """Our previous table play is recovered from hand delta, not table OCR."""
    manifest_path = ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample = next(
        item
        for item in manifest["table_calibration"]
        if item["region"] == "my_play"
    )
    frame = cv2.imread(
        str(Path(manifest["capture_root"]) / sample["path"]),
        cv2.IMREAD_COLOR,
    )
    reader = build_live_observation_reader(manifest_path)

    snapshot = reader.read_self_turn_snapshot(frame)

    assert snapshot.table.value is not None
    assert set(snapshot.table.value.detections) == {"left_play", "right_play"}


def test_runtime_builds_without_turn_ready_manifest_fields(tmp_path: Path) -> None:
    """Catches production runtime loading retired TurnReady manifest evidence."""
    manifest_path = ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in (
        "turn_ready_root",
        "turn_ready_artifact_sha256",
        "turn_ready_calibration",
    ):
        manifest.pop(field)
    no_turn_ready_manifest = tmp_path / "live-capture-manifest.json"
    no_turn_ready_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    reader = build_live_observation_reader(no_turn_ready_manifest)

    assert reader is not None
