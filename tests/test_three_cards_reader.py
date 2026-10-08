from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from private_asset_paths import PRIVATE_SPIKES_ROOT

import cv2
import numpy as np
import pytest

from douzero_advisor.vision.diagnostics import FailureStage
from douzero_advisor.vision.hand_reader import _Template
from douzero_advisor.vision.three_cards_reader import (
    DEFAULT_THREE_CARDS_PROFILE,
    BottomTemplateCatalog,
    ThreeCardsReader,
    ThreeCardsProfile,
    _BottomGlyph,
    _locate_three_slots,
    _rank_distance_for_variant,
)


pytestmark = pytest.mark.bottom


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
CORE_ROOT = FIXTURE_ROOT / "core_cases" / "bottom"
CORE_MANIFEST = json.loads(
    (FIXTURE_ROOT / "core_bottom_cases.json").read_text(encoding="utf-8")
)
CORE_CALIBRATION = {
    Path(item["source"]).name: CORE_ROOT / item["path"]
    for item in CORE_MANIFEST["calibration"]
}
CORE_CASES = {item["name"]: CORE_ROOT / item["path"] for item in CORE_MANIFEST["cases"]}
SCENE_ROOT = PRIVATE_SPIKES_ROOT / "scene-sessions/scene-20260816-081732-3f4b1629"
J53_CALIBRATION = CORE_CALIBRATION["frame_0008_change_0008.047.png"]
J53_EVALUATION = CORE_CASES["development-j53"]
D210_CALIBRATION = CORE_CALIBRATION["frame_0081_change_0083.390.png"]
D210_EVALUATION = CORE_CASES["animated-d210"]
D210_LATE_ANIMATION = SCENE_ROOT / "frame_0137_stable_0151.515.png"
DEAL_RECORDING_ROOT = PRIVATE_SPIKES_ROOT / "deal-recordings"
D24_MULTIPLIER_OVERLAY = (
    DEAL_RECORDING_ROOT / "run-1787028385238197500" / "frame_00204_seq_000205.jpg"
)
KQQ_MULTIPLIER_OVERLAY = CORE_CALIBRATION["frame_20260818_kqq_doubling.jpg"]
X24_EVALUATION = DEAL_RECORDING_ROOT / "run-1787028385238197500" / "frame_00205_seq_000206.jpg"
LATE_TIMELINE_EVALUATIONS = (
    (
        DEAL_RECORDING_ROOT / "run-1787028385238197500" / "frame_00359_seq_000360.jpg",
        ("X", "2", "4"),
    ),
    (
        DEAL_RECORDING_ROOT / "run-1787028385238197500" / "frame_00408_seq_000409.jpg",
        ("X", "2", "4"),
    ),
)
LATE_RENDER_EVALUATIONS = (
    (CORE_CASES["late-k73"], ("K", "7", "3")),
    (CORE_CASES["late-a84"], ("A", "8", "4")),
    (
        DEAL_RECORDING_ROOT / "run-1787028385238197500" / "frame_00590_seq_000591.jpg",
        ("X", "2", "4"),
    ),
)
EXISTING_CALIBRATION = (
    (
        CORE_CALIBRATION["frame_0006_stable_after_change_0025.052.png"],
        ("2", "10", "9"),
    ),
    (
        CORE_CALIBRATION["frame_0092_stable_after_change_0296.010.png"],
        ("2", "K", "5"),
    ),
    (
        CORE_CALIBRATION["frame_0094_stable_after_change_0282.340.png"],
        ("A", "K", "5"),
    ),
)
CALIBRATION = tuple(
    (CORE_ROOT / item["path"], tuple(item["cards"]))
    for item in CORE_MANIFEST["calibration"]
)


def load_path(path: Path) -> np.ndarray:
    if not path.exists():
        pytest.skip(f"live bottom-card frame is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


def catalog(
    samples: Sequence[tuple[Path, tuple[str, str, str]]] = CALIBRATION,
) -> BottomTemplateCatalog:
    return BottomTemplateCatalog.from_labeled_frames(
        [(load_path(path), cards) for path, cards in samples]
    )


@pytest.fixture(scope="module")
def reader() -> ThreeCardsReader:
    return ThreeCardsReader(catalog())


def test_evaluation_frames_are_excluded_from_template_membership() -> None:
    """Catches leaking either declared evaluation PNG into the catalog."""
    calibration_paths = {path for path, _cards in CALIBRATION}

    assert J53_EVALUATION not in calibration_paths
    assert D210_EVALUATION not in calibration_paths
    assert len(catalog().templates["D"]) == 1


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (J53_EVALUATION, ("J", "5", "3")),
        (D210_EVALUATION, ("D", "2", "10")),
    ),
)
def test_reads_distinct_real_development_evaluation_frames(
    reader: ThreeCardsReader,
    path: Path,
    expected: tuple[str, str, str],
) -> None:
    """Catches losing confirmed J/5/3 or animated D/2/10 appearances."""
    result = reader.read(load_path(path))

    assert result is not None
    assert result.cards == expected
    assert result.maximum_distance <= 0.55


def test_ordered_fallback_never_changes_late_big_joker_to_jack(
    reader: ThreeCardsReader,
) -> None:
    """Catches a less-discriminative core silently overriding the upper D glyph."""
    result = reader.read(load_path(D210_LATE_ANIMATION))

    assert result is None or result.cards == ("D", "2", "10")


def test_named_variant_gaps_never_shift_into_another_representation() -> None:
    """Catches matching an upper sample as full after a failed variant is dropped."""
    mask = np.zeros((48, 48), dtype=np.uint8)
    mask[12:36, 18:30] = 1
    template = _Template(mask=mask, red_fraction=0.0)
    observed = _BottomGlyph(full=template, upper=None, core=None)
    sample = _BottomGlyph(full=None, upper=template, core=None)

    assert _rank_distance_for_variant(observed, (sample,), "3", "full") is None
    assert _rank_distance_for_variant(observed, (sample,), "3", "upper") is None


def test_diagnostics_marks_absent_wide_roi_as_presence(reader: ThreeCardsReader) -> None:
    """Catches reporting a missing bottom region as a slot or classifier failure."""
    result = reader.read_with_diagnostics(np.zeros((819, 1455, 3), dtype=np.uint8))

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.PRESENCE
    assert result.diagnostics.reason == "bottom_region_absent"
    assert reader.read(np.zeros((819, 1455, 3), dtype=np.uint8)) is None


def test_diagnostics_marks_two_card_group_as_geometry(reader: ThreeCardsReader) -> None:
    """Catches accepting fewer than exactly three ordered bottom slots."""
    frame = load_path(J53_EVALUATION).copy()
    frame[60:115, 735:780] = frame[60:115, 630:675]

    result = reader.read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.GEOMETRY
    assert result.diagnostics.reason == "exactly_three_slots_not_found"


def test_diagnostics_marks_empty_middle_rank_as_glyph_extraction(
    reader: ThreeCardsReader,
) -> None:
    """Catches collapsing an empty located slot into bottom-card absence."""
    frame = load_path(J53_EVALUATION).copy()
    frame[70:99, 713:733] = 255

    result = reader.read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.GLYPH_EXTRACTION
    assert result.diagnostics.slots[1].failure_stage is FailureStage.GLYPH_EXTRACTION


def test_diagnostics_marks_unknown_glyph_as_low_confidence(
    reader: ThreeCardsReader,
) -> None:
    """Catches accepting an unrelated high-contrast glyph as a known rank."""
    frame = load_path(J53_EVALUATION).copy()
    frame[70:99, 691:711] = 255
    cv2.line(frame, (693, 72), (709, 97), (0, 0, 0), thickness=2)
    cv2.line(frame, (709, 72), (693, 97), (0, 0, 0), thickness=2)

    result = reader.read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.CLASSIFICATION
    assert result.diagnostics.reason == "classification_distance_exceeded"


def test_partial_catalog_exposes_coverage_in_canonical_order() -> None:
    """Catches treating a valid partial bottom catalog as a hand-catalog error."""
    partial = catalog(((J53_CALIBRATION, ("J", "5", "3")),))

    assert partial.available_ranks == ("J", "5", "3")
    assert partial.missing_ranks == (
        "D",
        "X",
        "2",
        "A",
        "K",
        "Q",
        "10",
        "9",
        "8",
        "7",
        "6",
        "4",
    )


def test_partial_catalog_exposes_coverage_without_inferring_the_hidden_rank() -> None:
    """Catches guessing template_missing from partial coverage alone."""
    partial = catalog(((J53_CALIBRATION, ("J", "5", "3")),))
    partial_reader = ThreeCardsReader(partial)

    result = partial_reader.read_with_diagnostics(load_path(D210_EVALUATION))

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.CLASSIFICATION
    assert result.diagnostics.reason == "classification_distance_exceeded"
    assert {"D", "2", "10"}.issubset(partial.missing_ranks)


def test_empty_catalog_reports_template_missing_without_guessing() -> None:
    """Catches losing the explicit no-usable-template diagnostic."""
    result = ThreeCardsReader(BottomTemplateCatalog({})).read_with_diagnostics(
        load_path(J53_EVALUATION)
    )

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.CLASSIFICATION
    assert result.diagnostics.reason == "template_missing"


def test_missing_template_failure_preserves_covered_slot_assignment() -> None:
    """Catches erasing valid covered-slot evidence when another rank is missing."""
    partial_reader = ThreeCardsReader(catalog(((J53_CALIBRATION, ("J", "5", "3")),)))

    result = partial_reader.read_with_diagnostics(load_path(EXISTING_CALIBRATION[2][0]))

    assert result.value is None
    assert result.diagnostics.reason == "classification_distance_exceeded"
    assert result.diagnostics.slots[2].assigned_candidate == "5"
    assert result.diagnostics.slots[2].failure_stage is None


@pytest.mark.parametrize(("dx", "dy"), ((-3, -2), (3, 2)))
def test_wide_roi_localizes_small_horizontal_and_vertical_shifts(
    reader: ThreeCardsReader,
    dx: int,
    dy: int,
) -> None:
    """Catches regressing from wide-ROI localization to fixed pixel crops."""
    frame = load_path(J53_EVALUATION)
    baseline = reader.read_with_diagnostics(frame)
    shifted = cv2.warpAffine(
        frame,
        np.float32(((1, 0, dx), (0, 1, dy))),
        (1455, 819),
    )

    result = reader.read_with_diagnostics(shifted)

    assert result.value is not None
    assert result.value.cards == ("J", "5", "3")
    assert baseline.value is not None
    for baseline_slot, shifted_slot in zip(
        baseline.diagnostics.slots,
        result.diagnostics.slots,
        strict=True,
    ):
        assert baseline_slot.box is not None
        assert shifted_slot.box is not None
        assert shifted_slot.box[0] == pytest.approx(baseline_slot.box[0] + dx, abs=1)
        assert shifted_slot.box[1] == pytest.approx(baseline_slot.box[1] + dy, abs=1)
        assert shifted_slot.box[2:] == baseline_slot.box[2:]


@pytest.mark.parametrize(
    "path",
    (D24_MULTIPLIER_OVERLAY, KQQ_MULTIPLIER_OVERLAY),
    ids=("joker-double", "ordinary-double"),
)
def test_left_corner_geometry_survives_multiplier_overlay(path: Path) -> None:
    """Catches a lower-card ribbon hiding otherwise readable rank corners."""
    boxes = _locate_three_slots(load_path(path), DEFAULT_THREE_CARDS_PROFILE)

    assert boxes is not None
    assert tuple(box[0] for box in boxes) == (692, 713, 735)


def test_reads_special_straight_overlay_from_upper_rank_corners(
    reader: ThreeCardsReader,
) -> None:
    """Catches a lower-rank ribbon making full-card variants prefer K/10/Q."""
    result = reader.read(load_path(CORE_CASES["akq-play-overlay"]))

    assert result is not None
    assert result.cards == ("A", "K", "Q")


def test_reads_held_out_small_joker_rank_corner_frame(reader: ThreeCardsReader) -> None:
    """Catches a missing rank template rejecting a clean independent frame."""
    result = reader.read(load_path(X24_EVALUATION))

    assert result is not None
    assert result.cards == ("X", "2", "4")


@pytest.mark.parametrize(("path", "expected"), LATE_TIMELINE_EVALUATIONS)
def test_reads_faded_rank_corners_late_in_real_deals(
    reader: ThreeCardsReader,
    path: Path,
    expected: tuple[str, str, str],
) -> None:
    """Catches absolute thresholds losing still-readable pale rank corners."""
    result = reader.read(load_path(path))

    assert result is not None
    assert result.cards == expected


@pytest.mark.parametrize(("path", "expected"), LATE_RENDER_EVALUATIONS)
def test_reads_held_out_late_render_states(
    reader: ThreeCardsReader,
    path: Path,
    expected: tuple[str, str, str],
) -> None:
    """Catches losing deal-play and settlement render variants."""
    result = reader.read(load_path(path))

    assert result is not None
    assert result.cards == expected


def test_single_long_stripe_is_not_three_observed_card_edges(
    reader: ThreeCardsReader,
) -> None:
    """Catches synthesizing three slots from one outer white rectangle."""
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[67:70, 691:764] = 255

    result = reader.read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.GEOMETRY
    assert result.diagnostics.reason == "exactly_three_slots_not_found"


@pytest.mark.parametrize(
    "profile",
    (
        ThreeCardsProfile((650, 45, 805, 125), (30, 31), (38, 42), (20, 24)),
        ThreeCardsProfile((650, 45, 805, 125), (27, 31), (35, 37), (20, 24)),
        ThreeCardsProfile((650, 45, 805, 125), (27, 31), (38, 42), (21, 21)),
    ),
    ids=("measured-width", "measured-height", "measured-pitches"),
)
def test_geometry_rejects_measured_features_outside_profile(
    profile: ThreeCardsProfile,
) -> None:
    """Catches fitting synthetic dimensions instead of validating observations."""
    result = ThreeCardsReader(catalog(), profile).read_with_diagnostics(load_path(J53_EVALUATION))

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.GEOMETRY


def test_presence_uses_the_supplied_profile_width() -> None:
    """Catches consulting DEFAULT profile geometry for a custom reader."""
    frame = np.zeros((819, 1455, 3), dtype=np.uint8)
    frame[60:63, 700:730] = 255
    profile = ThreeCardsProfile((650, 45, 805, 125), (40, 45), (38, 42), (20, 24))

    result = ThreeCardsReader(catalog(), profile).read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.PRESENCE


def test_each_classified_slot_exposes_stable_top_three_and_margin(
    reader: ThreeCardsReader,
) -> None:
    """Catches diagnostics dropping rank alternatives needed for replay audits."""
    result = reader.read_with_diagnostics(load_path(J53_EVALUATION))

    assert result.value is not None
    assert len(result.diagnostics.slots) == 3
    assert all(1 <= len(slot.candidates) <= 3 for slot in result.diagnostics.slots)
    assert all(slot.margin is not None for slot in result.diagnostics.slots)
    assert tuple(slot.assigned_candidate for slot in result.diagnostics.slots) == (
        "J",
        "5",
        "3",
    )


def test_invalid_three_card_multiset_fails_rule_validation(
    reader: ThreeCardsReader,
) -> None:
    """Catches emitting more Joker copies than exist in a legal deck."""
    frame = load_path(D210_EVALUATION).copy()
    frame[70:99, 713:733] = frame[70:99, 691:711]
    frame[70:99, 735:755] = frame[70:99, 691:711]

    result = reader.read_with_diagnostics(frame)

    assert result.value is None
    assert result.diagnostics.failure_stage is FailureStage.RULE_VALIDATION
    assert result.diagnostics.reason == "invalid_bottom_card_multiset"


def test_invalid_shape_remains_a_value_error(reader: ThreeCardsReader) -> None:
    """Catches mapping caller contract violations to legal-frame absence."""
    with pytest.raises(ValueError, match="1455x819"):
        reader.read(np.zeros((818, 1455, 3), dtype=np.uint8))
