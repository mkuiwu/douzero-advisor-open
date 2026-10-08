from __future__ import annotations

from pathlib import Path
from private_asset_paths import PRIVATE_SPIKES_ROOT

import cv2
import numpy as np
import pytest

from douzero_advisor.vision.diagnostics import FailureStage
from douzero_advisor.vision.hand_reader import VISUAL_RANKS
from douzero_advisor.recognition_service.runtime_assembly import build_live_observation_reader
from douzero_advisor.vision.table_reader import (
    TableReader,
    _independent_assignment,
    _locate_card_seams,
    _locate_group,
    REGIONS,
)
from test_hand_reader_live import CALIBRATION, load_frame


pytestmark = [pytest.mark.table, pytest.mark.private_assets]


def test_left_table_geometry_keeps_an_eight_card_airplane() -> None:
    """A 36-37 px live overlap must not cut the two airplane wings off."""
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    # Start at the fixed left-table anchor; 341 px is the observed apparent
    # width after the final card's folded-corner decoration is included.
    frame[334:440, 343:684] = 255

    geometry = _locate_group(frame, REGIONS["left_play"])

    assert geometry is not None
    edges, top = geometry
    assert len(edges) == 8
    assert top == 334


def test_card_seams_scan_the_whole_region_after_one_weak_boundary() -> None:
    """One weak divider must not truncate all clearly visible cards after it."""
    frame = np.full((819, 1455, 3), 30, dtype=np.uint8)
    top = REGIONS["left_play"].expected_top
    left_edges = tuple(343 + index * 32 for index in range(9))
    frame[top : top + 106, left_edges[0] : left_edges[-1] + 83] = 220

    scan_top = top + 51
    scan_bottom = top + 96
    for index, left in enumerate(left_edges):
        # A real anti-aliased divider can remain vertically continuous while
        # each individual pixel step is modest.  The third divider reproduces
        # that shape; all later dividers are deliberately strong.
        frame[scan_top:scan_bottom, left - 1] = 202 if index == 2 else 30
        frame[scan_top:scan_bottom, left] = 220

    geometry = _locate_card_seams(frame, REGIONS["left_play"])

    assert geometry is not None
    detected_edges, detected_top = geometry
    assert detected_edges == left_edges
    assert detected_top == top


OCR_FAILURE_ROOT = PRIVATE_SPIKES_ROOT / "ocr-failures"
LATEST_DEAL_RECORDING_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787059967518409800"
CURRENT_DEAL_RECORDING_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787068864691780100"
RANK_ONLY_REGRESSION_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787154894700781300"
NINE_CARD_GEOMETRY_REGRESSION_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787163276660790600"
LATEST_NINE_CARD_FAILURE_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787240493622616700"
LATEST_SINGLE_JOKER_FAILURE_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787243020440416500"
CURRENT_UNREADABLE_JOKER_ROOT = OCR_FAILURE_ROOT / (
    "1787068982140931100-left-play-snapshot_side_action_unreadable"
)
LATEST_ROCKET_FAILURE_ROOT = OCR_FAILURE_ROOT / (
    "1787338602957815700-left-play-snapshot_side_action_unreadable"
)
WRAPPED_TWELVE_STRAIGHT_FAILURE_ROOT = OCR_FAILURE_ROOT / (
    "1787485211881470000-right-play-snapshot_side_action_unreadable"
)


@pytest.mark.parametrize(
    "filename",
    (
        "frame_00_seq_008397.png",
        "frame_08_seq_008405.png",
        "frame_12_seq_008409.png",
    ),
)
def test_reads_wrapped_twelve_card_straight_from_recent_failure(
    production_reader: TableReader,
    filename: str,
) -> None:
    """A side-seat 3--A straight is rendered in two overlapping rows."""
    path = WRAPPED_TWELVE_STRAIGHT_FAILURE_ROOT / filename
    if not path.exists():
        pytest.skip(f"wrapped-straight replay is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = production_reader.read_region_with_diagnostics(frame, "right_play")

    assert read.value is not None
    assert read.value.cards("right_play") == (
        "A",
        "K",
        "Q",
        "J",
        "10",
        "9",
        "8",
        "7",
        "6",
        "5",
        "4",
        "3",
    )


def test_reads_recent_left_rocket_with_overlapped_corner_offsets(
    reader: TableReader,
) -> None:
    """The live left-table rocket has seam edges offset from both glyphs."""
    frames = (
        LATEST_ROCKET_FAILURE_ROOT / "frame_05_seq_001581.png",
        LATEST_ROCKET_FAILURE_ROOT / "frame_23_seq_001599.png",
    )
    for path in frames:
        if not path.exists():
            pytest.skip(f"latest rocket failure replay is unavailable: {path}")
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert frame is not None
        result = reader.read_region_with_diagnostics(frame, "left_play")
        assert result.value is not None
        assert result.value.cards("left_play") == ("D", "X")


@pytest.fixture(scope="module")
def reader() -> TableReader:
    """使用正式清单，确保回归也覆盖完整 JOKER 字样标定。"""
    manifest = Path(__file__).parent / "fixtures" / "live_capture_manifest.json"
    return build_live_observation_reader(manifest)._table_reader


@pytest.fixture(scope="module")
def production_reader() -> TableReader:
    manifest = Path(__file__).parent / "fixtures" / "live_capture_manifest.json"
    return build_live_observation_reader(manifest)._table_reader


@pytest.mark.parametrize(
    ("filename", "region", "expected"),
    (
        (
            "frame_0011_stable_after_change_0042.646.png",
            "right_play",
            ("A", "A"),
        ),
        (
            "frame_0021_stable_after_change_0068.864.png",
            "my_play",
            ("Q", "J", "10", "9", "8"),
        ),
        ("frame_0026_stable_after_change_0091.146.png", "left_play", ("J",)),
        ("frame_0031_stable_after_change_0104.177.png", "right_play", ("J",)),
    ),
)
def test_reads_real_table_cards(
    reader: TableReader,
    filename: str,
    region: str,
    expected: tuple[str, ...],
) -> None:
    result = reader.read(load_frame(filename))

    assert result.cards(region) == expected


def test_returns_empty_regions_before_gameplay(reader: TableReader) -> None:
    result = reader.read(load_frame("frame_0006_stable_after_change_0025.052.png"))

    assert result.cards("left_play") == ()
    assert result.cards("right_play") == ()
    assert result.cards("my_play") == ()


def test_diagnostics_preserves_successful_table_read(reader: TableReader) -> None:
    frame = load_frame("frame_0021_stable_after_change_0068.864.png")

    result = reader.read_with_diagnostics(frame)

    assert result.value == reader.read(frame)
    assert result.diagnostics.success is True
    assert result.diagnostics.failure_stage is None
    assert result.diagnostics.slots


def test_reads_only_the_expected_live_table_region(reader: TableReader) -> None:
    """An unrelated table region must not participate in action OCR."""
    frame = load_frame("frame_0021_stable_after_change_0068.864.png")

    result = reader.read_region_with_diagnostics(frame, "my_play")

    assert result.diagnostics.success is True
    assert result.value is not None
    assert result.value.detections == {"my_play": ("Q", "J", "10", "9", "8")}


def test_diagnostics_marks_missing_table_play_as_presence(reader: TableReader) -> None:
    result = reader.read_with_diagnostics(load_frame("frame_0006_stable_after_change_0025.052.png"))

    assert result.value == reader.read(load_frame("frame_0006_stable_after_change_0025.052.png"))
    assert all(slot.failure_stage is FailureStage.PRESENCE for slot in result.diagnostics.slots)


def test_table_assignment_keeps_repeated_threes_in_non_hand_order() -> None:
    """A table play such as 3 3 3 4 must not be rewritten as 8 8 8 4."""
    costs = np.ones((4, len(VISUAL_RANKS)), dtype=np.float64)
    costs[:3, VISUAL_RANKS.index("3")] = 0.01
    costs[3, VISUAL_RANKS.index("4")] = 0.01

    cards, assigned = _independent_assignment(costs)

    assert cards == ("3", "3", "3", "4")
    assert assigned == (0.01, 0.01, 0.01, 0.01)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        (
            "1786996292116830500-left-play-snapshot_missing_card_values",
            ("8", "8", "7", "7", "6", "6"),
        ),
        (
            "1786998208963849400-left-play-snapshot_missing_card_values",
            ("5", "5", "4", "4", "3", "3"),
        ),
    ),
)
def test_reads_stable_six_card_ocr_failures(
    reader: TableReader,
    case: str,
    expected: tuple[str, ...],
) -> None:
    path = OCR_FAILURE_ROOT / case / "frame_12_seq_001008.png"
    if not path.exists():
        alternatives = sorted((OCR_FAILURE_ROOT / case).glob("frame_12_*.png"))
        if not alternatives:
            pytest.skip(f"OCR failure corpus is unavailable: {case}")
        path = alternatives[0]
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    assert reader.read(frame).cards("left_play") == expected


def test_reads_both_overlapping_jacks_from_independent_rank_corners(
    reader: TableReader,
) -> None:
    """A covered lower card edge must not collapse two readable J corners."""
    path = LATEST_DEAL_RECORDING_ROOT / "frame_02446_seq_002447.jpg"
    if not path.exists():
        pytest.skip("pair-J live replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    assert reader.read(frame).cards("left_play") == ("J", "J")


def test_reads_both_twos_from_independent_rank_corners(
    reader: TableReader,
) -> None:
    """The old width-derived second slot misread this real pair as 2,8."""
    path = LATEST_DEAL_RECORDING_ROOT / "frame_00544_seq_000545.jpg"
    if not path.exists():
        pytest.skip("22-versus-28 live replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = reader.read(frame)

    assert read.cards("left_play") == ("2", "2")
    assert ("2", "8") in read.candidates("left_play")


def test_desktop_rank_crop_ignores_the_suit_below_a_black_two(
    production_reader: TableReader,
) -> None:
    """A club two must be classified by its rank, not by the club below it."""
    path = RANK_ONLY_REGRESSION_ROOT / "frame_00467_seq_000796.jpg"
    if not path.exists():
        pytest.skip("8882 rank-only regression replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = production_reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("8", "8", "8", "2")


def test_reads_dense_nine_card_straight_from_visible_rank_corners(
    reader: TableReader,
) -> None:
    """A 32 px dense straight must not collapse to eight coarse slots."""
    path = LATEST_DEAL_RECORDING_ROOT / "frame_00170_seq_000171.jpg"
    if not path.exists():
        pytest.skip("nine-card straight live replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == (
        "J", "10", "9", "8", "7", "6", "5", "4", "3"
    )


def test_vertical_card_seams_keep_the_current_dense_nine_card_straight(
    production_reader: TableReader,
) -> None:
    """Nine visible card faces must create nine slots before rank OCR runs."""
    path = NINE_CARD_GEOMETRY_REGRESSION_ROOT / "frame_03767_seq_006487.jpg"
    if not path.exists():
        pytest.skip("current nine-card geometry regression replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = production_reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == (
        "K", "Q", "J", "10", "9", "8", "7", "6", "5"
    )


def test_vertical_card_seams_keep_the_latest_nine_card_straight(
    production_reader: TableReader,
) -> None:
    """The latest live failure must retain every divider after Q and J."""
    path = LATEST_NINE_CARD_FAILURE_ROOT / "frame_03070_seq_005245.jpg"
    if not path.exists():
        pytest.skip("latest nine-card failure replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = production_reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == (
        "Q",
        "J",
        "10",
        "9",
        "8",
        "7",
        "6",
        "5",
        "4",
    )


def test_reads_green_face_small_joker_at_the_fixed_right_anchor(
    reader: TableReader,
) -> None:
    """未配置小王专用卡面证据时，主题小王不能仅凭固定锚点写入桌面牌历史。"""
    path = LATEST_DEAL_RECORDING_ROOT / "frame_01199_seq_001200.jpg"
    if not path.exists():
        pytest.skip("green-face small-joker replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = reader.read_region_with_diagnostics(frame, "right_play")

    assert read.value is not None
    assert read.value.cards("right_play") == ()


def test_reads_latest_left_big_joker_with_decorated_card_face(
    production_reader: TableReader,
) -> None:
    """A visible vertical JOKER must survive its slightly dark face artwork."""
    path = LATEST_SINGLE_JOKER_FAILURE_ROOT / "frame_08544_seq_014933.jpg"
    if not path.exists():
        pytest.skip("latest single-joker failure replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = production_reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("D",)


def test_reads_latest_left_big_joker_after_card_face_darkening(
    production_reader: TableReader,
) -> None:
    """JOKER structure, not a white-pixel quota, proves the visible card."""
    path = LATEST_SINGLE_JOKER_FAILURE_ROOT / "frame_08544_seq_014933.jpg"
    if not path.exists():
        pytest.skip("latest single-joker failure replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None
    # Preserve only the anchored card edge while darkening the interior skin.
    card_face = frame[328:443, 348:430]
    bright = np.min(card_face, axis=2) > 190
    card_face[bright] = 170

    read = production_reader.read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("D",)


@pytest.mark.parametrize(
    ("path", "region", "expected"),
    (
        (
            CURRENT_DEAL_RECORDING_ROOT / "frame_00126_seq_000127.jpg",
            "right_play",
            ("K",),
        ),
        (
            CURRENT_UNREADABLE_JOKER_ROOT / "frame_12_seq_000221.png",
            "left_play",
            ("X",),
        ),
        (
            CURRENT_DEAL_RECORDING_ROOT / "frame_00290_seq_000291.jpg",
            "right_play",
            ("8", "8", "8", "7", "7", "7", "6", "4"),
        ),
    ),
)
def test_reads_latest_static_table_regressions(
    reader: TableReader,
    path: Path,
    region: str,
    expected: tuple[str, ...],
) -> None:
    """Static geometry preserves one-card glyphs and the full long action."""
    if not path.exists():
        pytest.skip(f"latest static table replay is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    read = reader.read_region_with_diagnostics(frame, region)

    assert read.value is not None
    assert read.value.cards(region) == expected
