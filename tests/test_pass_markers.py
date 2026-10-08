from __future__ import annotations

from pathlib import Path
from private_asset_paths import PRIVATE_SPIKES_ROOT

import cv2
import numpy as np
import pytest

from douzero_advisor.vision.live_markers import PassMarkerCatalog, PassMarkerReader


CAPTURES = PRIVATE_SPIKES_ROOT / "wgc/captures/game-20260815-233322"
MY_FRAME = "frame_0040_stable_after_change_0128.854.png"
OPPONENT_FRAME = "frame_0080_stable_after_change_0257.198.png"
CURRENT_LEFT_PASS_FRAME = PRIVATE_SPIKES_ROOT / "ocr-failures/1787479013546123100-left-play-snapshot_side_action_unreadable/frame_08_seq_000631.png"
MY_BOX = (672, 461, 783, 534)
LEFT_BOX = (376, 377, 488, 430)
RIGHT_BOX = (968, 377, 1080, 430)


def load(name: str) -> np.ndarray:
    path = CAPTURES / name
    if not path.exists():
        pytest.skip(f"live calibration frame is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


def reader() -> PassMarkerReader:
    my_frame = load(MY_FRAME)
    opponent_frame = load(OPPONENT_FRAME)
    return PassMarkerReader(
        PassMarkerCatalog.from_labeled_frames(
            (
                (my_frame, "my_pass", MY_BOX),
                (opponent_frame, "left_pass", LEFT_BOX),
                (opponent_frame, "right_pass", RIGHT_BOX),
            )
        )
    )


def test_detects_real_self_pass_marker() -> None:
    result = reader().read(load(MY_FRAME))

    assert result is not None
    assert result.markers == frozenset({"my_pass"})


def test_detects_simultaneously_visible_opponent_pass_markers() -> None:
    result = reader().read(load(OPPONENT_FRAME))

    assert result is not None
    assert result.markers == frozenset({"left_pass", "right_pass"})


def test_detects_current_theme_left_pass_near_the_previous_threshold() -> None:
    """Keep the 0.841 left-side marker from regressing into a turn deadlock."""
    if not CURRENT_LEFT_PASS_FRAME.exists():
        pytest.skip(f"retained regression frame is unavailable: {CURRENT_LEFT_PASS_FRAME}")
    frame = cv2.imread(str(CURRENT_LEFT_PASS_FRAME), cv2.IMREAD_COLOR)
    assert frame is not None

    result = reader().read(frame)

    assert result is not None
    assert {"left_pass", "right_pass"}.issubset(result.markers)


def test_returns_none_without_pass_text() -> None:
    result = reader().read(np.zeros((819, 1455, 3), dtype=np.uint8))

    assert result is None
