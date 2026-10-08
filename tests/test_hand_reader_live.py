from __future__ import annotations

from pathlib import Path
from private_asset_paths import PRIVATE_SPIKES_ROOT

import cv2
import numpy as np
import pytest

from douzero_advisor.vision.diagnostics import FailureStage
from douzero_advisor.vision import hand_reader
from douzero_advisor.vision.hand_reader import HandReader, HandTemplateCatalog, _locate_geometry


pytestmark = pytest.mark.hand


CAPTURES = PRIVATE_SPIKES_ROOT / "wgc/captures/game-20260815-223120"
CURRENT_DEAL = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787028385238197500"
LATEST_REPLAY = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787059967518409800"
JOKER_REGRESSION_DEAL = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787102733960269900"
CURRENT_X_HAND = (
    "X",
    "2",
    "2",
    "2",
    "2",
    "A",
    "A",
    "K",
    "Q",
    "J",
    "J",
    "J",
    "9",
    "9",
    "8",
    "7",
    "5",
    "5",
    "4",
    "4",
)
CALIBRATION = (
    (
        "frame_0006_stable_after_change_0025.052.png",
        (
            "D",
            "X",
            "2",
            "A",
            "K",
            "K",
            "Q",
            "J",
            "10",
            "10",
            "10",
            "9",
            "9",
            "8",
            "8",
            "7",
            "6",
            "5",
            "5",
            "3",
        ),
    ),
    (
        "frame_0051_stable_after_change_0158.646.png",
        (
            "D",
            "X",
            "2",
            "2",
            "A",
            "K",
            "K",
            "Q",
            "Q",
            "Q",
            "J",
            "10",
            "10",
            "9",
            "9",
            "9",
            "7",
            "6",
            "5",
            "4",
        ),
    ),
    (CURRENT_DEAL / "frame_00204_seq_000205.jpg", CURRENT_X_HAND),
)
HOLDOUTS = (
    (
        "frame_0026_stable_after_change_0091.146.png",
        ("D", "X", "2", "K", "K", "10"),
    ),
    ("frame_0031_stable_after_change_0104.177.png", ("D", "2", "K", "K")),
    ("frame_0036_stable_after_change_0114.458.png", ("K", "K")),
    (
        "frame_0056_stable_after_change_0173.551.png",
        (
            "D",
            "X",
            "2",
            "2",
            "A",
            "K",
            "K",
            "Q",
            "Q",
            "Q",
            "J",
            "10",
            "10",
            "7",
            "6",
            "5",
        ),
    ),
    (
        "frame_0094_stable_after_change_0288.927.png",
        (
            "D",
            "X",
            "A",
            "A",
            "K",
            "K",
            "K",
            "Q",
            "J",
            "10",
            "9",
            "8",
            "5",
            "4",
            "4",
            "3",
            "3",
        ),
    ),
    (
        "frame_0098_stable_after_change_0299.458.png",
        (
            "D",
            "X",
            "A",
            "A",
            "K",
            "K",
            "K",
            "Q",
            "Q",
            "J",
            "J",
            "10",
            "9",
            "8",
            "7",
            "5",
            "4",
            "4",
            "3",
            "3",
        ),
    ),
    (CURRENT_DEAL / "frame_00209_seq_000210.jpg", CURRENT_X_HAND),
    (
        LATEST_REPLAY / "frame_00639_seq_000640.jpg",
        ("2", "2", "A", "K", "K", "Q", "Q", "Q", "10"),
    ),
    (
        LATEST_REPLAY / "frame_01317_seq_001318.jpg",
        ("2", "A", "K", "K", "Q", "Q", "Q", "J", "J"),
    ),
    (
        LATEST_REPLAY / "frame_01746_seq_001747.jpg",
        (
            "2",
            "2",
            "A",
            "K",
            "K",
            "Q",
            "J",
            "J",
            "10",
            "9",
            "9",
            "9",
            "7",
            "7",
            "6",
            "4",
            "4",
            "4",
            "3",
            "3",
        ),
    ),
    (
        LATEST_REPLAY / "frame_02088_seq_002089.jpg",
        (
            "D",
            "X",
            "2",
            "A",
            "A",
            "A",
            "K",
            "K",
            "Q",
            "Q",
            "Q",
            "J",
            "9",
            "8",
            "7",
            "6",
            "4",
            "4",
            "3",
            "3",
        ),
    ),
    (
        LATEST_REPLAY / "frame_02319_seq_002320.jpg",
        ("D", "X", "3", "3"),
    ),
)


def load_frame(filename: str | Path) -> np.ndarray:
    path = CAPTURES / filename
    if not path.exists():
        pytest.skip(f"live calibration frame is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


@pytest.fixture(scope="module")
def reader() -> HandReader:
    samples = [(load_frame(filename), cards) for filename, cards in CALIBRATION]
    return HandReader(HandTemplateCatalog.from_labeled_frames(samples))


@pytest.mark.parametrize(("filename", "expected"), HOLDOUTS)
def test_reads_real_holdout_hands(
    reader: HandReader, filename: str | Path, expected: tuple[str, ...]
) -> None:
    result = reader.read(load_frame(filename))

    assert result is not None
    assert result.cards == expected
    assert len(result.card_left_edges) == len(expected)


@pytest.mark.parametrize(
    "filename",
    (
        JOKER_REGRESSION_DEAL / "frame_01220_seq_001221.jpg",
        JOKER_REGRESSION_DEAL / "frame_01354_seq_001355.jpg",
    ),
)
def test_reads_black_joker_as_small_joker_before_and_after_hand_relayout(
    reader: HandReader, filename: Path
) -> None:
    result = reader.read(load_frame(filename))

    assert result is not None
    assert result.cards[0] == "X"


def test_returns_none_when_no_hand_is_visible(reader: HandReader) -> None:
    assert reader.read(load_frame("frame_0001_initial_0000.148.png")) is None


def test_diagnostics_preserves_successful_hand_read(reader: HandReader) -> None:
    frame = load_frame("frame_0026_stable_after_change_0091.146.png")

    result = reader.read_with_diagnostics(frame)

    assert result.value == reader.read(frame)
    assert result.diagnostics.success is True
    assert result.diagnostics.failure_stage is None
    assert all(slot.raw_candidate is not None for slot in result.diagnostics.slots)


def test_diagnostics_marks_missing_hand_geometry(reader: HandReader) -> None:
    result = reader.read_with_diagnostics(load_frame("frame_0001_initial_0000.148.png"))

    assert result.value is None
    assert result.diagnostics.success is False
    assert result.diagnostics.failure_stage is FailureStage.GEOMETRY


def test_rejects_non_capture_resolution(reader: HandReader) -> None:
    frame = load_frame("frame_0006_stable_after_change_0025.052.png")

    with pytest.raises(ValueError, match="1455x819"):
        reader.read(frame[:-1])


def test_hand_geometry_allows_independently_raised_cards() -> None:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[574:704, 500:623] = 255
    frame[574:584, 547:670] = 0
    frame[584:714, 547:670] = 255

    geometry = _locate_geometry(frame)

    assert geometry is not None
    left_edges, tops = geometry
    assert left_edges == (500, 547)
    assert tops == (574, 584)


def test_independent_rank_tokens_do_not_derive_count_from_extra_white_width() -> None:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    # A multiplier/selection glow can join the hand's white face and make the
    # broad run look like five cards even though only two rank corners exist.
    frame[600:720, 500:800] = 255
    frame[610:634, 513:525] = 0
    frame[610:634, 560:572] = 0
    legacy = _locate_geometry(frame)
    assert legacy is not None
    assert len(legacy[0]) == 5

    locate = getattr(hand_reader, "_locate_rank_tokens", None)
    assert locate is not None, "independent rank-corner locator is missing"
    assert locate(frame) == ((513, 600), (560, 600))


def test_independent_rank_tokens_keep_raised_and_unraised_cards_separate() -> None:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[584:704, 500:623] = 255
    frame[600:720, 547:800] = 255
    frame[594:618, 513:525] = 0
    frame[610:634, 560:572] = 0

    locate = getattr(hand_reader, "_locate_rank_tokens", None)
    assert locate is not None, "independent rank-corner locator is missing"
    assert locate(frame) == ((513, 584), (560, 600))


def test_independent_rank_tokens_merge_the_two_glyphs_of_ten() -> None:
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[600:720, 500:623] = 255
    frame[610:634, 509:519] = 0
    frame[610:634, 522:544] = 0

    locate = getattr(hand_reader, "_locate_rank_tokens", None)
    assert locate is not None, "independent rank-corner locator is missing"
    assert locate(frame) == ((509, 600),)


def test_reader_keeps_real_hand_when_white_face_run_is_contaminated(
    reader: HandReader,
) -> None:
    frame = load_frame("frame_0026_stable_after_change_0091.146.png").copy()
    # The real six-card hand ends at x=903.  Extending only its bright face
    # simulates a neighbouring selection/multiplier glow without adding ranks.
    frame[600:720, 903:1043] = 255

    result = reader.read_with_diagnostics(frame)

    assert result.value is not None
    assert result.value.cards == ("D", "X", "2", "K", "K", "10")
