from __future__ import annotations

import numpy as np
import pytest

from douzero_advisor.vision.turn_ready_reader import (
    TURN_READY_ROIS,
    TurnReadyCalibrationSample,
    TurnReadyReader,
    has_local_turn_indicator,
)


def _frame(region: str, value: int) -> np.ndarray:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    left, top, right, bottom = TURN_READY_ROIS[region]
    frame[top:bottom, left:right] = value
    return frame


def _sample(
    region: str,
    value: int,
    *,
    ready: bool,
    group: str,
    skin: str = "gold",
) -> TurnReadyCalibrationSample:
    return TurnReadyCalibrationSample(
        frame=_frame(region, value),
        region=region,
        ready=ready,
        group=group,
        skin=skin,
    )


def test_loso_calibration_derives_both_runtime_gates_from_groups() -> None:
    """Catches replacing grouped calibration with a hand-picked threshold."""
    reader = TurnReadyReader.from_labeled_frames(
        (
            _sample("right_play", 20, ready=True, group="positive-a"),
            _sample("right_play", 30, ready=True, group="positive-b"),
            _sample("right_play", 200, ready=False, group="negative-a"),
            _sample("right_play", 210, ready=False, group="negative-b"),
        )
    )

    calibration = reader.calibrations["right_play"]

    assert calibration.positive_loso_distance == pytest.approx(10 / 255)
    assert calibration.nearest_negative_distance == pytest.approx(170 / 255)
    assert calibration.maximum_distance == pytest.approx(90 / 255)
    assert calibration.minimum_margin == pytest.approx(80 / 255)
    assert reader.is_ready(_frame("right_play", 20), "right_play") is True


def test_runtime_requires_distance_and_negative_margin_gates() -> None:
    """Catches an in-between overlay being accepted by nearest-positive distance alone."""
    reader = TurnReadyReader.from_labeled_frames(
        (
            _sample("right_play", 20, ready=True, group="positive-a"),
            _sample("right_play", 30, ready=True, group="positive-b"),
            _sample("right_play", 200, ready=False, group="negative-a"),
            _sample("right_play", 210, ready=False, group="negative-b"),
        )
    )

    assert reader.is_ready(_frame("right_play", 115), "right_play") is False
    assert reader.is_ready(_frame("left_play", 20), "left_play") is False


def test_local_turn_indicator_uses_the_large_orange_countdown_not_templates() -> None:
    """The current client skin keeps this local timer stable across UI art changes."""
    frame = _frame("my_play", 0)
    frame[500:540, 620:660] = (0, 128, 255)

    assert has_local_turn_indicator(frame) is True
    assert has_local_turn_indicator(_frame("my_play", 0)) is False


def test_decision_diagnostics_include_both_distances_gates_and_hashes() -> None:
    """Catches an opaque READY decision without calibration provenance."""
    reader = TurnReadyReader.from_labeled_frames(
        (
            _sample("right_play", 20, ready=True, group="positive-a"),
            _sample("right_play", 30, ready=True, group="positive-b"),
            _sample("right_play", 200, ready=False, group="negative-a"),
            _sample("right_play", 210, ready=False, group="negative-b"),
        ),
        artifact_sha256="a" * 64,
        config_sha256="b" * 64,
    )

    decision = reader.classify(_frame("right_play", 20), "right_play")

    assert decision.decision == "READY"
    assert decision.positive_distance == pytest.approx(0.0)
    assert decision.negative_distance == pytest.approx(180 / 255)
    assert decision.maximum_distance == pytest.approx(90 / 255)
    assert decision.minimum_margin == pytest.approx(80 / 255)
    assert decision.observed_margin == pytest.approx(180 / 255)
    assert decision.artifact_sha256 == "a" * 64
    assert decision.config_sha256 == "b" * 64


def test_missing_independent_positive_group_disables_only_that_region() -> None:
    """Catches a same-frame duplicate being treated as independent calibration."""
    reader = TurnReadyReader.from_labeled_frames(
        (
            _sample("left_play", 20, ready=True, group="same-group"),
            _sample("left_play", 25, ready=True, group="same-group"),
            _sample("left_play", 200, ready=False, group="negative-a"),
            _sample("left_play", 210, ready=False, group="negative-b"),
            _sample("right_play", 20, ready=True, group="positive-a"),
            _sample("right_play", 30, ready=True, group="positive-b"),
            _sample("right_play", 200, ready=False, group="negative-a"),
            _sample("right_play", 210, ready=False, group="negative-b"),
        )
    )

    assert "left_play" not in reader.calibrations
    assert reader.is_ready(_frame("left_play", 20), "left_play") is False
    assert "right_play" in reader.calibrations


def test_missing_same_skin_group_or_nonseparable_distances_fail_closed() -> None:
    """Catches cross-skin positives or overlapping negatives enabling a seat."""
    missing_same_skin = TurnReadyReader.from_labeled_frames(
        (
            _sample("my_play", 20, ready=True, group="gold-a", skin="gold"),
            _sample("my_play", 30, ready=True, group="red-a", skin="red"),
            _sample("my_play", 200, ready=False, group="gold-negative", skin="gold"),
            _sample("my_play", 210, ready=False, group="red-negative", skin="red"),
        )
    )
    overlapping = TurnReadyReader.from_labeled_frames(
        (
            _sample("left_play", 20, ready=True, group="positive-a"),
            _sample("left_play", 200, ready=True, group="positive-b"),
            _sample("left_play", 100, ready=False, group="negative-a"),
            _sample("left_play", 110, ready=False, group="negative-b"),
        )
    )

    assert "my_play" not in missing_same_skin.calibrations
    assert "left_play" not in overlapping.calibrations


def test_missing_independent_negative_group_disables_only_that_region() -> None:
    """Catches self-matching one negative template passing held-out validation."""
    reader = TurnReadyReader.from_labeled_frames(
        (
            _sample("left_play", 20, ready=True, group="left-positive-a"),
            _sample("left_play", 30, ready=True, group="left-positive-b"),
            _sample("left_play", 200, ready=False, group="left-negative"),
            _sample("right_play", 20, ready=True, group="right-positive-a"),
            _sample("right_play", 30, ready=True, group="right-positive-b"),
            _sample("right_play", 200, ready=False, group="right-negative-a"),
            _sample("right_play", 210, ready=False, group="right-negative-b"),
        )
    )

    assert "left_play" not in reader.calibrations
    assert "right_play" in reader.calibrations


def test_calibration_rejects_unknown_regions_and_bad_metadata() -> None:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="region"):
        TurnReadyCalibrationSample(frame, "unknown", True, "group", "gold")
    with pytest.raises(ValueError, match="group"):
        TurnReadyCalibrationSample(frame, "left_play", True, "", "gold")
    with pytest.raises(ValueError, match="skin"):
        TurnReadyCalibrationSample(frame, "left_play", True, "group", "")
