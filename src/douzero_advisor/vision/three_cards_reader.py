from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

import cv2
import numpy as np

from douzero_advisor.vision.diagnostics import (
    FailureStage,
    ReaderDiagnostics,
    ReaderResult,
    SlotDiagnostics,
)
from douzero_advisor.vision.hand_reader import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    RANKS,
    HandReadError,
    _Template,
    _normalize_mask,
    _rank_distance,
)
from douzero_advisor.vision.live_observation import RegionCardsRead


MAXIMUM_DISTANCE = 0.55
_CAPACITY_BY_RANK = {rank: (1 if rank in {"D", "X"} else 4) for rank in RANKS}
# Bottom ranks occupy the upper-left 12x16 pixels of each localized card.
# Keep every representation above the suit so ordinary ranks are compared by
# their glyph shape rather than by an accidental rank+suit composite.
_RANK_FULL_BOUNDS = (0, 2, 12, 18)
_RANK_UPPER_BOUNDS = (0, 4, 12, 18)
_RANK_CORE_BOUNDS = (0, 4, 12, 16)
_RANK_VARIANTS = ("full", "upper", "core")
_CANONICAL_LEFT_CORNER_OFFSETS = (0, 21, 43)


@dataclass(frozen=True, slots=True)
class ThreeCardsProfile:
    """Canonical wide search area and visible bottom-card geometry."""

    roi: tuple[int, int, int, int]
    card_width_range: tuple[int, int]
    card_height_range: tuple[int, int]
    spacing_range: tuple[int, int]

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = self.roi
        if not (0 <= x0 < x1 <= FRAME_WIDTH and 0 <= y0 < y1 <= FRAME_HEIGHT):
            raise ValueError("three-card ROI must fit the canonical frame")
        for name, bounds in (
            ("card_width_range", self.card_width_range),
            ("card_height_range", self.card_height_range),
            ("spacing_range", self.spacing_range),
        ):
            lower, upper = bounds
            if lower <= 0 or upper < lower:
                raise ValueError(f"{name} must be a positive inclusive range")


DEFAULT_THREE_CARDS_PROFILE = ThreeCardsProfile(
    roi=(650, 45, 805, 125),
    # The last bottom card can lose a few visible pixels to the multiplier
    # ribbon/folded-corner decoration during the doubling animation.
    card_width_range=(24, 31),
    card_height_range=(38, 42),
    spacing_range=(20, 24),
)


@dataclass(frozen=True, slots=True)
class _BottomGlyph:
    full: _Template | None
    upper: _Template | None
    core: _Template | None


@dataclass(frozen=True, slots=True)
class BottomTemplateCatalog:
    """Dedicated, potentially partial templates for face-up bottom cards."""

    templates: dict[str, tuple[_BottomGlyph, ...]]

    @property
    def available_ranks(self) -> tuple[str, ...]:
        return tuple(rank for rank in RANKS if self.templates.get(rank))

    @property
    def missing_ranks(self) -> tuple[str, ...]:
        return tuple(rank for rank in RANKS if not self.templates.get(rank))

    @classmethod
    def from_labeled_frames(
        cls,
        samples: Sequence[tuple[np.ndarray, Sequence[str]]],
        profile: ThreeCardsProfile = DEFAULT_THREE_CARDS_PROFILE,
    ) -> BottomTemplateCatalog:
        collected: dict[str, list[_BottomGlyph]] = {rank: [] for rank in RANKS}
        for frame, labels in samples:
            _validate_frame(frame)
            boxes = _locate_three_slots(frame, profile)
            if boxes is None:
                raise HandReadError("bottom calibration frame does not contain exactly three slots")
            if len(labels) != 3:
                raise HandReadError("bottom calibration labels must contain three ranks")
            for index, (box, rank) in enumerate(zip(boxes, labels, strict=True)):
                if rank not in collected:
                    raise HandReadError(f"unknown bottom calibration rank: {rank}")
                collected[rank].append(_extract_bottom_glyph(_rank_crops(frame, boxes, index)))
        return cls({rank: tuple(values) for rank, values in collected.items() if values})


class ThreeCardsReader:
    def __init__(
        self,
        catalog: BottomTemplateCatalog,
        profile: ThreeCardsProfile = DEFAULT_THREE_CARDS_PROFILE,
        maximum_distance: float = MAXIMUM_DISTANCE,
    ) -> None:
        if not isinstance(catalog, BottomTemplateCatalog):
            raise TypeError("ThreeCardsReader requires a BottomTemplateCatalog")
        if maximum_distance != MAXIMUM_DISTANCE:
            raise ValueError("three-card classification threshold is frozen at 0.55")
        self._catalog = catalog
        self._profile = profile
        self._maximum_distance = maximum_distance

    def read(self, frame: np.ndarray) -> RegionCardsRead | None:
        return self.read_with_diagnostics(frame).value

    def read_with_diagnostics(self, frame: np.ndarray) -> ReaderResult[RegionCardsRead]:
        _validate_frame(frame)
        if not _bottom_region_present(frame, self._profile):
            return _failure(FailureStage.PRESENCE, "bottom_region_absent")
        boxes = _locate_three_slots(frame, self._profile)
        if boxes is None:
            return _failure(
                FailureStage.GEOMETRY,
                "exactly_three_slots_not_found",
            )

        observed: list[_BottomGlyph] = []
        for index, box in enumerate(boxes):
            try:
                observed.append(_extract_bottom_glyph(_rank_crops(frame, boxes, index)))
            except HandReadError:
                reason = "rank_crop_contains_no_foreground"
                slots = tuple(
                    _unclassified_slot(
                        slot,
                        candidate_box,
                        FailureStage.GLYPH_EXTRACTION if slot == index else None,
                        reason if slot == index else None,
                    )
                    for slot, candidate_box in enumerate(boxes)
                )
                return _failure(
                    FailureStage.GLYPH_EXTRACTION,
                    reason,
                    slots,
                )

        labels = self._catalog.available_ranks
        if not labels:
            return _failure(
                FailureStage.CLASSIFICATION,
                "template_missing",
                tuple(
                    _unclassified_slot(
                        index,
                        box,
                        FailureStage.CLASSIFICATION,
                        "template_missing",
                    )
                    for index, box in enumerate(boxes)
                ),
            )

        cards: list[str] = []
        assigned_distances: list[float] = []
        slots: list[SlotDiagnostics] = []
        failed_indices: list[int] = []
        for index, (box, item) in enumerate(zip(boxes, observed, strict=True)):
            distances = _ordered_rank_distances(
                item,
                self._catalog.templates,
                labels,
                self._maximum_distance,
            )
            distance, rank = min(zip(distances, labels, strict=True))
            cards.append(rank)
            assigned_distances.append(float(distance))
            slot = SlotDiagnostics.classified(
                slot=index,
                box=box,
                distances=distances,
                labels=labels,
                assigned_candidate=rank,
                assigned_distance=distance,
            )
            if distance > self._maximum_distance:
                failed_indices.append(index)
            slots.append(slot)

        if failed_indices:
            reason = "classification_distance_exceeded"
            failed = set(failed_indices)
            return _failure(
                FailureStage.CLASSIFICATION,
                reason,
                tuple(
                    replace(
                        slot,
                        assigned_candidate=(None if index in failed else slot.assigned_candidate),
                        assigned_distance=(None if index in failed else slot.assigned_distance),
                        failure_stage=(
                            FailureStage.CLASSIFICATION if index in failed else slot.failure_stage
                        ),
                        reason=reason if index in failed else slot.reason,
                    )
                    for index, slot in enumerate(slots)
                ),
            )

        result = RegionCardsRead(
            cards=tuple(cards),
            maximum_distance=max(assigned_distances),
        )
        if not _valid_bottom_cards(result.cards):
            return _failure(
                FailureStage.RULE_VALIDATION,
                "invalid_bottom_card_multiset",
                tuple(slots),
            )
        return ReaderResult(
            value=result,
            diagnostics=ReaderDiagnostics(
                reader="three_cards",
                region="three_cards",
                success=True,
                failure_stage=None,
                reason=None,
                slots=tuple(slots),
            ),
        )


def _failure(
    stage: FailureStage,
    reason: str,
    slots: tuple[SlotDiagnostics, ...] = (),
) -> ReaderResult[RegionCardsRead]:
    return ReaderResult(
        value=None,
        diagnostics=ReaderDiagnostics(
            reader="three_cards",
            region="three_cards",
            success=False,
            failure_stage=stage,
            reason=reason,
            slots=slots,
        ),
    )


def _unclassified_slot(
    slot: int,
    box: tuple[int, int, int, int],
    stage: FailureStage | None,
    reason: str | None,
) -> SlotDiagnostics:
    return SlotDiagnostics(
        slot=slot,
        box=box,
        candidates=(),
        margin=None,
        raw_candidate=None,
        raw_distance=None,
        assigned_candidate=None,
        assigned_distance=None,
        failure_stage=stage,
        reason=reason,
    )


def _bottom_region_present(
    frame: np.ndarray,
    profile: ThreeCardsProfile,
) -> bool:
    x0, y0, x1, y1 = profile.roi
    light = np.min(frame[y0:y1, x0:x1], axis=2) >= 160
    minimum_run = profile.card_width_range[0]
    return any(end - start >= minimum_run for row in light for start, end in _runs(row))


def _locate_three_slots(
    frame: np.ndarray,
    profile: ThreeCardsProfile,
) -> tuple[tuple[int, int, int, int], ...] | None:
    x0, y0, x1, y1 = profile.roi
    light = np.min(frame[y0:y1, x0:x1], axis=2) >= 160
    minimum_total = profile.card_width_range[0] + 2 * profile.spacing_range[0]
    maximum_total = profile.card_width_range[1] + 2 * profile.spacing_range[1]
    rows: dict[int, list[tuple[int, int]]] = {}
    for local_y, row in enumerate(light):
        candidates = [
            (start, end)
            for start, end in _runs(row)
            if minimum_total <= end - start <= maximum_total
        ]
        if candidates:
            rows[local_y] = candidates

    groups: list[tuple[int, int, int]] = []
    # The multiplier ribbon commonly covers the lower edge of a face-up
    # bottom card after only two clean rows remain at its top.  Two aligned
    # rows still identify the rigid 3-card geometry; demanding a third made
    # a visibly readable A/K/Q disappear exactly during doubling.
    for local_y in range(max(0, light.shape[0] - 1)):
        if not all(local_y + offset in rows for offset in range(2)):
            continue
        for first in rows[local_y]:
            aligned = [first]
            for offset in (1,):
                match = min(
                    rows[local_y + offset],
                    key=lambda run: abs(run[0] - first[0]) + abs(run[1] - first[1]),
                )
                if abs(match[0] - first[0]) > 2 or abs(match[1] - first[1]) > 2:
                    break
                aligned.append(match)
            if len(aligned) != 2:
                continue
            starts = sorted(run[0] for run in aligned)
            ends = sorted(run[1] for run in aligned)
            groups.append((starts[1] + x0, local_y + y0, ends[1] - starts[1]))

    deduplicated: list[tuple[int, int, int]] = []
    for group in sorted(groups, key=lambda item: (item[1], item[0])):
        if any(
            abs(group[0] - current[0]) <= 2 and abs(group[1] - current[1]) <= 2
            for current in deduplicated
        ):
            continue
        deduplicated.append(group)
    if not deduplicated:
        return None

    # Faded rank corners turn the card body into additional long light rows.
    # They describe the same rigid three-card group below its real top edge,
    # so anchor geometry to the first (topmost) aligned outer edge.
    left, top, total_width = deduplicated[0]
    outer_right = left + total_width
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    edges = [left]
    for slot in range(2):
        previous = edges[-1]
        candidates = range(
            previous + profile.spacing_range[0],
            previous + profile.spacing_range[1] + 1,
        )
        measured = max(candidates, key=lambda x: _corner_score(gray, x, top))
        if _corner_score(gray, measured, top) < 6.0:
            # Multiplier ribbons and special-bottom-card badges cover the
            # lower card body but leave the shared top edge and rank corners
            # fixed.  When the weak inner shadow disappears, recover the two
            # calibrated left-corner offsets from the uniquely located outer
            # top edge instead of requiring a full visible card outline.
            measured = left + _CANONICAL_LEFT_CORNER_OFFSETS[slot + 1]
            if measured not in candidates:
                return None
        edges.append(measured)
    pitches = tuple(right - left_edge for left_edge, right in zip(edges, edges[1:]))
    if any(
        pitch not in range(profile.spacing_range[0], profile.spacing_range[1] + 1)
        for pitch in pitches
    ):
        return None
    width = outer_right - edges[-1]
    if width not in range(profile.card_width_range[0], profile.card_width_range[1] + 1):
        return None
    height = _measure_card_height(gray, left, outer_right, top, y1)
    if height is None or height not in range(
        profile.card_height_range[0], profile.card_height_range[1] + 1
    ):
        return None
    return tuple((edge, top, width, height) for edge in edges)


def _corner_score(gray: np.ndarray, x: int, top: int) -> float:
    return float((gray[top + 1 : top + 4, x] - gray[top + 1 : top + 4, x - 1]).mean())


def _measure_card_height(
    gray: np.ndarray,
    left: int,
    right: int,
    top: int,
    roi_bottom: int,
) -> int | None:
    width = right - left
    minimum = width // 3
    maximum = min(roi_bottom - top - 1, width)
    if maximum < minimum:
        return None
    outer_columns = (left, left + 1, right - 2, right - 1)
    candidates = range(minimum, maximum + 1)
    return max(
        candidates,
        key=lambda height: float(
            np.abs(gray[top + height, outer_columns] - gray[top + height - 1, outer_columns]).mean()
        ),
    )


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    indices = np.flatnonzero(values)
    if not len(indices):
        return []
    result: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current != previous + 1:
            result.append((start, previous + 1))
            start = current
        previous = current
    result.append((start, previous + 1))
    return result


def _rank_crops(
    frame: np.ndarray,
    boxes: tuple[tuple[int, int, int, int], ...],
    index: int,
) -> tuple[np.ndarray, ...]:
    left, top, _width, _height = boxes[index]
    crops = []
    for x1, y1, x2, y2 in (
        _RANK_FULL_BOUNDS,
        _RANK_UPPER_BOUNDS,
        _RANK_CORE_BOUNDS,
    ):
        crops.append(frame[top + y1 : top + y2, left + x1 : left + x2])
    return tuple(crops)


def _extract_bottom_glyph(crops: tuple[np.ndarray, ...]) -> _BottomGlyph:
    variants: list[_Template | None] = []
    for crop in crops:
        try:
            variants.append(_extract_rank_template(crop))
        except HandReadError:
            variants.append(None)
    if len(variants) != len(_RANK_VARIANTS):
        raise ValueError("bottom glyph requires full, upper, and core crops")
    result = _BottomGlyph(*variants)
    if all(getattr(result, variant) is None for variant in _RANK_VARIANTS):
        raise HandReadError("rank crop contains no foreground")
    return result


def _extract_rank_template(crop: np.ndarray) -> _Template:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    spread = np.max(crop, axis=2).astype(np.int16) - np.min(crop, axis=2).astype(np.int16)
    absolute = ((gray < 145) | ((spread > 35) & (gray < 195))).astype(np.uint8)
    cleaned = _clean_rank_mask(absolute)
    if int(cleaned.sum()) < 10:
        # A deal animation can wash one rank corner toward white while its
        # outline remains 40+ grayscale levels below the local card face.
        # Keep the calibrated absolute mask for normal cards and use local
        # contrast only when that mask has genuinely lost the glyph.
        card_background = float(np.percentile(gray, 90))
        cleaned = _clean_rank_mask((gray < card_background - 40).astype(np.uint8))
    normalized = _normalize_mask(cleaned)
    foreground = cleaned.astype(bool)
    red = (crop[:, :, 2] > crop[:, :, 1] + 35) & (crop[:, :, 2] > crop[:, :, 0] + 35)
    red_fraction = float(red[foreground].mean()) if foreground.any() else 0.0
    return _Template(normalized, red_fraction)


def _clean_rank_mask(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 2:
            cleaned[labels == label] = 1
    return cleaned


def _rank_distance_for_variant(
    observed: _BottomGlyph,
    samples: Sequence[_BottomGlyph],
    rank: str,
    variant: str,
) -> float | None:
    observed_template = getattr(observed, variant)
    if observed_template is None:
        return None
    templates = tuple(
        template for sample in samples if (template := getattr(sample, variant)) is not None
    )
    if not templates:
        return None
    return _rank_distance(observed_template, templates, rank)


def _ordered_rank_distances(
    observed: _BottomGlyph,
    catalog: dict[str, tuple[_BottomGlyph, ...]],
    labels: tuple[str, ...],
    maximum_distance: float,
) -> tuple[float, ...]:
    variants: list[tuple[float, float, int, tuple[float, ...]]] = []
    for variant in _RANK_VARIANTS:
        distances = tuple(
            distance
            if (
                distance := _rank_distance_for_variant(
                    observed,
                    catalog[rank],
                    rank,
                    variant,
                )
            )
            is not None
            else float("inf")
            for rank in labels
        )
        if all(not np.isfinite(distance) for distance in distances):
            continue
        ordered = sorted(distance for distance in distances if np.isfinite(distance))
        best = ordered[0]
        margin = ordered[1] - best if len(ordered) > 1 else float("inf")
        variants.append((margin, -best, -len(variants), distances))
    accepted = [item for item in variants if -item[1] <= maximum_distance]
    if accepted:
        # Full-card crops contain more pixels, but multiplier ribbons corrupt
        # exactly those lower pixels.  Select the representation whose best
        # rank is most clearly separated from its runner-up; use distance and
        # the original full/upper/core order only as deterministic tie-breaks.
        return max(accepted, key=lambda item: item[:3])[3]
    if variants:
        return max(variants, key=lambda item: item[1:3])[3]
    return tuple(float("inf") for _rank in labels)


def _valid_bottom_cards(cards: tuple[str, ...]) -> bool:
    if len(cards) != 3 or any(rank not in _CAPACITY_BY_RANK for rank in cards):
        return False
    return all(amount <= _CAPACITY_BY_RANK[rank] for rank, amount in Counter(cards).items())


def _validate_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.shape != (
        FRAME_HEIGHT,
        FRAME_WIDTH,
        3,
    ):
        raise ValueError("frame must be a 1455x819 3-channel BGR image")
