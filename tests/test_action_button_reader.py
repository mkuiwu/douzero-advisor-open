from __future__ import annotations

import cv2
import numpy as np
import pytest

from douzero_advisor.vision.action_button_reader import (
    ActionButtonGate,
    ActionButtonReader,
    ActionButtonStabilizer,
)


ORANGE = (80, 165, 252)
BLUE = (249, 135, 111)
TABLE = (175, 132, 105)


def _frame(
    *buttons: tuple[str, tuple[int, int, int, int]],
    timer_center: tuple[int, int] = (643, 523),
) -> np.ndarray:
    """Render the observed fixed-skin palette, including the round timer."""
    frame = np.empty((819, 1455, 3), dtype=np.uint8)
    frame[:] = TABLE
    cv2.circle(frame, timer_center, 33, ORANGE, thickness=-1)
    for color, (left, top, right, bottom) in buttons:
        cv2.rectangle(
            frame,
            (left, top),
            (right, bottom),
            ORANGE if color == "orange" else BLUE,
            thickness=-1,
        )
    return frame


def test_reader_ignores_round_timer_and_maps_three_buttons_by_visual_order() -> None:
    """Catches treating the countdown circle as a fourth/narrow button."""
    result = ActionButtonReader().read(
        _frame(
            ("orange", (436, 493, 575, 555)),
            ("blue", (710, 495, 849, 554)),
            ("blue", (880, 495, 1019, 554)),
        )
    )

    assert result.layout == "play_pass_hint"
    assert result.centers == {
        "play": (505, 524),
        "pass": (779, 524),
        "hint": (949, 524),
    }


def test_reader_maps_translated_two_button_layout_without_fixed_click_points() -> None:
    """Catches reusing stale centers when the whole control row shifts."""
    result = ActionButtonReader().read(
        _frame(
            ("orange", (524, 496, 663, 557)),
            ("blue", (798, 498, 937, 557)),
            timer_center=(728, 526),
        )
    )

    assert result.layout == "play_hint"
    assert result.centers == {
        "play": (593, 526),
        "hint": (867, 527),
    }


def test_reader_identifies_single_blue_yaobuqi_as_cannot_beat() -> None:
    """Catches conflating forced "要不起" with voluntary "不出"."""
    result = ActionButtonReader().read(
        _frame(("blue", (710, 494, 849, 554)))
    )

    assert result.layout == "cannot_beat_only"
    assert result.centers == {"cannot_beat": (779, 524)}


def test_reader_fails_closed_on_four_wide_controls_or_wrong_color_order() -> None:
    """Catches accepting bidding/doubling rows or unrelated colored panels."""
    reader = ActionButtonReader()
    four = reader.read(
        _frame(
            ("orange", (350, 493, 489, 555)),
            ("blue", (520, 495, 659, 554)),
            ("blue", (690, 495, 829, 554)),
            ("blue", (860, 495, 999, 554)),
        )
    )
    wrong_order = reader.read(
        _frame(
            ("blue", (521, 495, 660, 554)),
            ("orange", (795, 493, 934, 555)),
        )
    )

    assert four.layout == "unknown"
    assert four.centers == {}
    assert wrong_order.layout == "unknown"
    assert wrong_order.centers == {}


def test_stabilizer_waits_for_three_matching_observations() -> None:
    """Catches authorizing a transient animation frame as a click target."""
    reader = ActionButtonReader()
    stabilizer = ActionButtonStabilizer(required=3, coordinate_tolerance=4)
    read = reader.read(_frame(("blue", (710, 494, 849, 554))))

    assert stabilizer.observe(read).layout == "unknown"
    assert stabilizer.observe(read).layout == "unknown"
    assert stabilizer.observe(read) == read


def test_stabilizer_restarts_when_layout_changes() -> None:
    """Catches combining observations from two different button layouts."""
    reader = ActionButtonReader()
    stabilizer = ActionButtonStabilizer(required=3)
    single = reader.read(_frame(("blue", (710, 494, 849, 554))))
    double = reader.read(
        _frame(
            ("orange", (521, 493, 660, 554)),
            ("blue", (795, 495, 934, 554)),
        )
    )

    stabilizer.observe(single)
    stabilizer.observe(single)
    assert stabilizer.observe(double).layout == "unknown"
    assert stabilizer.observe(double).layout == "unknown"
    assert stabilizer.observe(double) == double


def test_gate_blocks_visually_identical_bidding_controls() -> None:
    """Catches treating a two-button bidding row as play-stage controls."""
    gate = ActionButtonGate()
    frame = _frame(
        ("orange", (521, 493, 660, 554)),
        ("blue", (795, 495, 934, 554)),
    )

    result = gate.observe(
        frame,
        phase="bottom_cards_reveal",
        my_turn_ready=True,
    )

    assert result.layout == "unknown"
    assert result.reason == "phase_not_playing"


def test_gate_requires_playing_turn_then_three_stable_frames() -> None:
    """Catches bypassing the existing local-turn gate or temporal gate."""
    gate = ActionButtonGate()
    frame = _frame(("blue", (710, 494, 849, 554)))

    not_ready = gate.observe(frame, phase="playing", my_turn_ready=False)
    first = gate.observe(frame, phase="playing", my_turn_ready=True)
    second = gate.observe(frame, phase="playing", my_turn_ready=True)
    third = gate.observe(frame, phase="playing", my_turn_ready=True)

    assert not_ready.reason == "local_turn_not_ready"
    assert first.layout == "unknown"
    assert second.layout == "unknown"
    assert third.layout == "cannot_beat_only"


def test_reader_rejects_empty_image_dimensions_before_opencv() -> None:
    """Catches leaking an opaque OpenCV split error for a malformed capture."""
    with pytest.raises(ValueError, match="non-empty"):
        ActionButtonReader().read(np.empty((0, 1455, 3), dtype=np.uint8))
