from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    VISUAL_RANKS,
    HandReadError,
    HandTemplateCatalog,
    _Template,
    _normalize_mask,
    _runs,
)


@dataclass(frozen=True)
class _RegionSpec:
    box: tuple[int, int, int, int]
    anchor_mode: str
    anchor: float
    expected_top: int


REGIONS = {
    # Dense combinations (for example an 8-card airplane) extend well past
    # x=650 on the left table.  The old crop silently excluded their tail.
    "left_play": _RegionSpec((330, 320, 800, 455), "left", 343.0, 334),
    # Mirror the left-table width so an eight-card action does not lose its
    # leftmost exposed rank corner before the fixed right anchor.
    "right_play": _RegionSpec((655, 320, 1125, 455), "right", 1114.0, 334),
    "my_play": _RegionSpec((450, 425, 1005, 560), "center", 726.0, 438),
}
DESKTOP_CARD_WIDTH = 83.0
DESKTOP_CARD_SPACING = 35.0
MAX_ANCHOR_OVERHANG = 20.0
RANK_CORNER_X_OFFSETS = (-8, -4, 0, 5)
# The desktop rank occupies the upper corner through about y=28; the suit
# starts immediately below it.  Small top-edge jitter is real, but the old
# +5/+6 alternatives entered the suit and let a club 2 resemble a club 8.
RANK_CORNER_Y_OFFSETS = (-1, 0)
RANK_BAND_TOP_OFFSET = 3
RANK_BAND_BOTTOM_OFFSET = 23
RANK_COMPONENT_MIN_AREA = 20
RANK_COMPONENT_MIN_HEIGHT = 8
RANK_COMPONENT_MIN_TOP_IN_BAND = 4
RANK_COMPONENT_MERGE_GAP = 3
CARD_SEAM_SCAN_TOP_OFFSET = 51
CARD_SEAM_SCAN_BOTTOM_OFFSET = 96
CARD_SEAM_GRADIENT_THRESHOLD = 18
CARD_SEAM_MIN_SCORE = 1200
CARD_SEAM_CONTINUITY_GRADIENT = 10
CARD_SEAM_MIN_CONTINUOUS_ROWS = 30
CARD_SEAM_MIN_SPACING = 30
CARD_SEAM_MAX_SPACING = 38

# A twelve-card 3--A straight is drawn as two overlapping rows at a side
# seat: A..7 on a raised first row, then 6..3 on the lower row.  It is not a
# normal wide single-row action, so the standard fixed-height rank crop sees
# part of the suit rather than part of the rank.  Keep this narrow signature
# separate from normal table OCR; it deliberately does not relax its gates.
_WRAPPED_STRAIGHT_UPPER_RANKS = ("A", "K", "Q", "J", "10", "9", "8", "7")
_WRAPPED_STRAIGHT_LOWER_RANKS = ("6", "5", "4", "3")
_WRAPPED_STRAIGHT_RANKS = (
    *_WRAPPED_STRAIGHT_UPPER_RANKS,
    *_WRAPPED_STRAIGHT_LOWER_RANKS,
)
_WRAPPED_STRAIGHT_UPPER_TOP_OFFSETS = range(-36, -29)
_WRAPPED_STRAIGHT_LOWER_TOP_OFFSETS = range(4, 11)
_WRAPPED_STRAIGHT_MAX_DISTANCE = 0.62
_WRAPPED_STRAIGHT_MAX_MEAN_DISTANCE = 0.45


@dataclass(frozen=True)
class TableTemplateCatalog:
    templates: dict[str, tuple[_Template, ...]]

    @classmethod
    def from_labeled_regions(cls, samples) -> TableTemplateCatalog:
        collected: dict[str, list[_Template]] = {}
        for frame, region, labels in samples:
            if region not in REGIONS:
                raise ValueError(f"unknown table region: {region}")
            geometry = _locate_group(frame, REGIONS[region])
            if geometry is None:
                raise HandReadError(f"calibration frame has no cards in {region}")
            left_edges, top = geometry
            if len(left_edges) != len(labels):
                raise HandReadError(
                    f"calibration label count {len(labels)} does not match detected cards {len(left_edges)}"
                )
            for left, rank in zip(left_edges, labels, strict=True):
                if rank not in RANKS:
                    raise HandReadError(f"unknown calibration rank: {rank}")
                collected.setdefault(rank, []).append(_extract_desktop_template(frame, left, top))
        return cls({rank: tuple(values) for rank, values in collected.items()})


@dataclass(frozen=True)
class _JokerWordTemplate:
    """一套已标定的竖排五字形结构。"""

    rank: str
    foreground: str
    strip: tuple[int, int]
    glyphs: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class JokerWordCatalog:
    """只接受完整五个竖排字形、固定锚点与前景颜色共同证据的大小王标定集。"""

    templates: tuple[_JokerWordTemplate, ...]

    @classmethod
    def from_labeled_frames(cls, samples) -> "JokerWordCatalog":
        templates: list[_JokerWordTemplate] = []
        for frame, rank, left, top, foreground, strip in samples:
            if rank not in {"D", "X"}:
                raise HandReadError(f"joker calibration rank must be D or X: {rank}")
            template = _joker_word_template(
                frame,
                rank=rank,
                left=left,
                top=top,
                foreground=foreground,
                strip=strip,
            )
            if template is None:
                raise HandReadError(
                    f"joker calibration does not contain a literal JOKER word: {rank}"
                )
            templates.append(template)
        if not templates:
            raise HandReadError("joker calibration must contain at least one word template")
        return cls(tuple(templates))

    def rank_near(self, frame: np.ndarray, left: int, top: int) -> str | None:
        """在桌面牌锚点附近确认五字形结构后，按颜色返回大小王。"""
        matches: set[str] = set()
        for template in self.templates:
            for horizontal_offset in range(-8, 9):
                for vertical_offset in range(-6, 7):
                    glyphs = _joker_glyph_masks(
                        frame,
                        left + horizontal_offset,
                        top + vertical_offset,
                        template.foreground,
                        template.strip,
                    )
                    if glyphs is not None:
                        matches.add(template.rank)
                        break
                if template.rank in matches:
                    break
        if len(matches) != 1:
            return None
        return matches.pop()


@dataclass(frozen=True)
class TableRead:
    detections: dict[str, tuple[str, ...]]
    maximum_distance: float
    candidate_detections: dict[str, tuple[tuple[str, ...], ...]] = field(
        default_factory=dict
    )

    def cards(self, region: str) -> tuple[str, ...]:
        return self.detections.get(region, ())

    def candidates(self, region: str) -> tuple[tuple[str, ...], ...]:
        return self.candidate_detections.get(region, ())


class TableReader:
    def __init__(
        self,
        hand_catalog: HandTemplateCatalog,
        table_catalog: TableTemplateCatalog | None = None,
        joker_word_catalog: JokerWordCatalog | None = None,
        maximum_distance: float = 0.38,
    ) -> None:
        self._hand_catalog = hand_catalog
        self._table_catalog = table_catalog or TableTemplateCatalog({})
        self._joker_word_catalog = joker_word_catalog
        self._maximum_distance = maximum_distance

    def read(self, frame: np.ndarray) -> TableRead:
        result = self.read_with_diagnostics(frame)
        if result.value is not None:
            return result.value
        raise HandReadError(result.diagnostics.reason or "table classification failed")

    def read_with_diagnostics(self, frame: np.ndarray) -> ReaderResult[TableRead]:
        return self._read_with_diagnostics(frame, tuple(REGIONS))

    def read_regions_with_diagnostics(
        self,
        frame: np.ndarray,
        regions: tuple[str, ...],
    ) -> ReaderResult[TableRead]:
        """Read exactly the requested table regions as one diagnostic result."""
        if not regions or len(set(regions)) != len(regions):
            raise ValueError("table regions must be non-empty and unique")
        unknown = set(regions).difference(REGIONS)
        if unknown:
            raise ValueError(f"unknown table regions: {sorted(unknown)}")
        return self._read_with_diagnostics(frame, regions)

    def read_region_with_diagnostics(
        self,
        frame: np.ndarray,
        region: str,
    ) -> ReaderResult[TableRead]:
        """Read one action-table region without making other regions veto it.

        During live play only the expected actor's table is relevant.  The
        other two areas can contain a fading previous play or animation that
        looks like a card edge; failing to classify those unrelated areas must
        not discard a complete expected action.
        """
        return self.read_regions_with_diagnostics(frame, (region,))

    def _read_with_diagnostics(
        self,
        frame: np.ndarray,
        regions: tuple[str, ...],
    ) -> ReaderResult[TableRead]:
        if not isinstance(frame, np.ndarray) or frame.shape != (FRAME_HEIGHT, FRAME_WIDTH, 3):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        detections: dict[str, tuple[str, ...]] = {}
        candidate_detections: dict[str, tuple[tuple[str, ...], ...]] = {}
        distances: list[float] = []
        slots: list[SlotDiagnostics] = []
        for name in regions:
            spec = REGIONS[name]
            coarse_geometry = _locate_group(frame, spec)
            # Keep the normal broad face-presence gate.  Seam-only geometry
            # can mistake unrelated UI strokes for cards on empty regions.
            seam_geometry = (
                _locate_card_seams(frame, spec)
                if coarse_geometry is not None
                else None
            )
            wrapped_straight = _read_wrapped_twelve_card_straight(
                frame,
                name,
                spec,
                seam_geometry,
                self._hand_catalog,
                self._table_catalog,
            )
            if wrapped_straight is not None:
                cards, assigned, wrapped_slots = wrapped_straight
                detections[name] = cards
                distances.extend(assigned)
                slots.extend(wrapped_slots)
                continue
            geometry = seam_geometry or coarse_geometry
            if geometry is None:
                anchored_joker = self._anchored_joker(frame, spec)
                if anchored_joker is not None:
                    detections[name] = (anchored_joker,)
                    distances.append(0.0)
                    continue
                detections[name] = ()
                slots.append(
                    SlotDiagnostics(
                        slot=len(slots),
                        box=spec.box,
                        candidates=(),
                        margin=None,
                        raw_candidate=None,
                        raw_distance=None,
                        assigned_candidate=None,
                        assigned_distance=None,
                        failure_stage=FailureStage.PRESENCE,
                        reason=f"no cards detected in {name}",
                    )
                )
                continue
            left_edges, top = geometry
            # 欢乐斗地主的大小王不是普通角标：只有固定锚点内完整的竖排五字形
            # 结构才是牌点证据。颜色只在五字形已经成立后区分 D/X，不能单独
            # 凭红色、黑色笔画或卡面亮度推断大小王。
            if len(left_edges) in {1, 2}:
                jokers = tuple(
                    self._joker_word_catalog.rank_near(frame, left, top)
                    if self._joker_word_catalog is not None
                    else None
                    for left in left_edges
                )
                if all(jokers) and (
                    len(jokers) == 1 or set(jokers) == {"D", "X"}
                ):
                    detections[name] = jokers
                    distances.extend(0.0 for _ in jokers)
                    continue
            # A bright background can extend the final white-card run by one
            # desktop spacing.  It produces a trailing slot whose corner is
            # completely blank (observed in 88 77 66), not a partial card.
            # Ignore only that final phantom; a blank first/middle slot still
            # fails closed because it could hide a real card.
            observed: list[tuple[_Template, ...]] = []
            valid_edges: list[int] = []
            for index, left in enumerate(left_edges):
                candidates = _desktop_template_candidates(frame, left, top)
                if candidates:
                    observed.append(candidates)
                    valid_edges.append(left)
                else:
                    if index == len(left_edges) - 1 and observed:
                        break
                    return _table_failure(
                        FailureStage.GLYPH_EXTRACTION,
                        f"{name} rank corner is unreadable at slot {index}",
                        slots,
                    )
            if not observed:
                return _table_failure(
                    FailureStage.GLYPH_EXTRACTION,
                    f"{name} has no readable card glyphs",
                    slots,
                )
            costs = np.asarray(
                [
                    [
                        _minimum_rank_distance(
                            items,
                            self._table_catalog.templates.get(
                                rank, self._hand_catalog.templates[rank]
                            ),
                            rank,
                        )
                        for rank in VISUAL_RANKS
                    ]
                    for items in observed
                ],
                dtype=np.float64,
            )
            # Table cards are not guaranteed to be displayed in rank order.
            # In particular, an animated / newly played group can appear as
            # ``3 3 3 4`` left-to-right.  ``_ordered_assignment`` is for the
            # local hand only: it assumes a sorted hand and therefore forced
            # the duplicate threes into different ranks (for example 8 8 8
            # 4) to satisfy its ordering constraint.  A table slot is an
            # independent glyph, so retain each slot's best rank instead.
            cards, assigned = _independent_assignment(costs)
            slot_offset = len(slots)
            slots.extend(
                [
                    SlotDiagnostics.classified(
                        slot=slot_offset + index,
                        box=(left, top, 29, 44),
                        distances=costs[index],
                        labels=VISUAL_RANKS,
                        assigned_candidate=card,
                        assigned_distance=assigned[index],
                    )
                    for index, (left, card) in enumerate(zip(valid_edges, cards, strict=True))
                ]
            )
            maximum = max(assigned, default=1.0)
            if maximum > self._maximum_distance:
                return _table_failure(
                    FailureStage.CLASSIFICATION,
                    (
                        f"{name} classification distance {maximum:.3f} exceeds "
                        f"{self._maximum_distance:.3f}"
                    ),
                    slots,
                )
            detections[name] = cards
            candidate_detections[name] = _single_slot_candidates(
                costs,
                cards,
                self._maximum_distance,
            )
            distances.extend(assigned)
        return ReaderResult(
            value=TableRead(
                detections,
                max(distances, default=0.0),
                candidate_detections,
            ),
            diagnostics=ReaderDiagnostics(
                reader="table",
                region="table",
                success=True,
                failure_stage=None,
                reason=None,
                slots=tuple(slots),
            ),
        )

    def _anchored_joker(self, frame: np.ndarray, spec: _RegionSpec) -> str | None:
        """无连续白色卡面时仍只凭完整 JOKER 字样确认单张桌面王。"""
        if self._joker_word_catalog is None:
            return None
        if spec.anchor_mode == "left":
            left = round(spec.anchor)
        elif spec.anchor_mode == "right":
            left = round(spec.anchor - DESKTOP_CARD_WIDTH)
        else:
            left = round(spec.anchor - DESKTOP_CARD_WIDTH / 2)
        return self._joker_word_catalog.rank_near(frame, left, spec.expected_top)


def _read_wrapped_twelve_card_straight(
    frame: np.ndarray,
    name: str,
    spec: _RegionSpec,
    upper_geometry: tuple[tuple[int, ...], int] | None,
    hand_catalog: HandTemplateCatalog,
    table_catalog: TableTemplateCatalog,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[SlotDiagnostics, ...]] | None:
    """Read the client's two-row rendering of the exact 3--A straight.

    The rendering is a recognisable layout, not an OCR fallback: the upper
    row has eight regularly spaced cards at the usual side anchor, while a
    lower four-card row covers its trailing four columns.  Both rows must be
    present and every rank is compared with its expected literal glyph.  This
    gives the rule layer the real twelve cards without making ordinary OCR
    accept a looser classification.
    """
    if name not in {"left_play", "right_play"} or upper_geometry is None:
        return None
    upper_edges, _ = upper_geometry
    if len(upper_edges) != len(_WRAPPED_STRAIGHT_UPPER_RANKS):
        return None

    lower_geometry = next(
        (
            geometry
            for offset in _WRAPPED_STRAIGHT_LOWER_TOP_OFFSETS
            if (geometry := _locate_card_seams(
                frame, replace(spec, expected_top=spec.expected_top + offset)
            )) is not None
            and len(geometry[0]) == len(_WRAPPED_STRAIGHT_LOWER_RANKS)
            and all(
                abs(lower - upper) <= 2
                for lower, upper in zip(geometry[0], upper_edges[-4:], strict=True)
            )
        ),
        None,
    )
    if lower_geometry is None:
        return None
    lower_edges, lower_top = lower_geometry

    best: tuple[float, float, tuple[float, ...], int] | None = None
    for upper_offset in _WRAPPED_STRAIGHT_UPPER_TOP_OFFSETS:
        upper_top = spec.expected_top + upper_offset
        edges = (*upper_edges, *lower_edges)
        tops = (upper_top,) * len(upper_edges) + (lower_top,) * len(lower_edges)
        assigned = tuple(
            _minimum_rank_distance(
                _desktop_template_candidates(frame, left, top),
                table_catalog.templates.get(rank, hand_catalog.templates[rank]),
                rank,
            )
            for left, top, rank in zip(edges, tops, _WRAPPED_STRAIGHT_RANKS, strict=True)
        )
        score = (max(assigned), float(np.mean(assigned)), assigned, upper_top)
        if best is None or score[:2] < best[:2]:
            best = score
    if best is None:
        return None
    maximum, mean, assigned, upper_top = best
    if (
        maximum > _WRAPPED_STRAIGHT_MAX_DISTANCE
        or mean > _WRAPPED_STRAIGHT_MAX_MEAN_DISTANCE
    ):
        return None

    edges = (*upper_edges, *lower_edges)
    tops = (upper_top,) * len(upper_edges) + (lower_top,) * len(lower_edges)
    return (
        _WRAPPED_STRAIGHT_RANKS,
        assigned,
        tuple(
            SlotDiagnostics.classified(
                slot=index,
                box=(left, top, 29, 44),
                distances=(distance,),
                labels=(rank,),
                assigned_candidate=rank,
                assigned_distance=distance,
            )
            for index, (left, top, rank, distance) in enumerate(
                zip(edges, tops, _WRAPPED_STRAIGHT_RANKS, assigned, strict=True)
            )
        ),
    )


def _table_failure(
    stage: FailureStage, reason: str, slots: list[SlotDiagnostics]
) -> ReaderResult[TableRead]:
    return ReaderResult(
        value=None,
        diagnostics=ReaderDiagnostics(
            reader="table",
            region="table",
            success=False,
            failure_stage=stage,
            reason=reason,
            slots=tuple(slots),
        ),
    )


def _independent_assignment(costs: np.ndarray) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """Classify every visible table-card slot independently.

    Unlike a player's sorted hand, table cards may arrive in animation order
    and may repeat a rank.  There must be no cross-slot rank uniqueness or
    ordering constraint here.
    """
    if costs.ndim != 2 or costs.shape[1] != len(VISUAL_RANKS):
        raise ValueError("table classification costs have unexpected shape")
    indices = np.argmin(costs, axis=1)
    cards = tuple(VISUAL_RANKS[int(index)] for index in indices)
    assigned = tuple(float(costs[row, index]) for row, index in enumerate(indices))
    return cards, assigned


def _single_slot_candidates(
    costs: np.ndarray,
    cards: tuple[str, ...],
    maximum_distance: float,
) -> tuple[tuple[str, ...], ...]:
    """Expose bounded OCR alternatives without inventing whole actions.

    A candidate may differ in exactly one independently scanned rank corner,
    and that replacement must already satisfy the normal OCR distance gate.
    Game rules decide later whether exactly one complete action is possible.
    """
    alternatives: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = {cards}
    for row in range(costs.shape[0]):
        for rank_index in np.argsort(costs[row]):
            if float(costs[row, rank_index]) > maximum_distance:
                break
            rank = VISUAL_RANKS[int(rank_index)]
            if rank == cards[row]:
                continue
            candidate = (*cards[:row], rank, *cards[row + 1 :])
            if candidate in seen:
                continue
            seen.add(candidate)
            alternatives.append(candidate)
    return tuple(alternatives)


_JOKER_GLYPH_COUNT = 5
_JOKER_NORMALIZED_GLYPH_SIZE = (12, 18)


def _joker_word_template(
    frame: np.ndarray,
    *,
    rank: str,
    left: int,
    top: int,
    foreground: str,
    strip: tuple[int, int],
) -> _JokerWordTemplate | None:
    glyphs = _joker_glyph_masks(frame, left, top, foreground, strip)
    if glyphs is None:
        return None
    return _JokerWordTemplate(rank, foreground, strip, glyphs)


def _joker_glyph_masks(
    frame: np.ndarray,
    left: int,
    top: int,
    foreground: str,
    strip: tuple[int, int],
) -> tuple[np.ndarray, ...] | None:
    """从标定的窄字带提取按 J、O、K、E、R 排列的五个字形。"""
    x0, x1 = strip
    patch = frame[top : top + 105, left : left + 32]
    if patch.shape != (105, 32, 3) or not 0 <= x0 < x1 <= patch.shape[1]:
        return None
    blue, green, red = cv2.split(patch)
    if foreground == "red":
        mask = (red.astype(np.int16) > green.astype(np.int16) + 35) & (
            red.astype(np.int16) > blue.astype(np.int16) + 35
        )
    elif foreground == "dark":
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        spread = np.max(patch, axis=2).astype(np.int16) - np.min(patch, axis=2).astype(np.int16)
        mask = (gray < 100) & (spread < 60)
    else:
        raise ValueError(f"unknown joker foreground: {foreground}")
    narrow = mask[:, x0:x1]
    row_runs = _runs(narrow.sum(axis=1), 2)
    if len(row_runs) != _JOKER_GLYPH_COUNT:
        return None
    glyphs: list[np.ndarray] = []
    for start, end in row_runs:
        glyph = narrow[start:end].astype(np.uint8)
        if glyph.shape[0] < 10:
            return None
        glyphs.append(
            cv2.resize(
                glyph,
                _JOKER_NORMALIZED_GLYPH_SIZE,
                interpolation=cv2.INTER_NEAREST,
            )
        )
    return tuple(glyphs)


def _locate_group(frame: np.ndarray, spec: _RegionSpec) -> tuple[tuple[int, ...], int] | None:
    x0, y0, x1, y1 = spec.box
    crop = frame[y0:y1, x0:x1]
    light = np.min(crop, axis=2) >= 165
    candidates = []
    lower_face = np.min(
        frame[spec.expected_top + 80 : spec.expected_top + 105, x0:x1],
        axis=2,
    ) >= 165
    run_sets = (
        _runs(lower_face.sum(axis=0), 15),
        _runs(light.sum(axis=0), 40),
    )
    for source_priority, runs in enumerate(run_sets):
        for local_start, local_end in runs:
            start = local_start + x0
            end = local_end + x0
            if (
                spec.anchor_mode == "left"
                and start <= spec.anchor <= end
                and spec.anchor - start <= MAX_ANCHOR_OVERHANG
            ):
                start = round(spec.anchor)
            elif (
                spec.anchor_mode == "right"
                and start <= spec.anchor <= end
                and end - spec.anchor <= MAX_ANCHOR_OVERHANG
            ):
                end = round(spec.anchor)
            width = end - start
            for count in range(1, 21):
                if count == 1:
                    geometry_error = abs(width - DESKTOP_CARD_WIDTH)
                    if geometry_error > 10:
                        continue
                else:
                    spacing = (width - DESKTOP_CARD_WIDTH) / (count - 1)
                    if not 32 <= spacing <= 38:
                        continue
                    geometry_error = abs(spacing - DESKTOP_CARD_SPACING)
                if spec.anchor_mode == "left":
                    anchor_error = abs(start - spec.anchor)
                elif spec.anchor_mode == "right":
                    anchor_error = abs(end - spec.anchor)
                else:
                    anchor_error = abs((start + end) / 2 - spec.anchor)
                score = anchor_error + geometry_error * 4
                if score <= 25:
                    candidates.append((source_priority, score, start, end, count))
    if not candidates:
        return None
    _, _, start, end, count = min(candidates)
    group = frame[y0:y1, start:end]
    light_rows = (np.min(group, axis=2) >= 165).sum(axis=1)
    threshold = max(25, int((end - start) * 0.30))
    rows = np.flatnonzero(light_rows >= threshold)
    if not len(rows):
        return None
    # Avatar clothing and table decorations may satisfy the light-row
    # threshold above a real card group.  The card top itself is fixed for
    # each table region, so select a qualifying row near that expected top
    # instead of blindly taking the first bright row in the crop.
    expected_local_top = spec.expected_top - y0
    near_expected = rows[np.abs(rows - expected_local_top) <= 3]
    if not len(near_expected):
        return None
    top = int(near_expected[np.argmin(np.abs(near_expected - expected_local_top))] + y0)
    if count == 1:
        left_edges = (start,)
    else:
        spacing = (end - start - DESKTOP_CARD_WIDTH) / (count - 1)
        if not 32 <= spacing <= 38:
            return None
        left_edges = tuple(int(round(start + index * spacing)) for index in range(count))
    return left_edges, top


def _locate_card_seams(
    frame: np.ndarray,
    spec: _RegionSpec,
) -> tuple[tuple[int, ...], int] | None:
    """Use continuous vertical card seams to determine side-action slots.

    Every overlapping card starts with a dark-to-light vertical boundary in
    the lower, glyph-free part of the card face. Starting from the fixed
    outer card anchor, scan the whole region for independent boundaries and
    select the longest regularly spaced sequence.  One modest anti-aliased
    boundary must never prevent later, clearer boundaries from being seen.
    Rank and suit pixels never decide how many cards are present.
    """
    if spec.anchor_mode not in {"left", "right"}:
        return None
    top = spec.expected_top
    band = frame[
        top + CARD_SEAM_SCAN_TOP_OFFSET : top + CARD_SEAM_SCAN_BOTTOM_OFFSET
    ]
    if band.size == 0:
        return None
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    delta = gray[:, 1:].astype(np.int16) - gray[:, :-1].astype(np.int16)
    positive = np.maximum(delta, 0)
    scores = positive.sum(axis=0) + (
        delta > CARD_SEAM_GRADIENT_THRESHOLD
    ).sum(axis=0) * 100
    continuous_rows = (delta >= CARD_SEAM_CONTINUITY_GRADIENT).sum(axis=0)

    x0, _, x1, _ = spec.box
    if spec.anchor_mode == "left":
        seed_target = int(round(spec.anchor)) - 1
        direction = 1
    else:
        seed_target = int(round(spec.anchor - DESKTOP_CARD_WIDTH)) - 1
        direction = -1

    # Detect every vertical divider before considering card order.  A divider
    # can qualify either by strong contrast or by remaining vertically
    # continuous across most of the glyph-free band.  The latter covers a
    # visibly real anti-aliased line whose per-pixel gradient is modest.
    qualified = np.flatnonzero(
        (scores >= CARD_SEAM_MIN_SCORE)
        | (continuous_rows >= CARD_SEAM_MIN_CONTINUOUS_ROWS)
    )
    qualified = qualified[(qualified >= x0) & (qualified < x1 - 1)]
    if not len(qualified):
        return None

    # Adjacent columns can represent the same anti-aliased edge.  Collapse
    # each narrow cluster to its strongest continuous column.
    split_at = np.flatnonzero(np.diff(qualified) > 2) + 1
    clusters = np.split(qualified, split_at)
    strength = scores + continuous_rows * 100
    candidates = tuple(
        int(cluster[np.argmax(strength[cluster])])
        for cluster in clusters
        if len(cluster)
    )

    # The broad white card face bounds where a real left edge may occur.  It
    # does not assign a count; it only keeps unrelated table decorations from
    # extending an otherwise regular sequence beyond the final full card.
    face_bounds = _card_face_bounds(frame, spec)
    if face_bounds is not None:
        face_start, face_end = face_bounds
        minimum_boundary = face_start - 6
        maximum_boundary = face_end - int(round(DESKTOP_CARD_WIDTH)) + 5
        candidates = tuple(
            boundary
            for boundary in candidates
            if minimum_boundary <= boundary + 1 <= maximum_boundary
        )

    seeds = tuple(
        boundary for boundary in candidates if abs(boundary - seed_target) <= 5
    )
    if not seeds:
        return None
    seed = max(seeds, key=lambda boundary: int(strength[boundary]))

    # Evaluate every candidate in the region, then choose the longest regular
    # sequence anchored at the known outer card.  This is global selection,
    # not the previous "first weak edge ends the scan" walk.
    ordered = sorted(
        (boundary for boundary in candidates if direction * (boundary - seed) > 0),
        reverse=direction < 0,
    )
    paths: dict[int, tuple[int, ...]] = {seed: (seed,)}
    for boundary in ordered:
        predecessors = [
            previous
            for previous in paths
            if CARD_SEAM_MIN_SPACING
            <= direction * (boundary - previous)
            <= CARD_SEAM_MAX_SPACING
        ]
        if not predecessors:
            continue
        previous = max(
            predecessors,
            key=lambda item: (
                len(paths[item]),
                sum(int(strength[value]) for value in paths[item]),
            ),
        )
        paths[boundary] = (*paths[previous], boundary)

    boundaries = max(
        paths.values(),
        key=lambda path: (
            len(path),
            sum(int(strength[value]) for value in path),
        ),
    )

    left_edges = tuple(boundary + 1 for boundary in sorted(boundaries))
    return left_edges, top


def _locate_rank_corners(
    frame: np.ndarray,
    spec: _RegionSpec,
) -> tuple[tuple[int, ...], int] | None:
    """Locate exposed rank glyphs without deriving card count from group width.

    Overlapping table cards can hide every lower/right white edge while their
    rank corners remain fully visible.  The broad white face is therefore only
    used to bound the scan; connected rank glyphs inside that face determine
    the semantic card slots.
    """
    bounds = _card_face_bounds(frame, spec)
    if bounds is None:
        return None
    face_start, face_end = bounds
    top = spec.expected_top
    band_top = top + RANK_BAND_TOP_OFFSET
    band_bottom = top + RANK_BAND_BOTTOM_OFFSET
    patch = frame[band_top:band_bottom, face_start:face_end]
    if patch.size == 0:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    spread = np.max(patch, axis=2).astype(np.int16) - np.min(patch, axis=2).astype(
        np.int16
    )
    foreground = ((gray < 145) | ((spread > 35) & (gray < 195))).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    components: list[tuple[int, int, int, int]] = []
    for label in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[label])
        if (
            area < RANK_COMPONENT_MIN_AREA
            or height < RANK_COMPONENT_MIN_HEIGHT
            # The folded/decorated tail of the last full card often touches
            # the scan band's top border.  Real rank glyphs sit lower and do
            # not touch that boundary.
            or y < RANK_COMPONENT_MIN_TOP_IN_BAND
        ):
            continue
        components.append(
            (
                face_start + x,
                face_start + x + width,
                band_top + y,
                band_top + y + height,
            )
        )
    if not components:
        return None
    components.sort()
    tokens: list[tuple[int, int, int, int]] = []
    for component in components:
        if tokens and _rank_components_belong_to_one_token(tokens[-1], component):
            previous = tokens[-1]
            tokens[-1] = (
                previous[0],
                component[1],
                min(previous[2], component[2]),
                max(previous[3], component[3]),
            )
        else:
            tokens.append(component)
    left_edges = tuple(token[0] - 3 for token in tokens)
    return (left_edges, top) if left_edges else None


def _card_face_bounds(frame: np.ndarray, spec: _RegionSpec) -> tuple[int, int] | None:
    """Return the anchored broad white run, without assigning it a card count."""
    x0, y0, x1, y1 = spec.box
    light = np.min(frame[y0:y1, x0:x1], axis=2) >= 165
    runs = [(start + x0, end + x0) for start, end in _runs(light.sum(axis=0), 40)]
    if spec.anchor_mode == "left":
        candidates = [
            run
            for run in runs
            if run[0] <= spec.anchor <= run[1]
            and spec.anchor - run[0] <= MAX_ANCHOR_OVERHANG
        ]
    elif spec.anchor_mode == "right":
        candidates = [
            run
            for run in runs
            if run[0] <= spec.anchor <= run[1]
            and run[1] - spec.anchor <= MAX_ANCHOR_OVERHANG
        ]
    else:
        candidates = [run for run in runs if run[0] <= spec.anchor <= run[1]]
    if not candidates:
        return None
    return max(candidates, key=lambda run: run[1] - run[0])


def _rank_components_belong_to_one_token(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    gap = right[0] - left[1]
    vertical_overlap = min(left[3], right[3]) - max(left[2], right[2])
    return 0 <= gap <= RANK_COMPONENT_MERGE_GAP and vertical_overlap > 0


def _select_table_geometry(
    coarse_geometry: tuple[tuple[int, ...], int] | None,
    corner_geometry: tuple[tuple[int, ...], int] | None,
) -> tuple[tuple[int, ...], int] | None:
    """Keep static white-card geometry unless spaced corners prove an undercount.

    Rank and suit glyphs inside one card can form multiple connected components,
    so their raw count must not replace a stable card-face count.  The corner
    scan is only allowed to recover extra overlapping cards when every proposed
    slot follows the observed desktop-card spacing.
    """
    if coarse_geometry is None or corner_geometry is None:
        return coarse_geometry
    coarse_edges, _ = coarse_geometry
    corner_edges, _ = corner_geometry
    if len(corner_edges) <= len(coarse_edges):
        return coarse_geometry
    spacings = tuple(
        right - left for left, right in zip(corner_edges, corner_edges[1:])
    )
    if spacings and all(32 <= spacing <= 38 for spacing in spacings):
        return corner_geometry
    # A long, tightly overlapped group can make the same white width fit one
    # fewer coarse slot.  Rank glyphs at the two exposed ends are not aligned
    # like interior glyphs, so allow those two gaps a little more freedom only
    # when the complete interior sequence still proves exactly one extra card.
    interior_spacings = spacings[1:-1]
    if (
        len(coarse_edges) >= 5
        and len(corner_edges) == len(coarse_edges) + 1
        and interior_spacings
        and all(30 <= spacing <= 38 for spacing in interior_spacings)
        and all(20 <= spacing <= 40 for spacing in spacings)
    ):
        return corner_geometry
    return coarse_geometry


def _extract_desktop_template(frame: np.ndarray, left: int, top: int) -> _Template:
    # Cover the complete rank token while stopping above the suit.  Template
    # and observation crops must use the same rank-only representation.
    patch = frame[top + 4 : top + 29, left + 3 : left + 32]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    spread = np.max(patch, axis=2).astype(np.int16) - np.min(patch, axis=2).astype(np.int16)
    mask = ((gray < 145) | ((spread > 35) & (gray < 195))).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 3:
            cleaned[labels == label] = 1
    normalized = _normalize_mask(cleaned)
    foreground = cleaned.astype(bool)
    red = (patch[:, :, 2] > patch[:, :, 1] + 35) & (patch[:, :, 2] > patch[:, :, 0] + 35)
    red_fraction = float(red[foreground].mean()) if foreground.any() else 0.0
    return _Template(normalized, red_fraction)


def _desktop_template_candidates(
    frame: np.ndarray,
    left: int,
    top: int,
) -> tuple[_Template, ...]:
    """Read one rank corner across small independent x/y offsets."""
    candidates: list[_Template] = []
    for horizontal_offset in RANK_CORNER_X_OFFSETS:
        for vertical_offset in RANK_CORNER_Y_OFFSETS:
            try:
                candidates.append(
                    _extract_desktop_template(
                        frame,
                        left + horizontal_offset,
                        top + vertical_offset,
                    )
                )
            except HandReadError:
                continue
    return tuple(candidates)


def _minimum_rank_distance(
    observed: tuple[_Template, ...],
    templates: tuple[_Template, ...],
    rank: str,
) -> float:
    """Vectorize candidate/template IoU for one rank."""
    observed_masks = np.stack([item.mask for item in observed]).astype(bool)[:, None]
    template_masks = np.stack([item.mask for item in templates]).astype(bool)[None, :]
    union = np.logical_or(observed_masks, template_masks).sum(axis=(2, 3))
    intersection = np.logical_and(observed_masks, template_masks).sum(axis=(2, 3))
    distances = np.where(union, 1.0 - intersection / union, 1.0)
    if rank in {"D", "X"}:
        observed_red = np.asarray([item.red_fraction for item in observed])[:, None]
        template_red = np.asarray([item.red_fraction for item in templates])[None, :]
        distances = distances + 0.5 * np.abs(observed_red - template_red)
    return float(np.min(distances))
