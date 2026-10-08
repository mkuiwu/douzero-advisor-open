"""从手牌区域提取牌面，并以不确定结果阻断下游自动动作。

读牌器输出排序后的环境牌编码和可见几何信息；重叠、缺口或超出牌面
容量时返回不可用结果。调用方应把它当作观测证据，而不是直接授权点击。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

import cv2
import numpy as np

from douzero_advisor.vision.diagnostics import (
    FailureStage,
    ReaderDiagnostics,
    ReaderResult,
    SlotDiagnostics,
)


FRAME_WIDTH = 1455
FRAME_HEIGHT = 819
SEARCH_X0 = 220
SEARCH_X1 = 1270
SEARCH_Y0 = 570
SEARCH_Y1 = 790
CARD_TOP_SEARCH_Y0 = 565
CARD_TOP_SEARCH_Y1 = 625
CARD_SPACING = 46.65
LAST_CARD_WIDTH = 123.0
HAND_CARD_TOPS = (574, 584, 600)
RANK_COMPONENT_MIN_AREA = 40
RANK_COMPONENT_MAX_AREA = 700
RANK_COMPONENT_MIN_WIDTH = 8
RANK_COMPONENT_MAX_WIDTH = 32
RANK_COMPONENT_MIN_HEIGHT = 18
RANK_COMPONENT_MERGE_GAP = 3
RANK_CORNER_LEFT_OFFSETS = (9, 13, 17)
RANKS = ("D", "X", "2", "A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3")
VISUAL_RANKS = ("D", "X", "2", "A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3")
CAPACITIES = (1, 1) + (4,) * 13
_CAPACITY_BY_RANK = dict(zip(RANKS, CAPACITIES, strict=True))


class HandReadError(ValueError):
    pass


@dataclass(frozen=True)
class HandRead:
    cards: tuple[str, ...]
    card_left_edges: tuple[int, ...]
    card_tops: tuple[int, ...]
    maximum_distance: float
    recovered_slots: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Template:
    mask: np.ndarray
    red_fraction: float


@dataclass(frozen=True)
class HandTemplateCatalog:
    templates: dict[str, tuple[_Template, ...]]

    @classmethod
    def from_labeled_frames(
        cls, samples: Sequence[tuple[np.ndarray, Sequence[str]]]
    ) -> HandTemplateCatalog:
        collected: dict[str, list[_Template]] = {rank: [] for rank in RANKS}
        for frame, labels in samples:
            _validate_frame(frame)
            geometry = _locate_geometry(frame)
            if geometry is None:
                raise HandReadError("calibration frame does not contain a visible hand")
            left_edges, tops = geometry
            if len(left_edges) != len(labels):
                raise HandReadError(
                    f"calibration label count {len(labels)} does not match detected cards {len(left_edges)}"
                )
            for left, top, rank in zip(left_edges, tops, labels, strict=True):
                if rank not in collected:
                    raise HandReadError(f"unknown calibration rank: {rank}")
                collected[rank].append(_extract_template(frame, left, top))
        missing = [rank for rank, values in collected.items() if not values]
        if missing:
            raise HandReadError(f"calibration is missing ranks: {', '.join(missing)}")
        return cls({rank: tuple(values) for rank, values in collected.items()})


class HandReader:
    def __init__(self, catalog: HandTemplateCatalog, maximum_distance: float = 0.38) -> None:
        self._catalog = catalog
        self._maximum_distance = maximum_distance

    def read(self, frame: np.ndarray) -> HandRead | None:
        result = self.read_with_diagnostics(frame)
        if result.value is not None:
            return result.value
        if result.diagnostics.failure_stage is FailureStage.GEOMETRY:
            return None
        raise HandReadError(result.diagnostics.reason or "hand classification failed")

    def read_with_diagnostics(self, frame: np.ndarray) -> ReaderResult[HandRead]:
        _validate_frame(frame)
        rank_tokens = _locate_rank_tokens(frame)
        if not 1 <= len(rank_tokens) <= 20:
            return self._fallback_or(
                frame,
                ReaderResult(
                    value=None,
                    diagnostics=ReaderDiagnostics(
                        reader="hand",
                        region="hand",
                        success=False,
                        failure_stage=FailureStage.GEOMETRY,
                        reason="hand rank corners are not visible",
                        slots=(),
                    ),
                ),
            )
        observed: list[tuple[tuple[int, int, _Template], ...]] = []
        for token_x, top in rank_tokens:
            candidates: list[tuple[int, int, _Template]] = []
            for offset in RANK_CORNER_LEFT_OFFSETS:
                left = token_x - offset
                try:
                    candidates.append((left, top, _extract_template(frame, left, top)))
                except HandReadError:
                    continue
            if not candidates:
                return self._fallback_or(
                    frame,
                    _hand_failure(
                        FailureStage.GLYPH_EXTRACTION,
                        "rank corner contains no readable glyph",
                        tuple(token_x - 13 for token_x, _ in rank_tokens),
                        tuple(token_top for _, token_top in rank_tokens),
                    ),
                )
            observed.append(tuple(candidates))
        costs = np.asarray(
            [
                [
                    min(
                        _rank_distance(item, self._catalog.templates[rank], rank)
                        for _left, _top, item in items
                    )
                    for rank in VISUAL_RANKS
                ]
                for items in observed
            ],
            dtype=np.float64,
        )
        try:
            cards, assigned_costs = _ordered_assignment(costs)
        except HandReadError as error:
            return self._fallback_or(
                frame,
                _hand_failure(
                    FailureStage.ASSIGNMENT,
                    str(error),
                    tuple(token_x - 13 for token_x, _ in rank_tokens),
                    tuple(top for _, top in rank_tokens),
                ),
            )
        cards, assigned_costs, recovered_slots = _recover_tight_duplicates(
            cards,
            assigned_costs,
            rank_tokens,
            self._maximum_distance,
        )
        selected = tuple(
            min(
                items,
                key=lambda candidate: _rank_distance(
                    candidate[2],
                    self._catalog.templates[rank],
                    rank,
                ),
            )
            for items, rank in zip(observed, cards, strict=True)
        )
        left_edges = tuple(item[0] for item in selected)
        selected_tops = tuple(item[1] for item in selected)
        slots = tuple(
            SlotDiagnostics.classified(
                slot=index,
                box=(left, top, 40, 65),
                distances=costs[index],
                labels=VISUAL_RANKS,
                assigned_candidate=card,
                assigned_distance=assigned_costs[index],
            )
            for index, (left, top, card) in enumerate(
                zip(left_edges, selected_tops, cards, strict=True)
            )
        )
        maximum = max(assigned_costs, default=1.0)
        if maximum > self._maximum_distance:
            return self._fallback_or(
                frame,
                ReaderResult(
                    value=None,
                    diagnostics=ReaderDiagnostics(
                        reader="hand",
                        region="hand",
                        success=False,
                        failure_stage=FailureStage.CLASSIFICATION,
                        reason=(
                            f"hand classification distance {maximum:.3f} exceeds "
                            f"{self._maximum_distance:.3f}"
                        ),
                        slots=slots,
                    ),
                ),
            )
        return ReaderResult(
            value=HandRead(
                cards=cards,
                card_left_edges=left_edges,
                card_tops=selected_tops,
                maximum_distance=maximum,
                recovered_slots=recovered_slots,
            ),
            diagnostics=ReaderDiagnostics(
                reader="hand",
                region="hand",
                success=True,
                failure_stage=None,
                reason=None,
                slots=slots,
            ),
        )

    def _fallback_or(
        self,
        frame: np.ndarray,
        primary_failure: ReaderResult[HandRead],
    ) -> ReaderResult[HandRead]:
        fallback = self._read_legacy_geometry(frame)
        return fallback if fallback is not None else primary_failure

    def _read_legacy_geometry(self, frame: np.ndarray) -> ReaderResult[HandRead] | None:
        geometry = _locate_geometry(frame)
        if geometry is None:
            return None
        left_edges, detected_tops = geometry
        candidate_tops = [tuple(dict.fromkeys((top, 584, 600))) for top in detected_tops]
        try:
            observed = [
                tuple(_extract_template(frame, left, top) for top in tops)
                for left, tops in zip(left_edges, candidate_tops, strict=True)
            ]
        except HandReadError:
            return None
        costs = np.asarray(
            [
                [
                    min(_rank_distance(item, self._catalog.templates[rank], rank) for item in items)
                    for rank in VISUAL_RANKS
                ]
                for items in observed
            ],
            dtype=np.float64,
        )
        try:
            cards, assigned_costs = _ordered_assignment(costs)
        except HandReadError:
            return None
        maximum = max(assigned_costs, default=1.0)
        if maximum > self._maximum_distance:
            return None
        selected_tops = tuple(
            min(
                tops,
                key=lambda top: _rank_distance(
                    _extract_template(frame, left, top),
                    self._catalog.templates[rank],
                    rank,
                ),
            )
            for left, tops, rank in zip(left_edges, candidate_tops, cards, strict=True)
        )
        slots = tuple(
            SlotDiagnostics.classified(
                slot=index,
                box=(left, top, 40, 65),
                distances=costs[index],
                labels=VISUAL_RANKS,
                assigned_candidate=card,
                assigned_distance=assigned_costs[index],
            )
            for index, (left, top, card) in enumerate(
                zip(left_edges, selected_tops, cards, strict=True)
            )
        )
        return ReaderResult(
            value=HandRead(
                cards=cards,
                card_left_edges=left_edges,
                card_tops=selected_tops,
                maximum_distance=maximum,
            ),
            diagnostics=ReaderDiagnostics(
                reader="hand",
                region="hand",
                success=True,
                failure_stage=None,
                reason=None,
                slots=slots,
            ),
        )


def _hand_failure(
    stage: FailureStage,
    reason: str,
    left_edges: tuple[int, ...],
    tops: tuple[int, ...],
) -> ReaderResult[HandRead]:
    return ReaderResult(
        value=None,
        diagnostics=ReaderDiagnostics(
            reader="hand",
            region="hand",
            success=False,
            failure_stage=stage,
            reason=reason,
            slots=tuple(
                SlotDiagnostics(
                    slot=index,
                    box=(left, top, 40, 65),
                    candidates=(),
                    margin=None,
                    raw_candidate=None,
                    raw_distance=None,
                    assigned_candidate=None,
                    assigned_distance=None,
                    failure_stage=stage,
                    reason=reason,
                )
                for index, (left, top) in enumerate(zip(left_edges, tops, strict=True))
            ),
        ),
    )


def _validate_frame(frame: np.ndarray) -> None:
    if (
        not isinstance(frame, np.ndarray)
        or frame.ndim != 3
        or frame.shape != (FRAME_HEIGHT, FRAME_WIDTH, 3)
    ):
        raise ValueError("frame must be a 1455x819 3-channel BGR image")


def _runs(values: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    indices = np.flatnonzero(values >= minimum)
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


def _merge_runs(runs: Sequence[tuple[int, int]], maximum_gap: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] <= maximum_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _locate_geometry(frame: np.ndarray) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    crop = frame[SEARCH_Y0:SEARCH_Y1, SEARCH_X0:SEARCH_X1]
    light = np.min(crop, axis=2) >= 165
    column_runs = _merge_runs(_runs(light.sum(axis=0), 80), maximum_gap=15)
    candidates: list[tuple[float, int, int, int]] = []
    for local_start, local_end in column_runs:
        width = local_end - local_start
        for count in range(1, 21):
            expected = LAST_CARD_WIDTH + (count - 1) * CARD_SPACING
            error = abs(width - expected)
            if error <= 12:
                candidates.append((error, -width, local_start + SEARCH_X0, count))
    if not candidates:
        return None
    _, _, start, count = min(candidates)
    left_edges = tuple(int(round(start + index * CARD_SPACING)) for index in range(count))
    tops: list[int] = []
    for left in left_edges:
        light_rows = (
            np.min(
                frame[CARD_TOP_SEARCH_Y0:CARD_TOP_SEARCH_Y1, left + 5 : left + 45],
                axis=2,
            )
            >= 165
        ).sum(axis=1)
        indices = np.flatnonzero(light_rows >= 20)
        if not len(indices):
            return None
        top = int(indices[0] + CARD_TOP_SEARCH_Y0)
        if top not in range(568, 613):
            return None
        tops.append(top)
    return left_edges, tuple(tops)


def _locate_rank_tokens(frame: np.ndarray) -> tuple[tuple[int, int], ...]:
    """Find every exposed hand-rank glyph without inferring a card count.

    Selected cards and ordinary cards occupy a small set of stable vertical
    levels in the canonical client.  Scanning each level independently keeps a
    raised glyph from joining the row below it.  The two neighbouring glyphs
    of ``10`` are merged into one semantic token before the tokens are counted.
    """
    tokens: list[tuple[int, int]] = []
    for top in HAND_CARD_TOPS:
        patch = frame[top + 5 : top + 34, SEARCH_X0:SEARCH_X1]
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        spread = np.max(patch, axis=2).astype(np.int16) - np.min(patch, axis=2).astype(np.int16)
        foreground = ((gray < 145) | ((spread > 35) & (gray < 195))).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            foreground,
            connectivity=8,
        )
        components: list[tuple[int, int, int, int]] = []
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if (
                not RANK_COMPONENT_MIN_AREA <= area <= RANK_COMPONENT_MAX_AREA
                or not RANK_COMPONENT_MIN_WIDTH <= width <= RANK_COMPONENT_MAX_WIDTH
                or height < RANK_COMPONENT_MIN_HEIGHT
                # Correctly aligned ranks start inside the crop.  A glyph from
                # the next vertical level is clipped at the border instead.
                or y < 2
            ):
                continue
            components.append(
                (
                    x + SEARCH_X0,
                    x + SEARCH_X0 + width,
                    y + top + 5,
                    y + top + 5 + height,
                )
            )
        for left, right, _upper, _lower in _merge_rank_components(components):
            if right - left <= 38:
                tokens.append((left, top))
    return tuple(sorted(tokens))


def _merge_rank_components(
    components: Sequence[tuple[int, int, int, int]],
) -> tuple[tuple[int, int, int, int], ...]:
    merged: list[tuple[int, int, int, int]] = []
    for component in sorted(components):
        if merged:
            previous = merged[-1]
            gap = component[0] - previous[1]
            vertical_overlap = min(component[3], previous[3]) - max(component[2], previous[2])
            if 0 <= gap <= RANK_COMPONENT_MERGE_GAP and vertical_overlap > 0:
                merged[-1] = (
                    previous[0],
                    component[1],
                    min(previous[2], component[2]),
                    max(previous[3], component[3]),
                )
                continue
        merged.append(component)
    return tuple(merged)


def _recover_tight_duplicates(
    cards: tuple[str, ...],
    assigned_costs: tuple[float, ...],
    rank_tokens: tuple[tuple[int, int], ...],
    maximum_distance: float,
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[int, ...]]:
    """Recover one rank corner mostly covered by the following duplicate.

    The final two cards of a small hand can overlap by roughly half the normal
    spacing.  The first narrow ``J`` then remains strong enough to prove a card
    slot, but not enough for full-template OCR.  A tight token may inherit the
    following card's rank only when that following glyph is already confident,
    the previous spacing is normal, and deck capacity remains valid.
    """
    recovered_cards = list(cards)
    recovered_costs = list(assigned_costs)
    recovered: list[int] = []
    for index in range(len(cards) - 1):
        if recovered_costs[index] <= maximum_distance:
            continue
        current_x = rank_tokens[index][0]
        next_x = rank_tokens[index + 1][0]
        if not 15 <= next_x - current_x <= 30:
            continue
        if index and current_x - rank_tokens[index - 1][0] < 35:
            continue
        if recovered_costs[index + 1] > maximum_distance:
            continue
        recovered_cards[index] = recovered_cards[index + 1]
        if any(
            amount > _CAPACITY_BY_RANK[rank] for rank, amount in Counter(recovered_cards).items()
        ):
            recovered_cards[index] = cards[index]
            continue
        recovered_costs[index] = min(
            maximum_distance,
            recovered_costs[index + 1] + 0.05,
        )
        recovered.append(index)
    return tuple(recovered_cards), tuple(recovered_costs), tuple(recovered)


def _extract_template(frame: np.ndarray, left: int, top: int) -> _Template:
    # Only the rank corner is stable across selected/unselected cards.  The
    # old 65 px crop also included the suit and overlap decoration, so moving
    # one card a few pixels changed the normalized glyph even when its rank
    # was perfectly readable.
    patch = frame[top + 5 : top + 33, left + 5 : left + 45]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    spread = np.max(patch, axis=2).astype(np.int16) - np.min(patch, axis=2).astype(np.int16)
    mask = ((gray < 145) | ((spread > 35) & (gray < 195))).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 4:
            cleaned[labels == label] = 1
    normalized = _normalize_mask(cleaned)
    foreground = cleaned.astype(bool)
    red = (patch[:, :, 2] > patch[:, :, 1] + 35) & (patch[:, :, 2] > patch[:, :, 0] + 35)
    red_fraction = float(red[foreground].mean()) if foreground.any() else 0.0
    return _Template(normalized, red_fraction)


def _normalize_mask(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask > 0)
    if not len(xs):
        raise HandReadError("rank crop contains no foreground")
    glyph = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = min(44 / glyph.shape[0], 44 / glyph.shape[1])
    width = max(1, int(round(glyph.shape[1] * scale)))
    height = max(1, int(round(glyph.shape[0] * scale)))
    resized = cv2.resize(glyph, (width, height), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((48, 48), dtype=np.uint8)
    x = (48 - width) // 2
    y = (48 - height) // 2
    canvas[y : y + height, x : x + width] = resized
    return canvas


def _mask_distance(left: np.ndarray, right: np.ndarray) -> float:
    union = np.logical_or(left, right).sum()
    intersection = np.logical_and(left, right).sum()
    return 1.0 - float(intersection) / float(union) if union else 1.0


def _rank_distance(observed: _Template, templates: Sequence[_Template], rank: str) -> float:
    distances = []
    for template in templates:
        distance = _mask_distance(observed.mask, template.mask)
        if rank in {"D", "X"}:
            distance += 0.5 * abs(observed.red_fraction - template.red_fraction)
        distances.append(distance)
    return min(distances)


def _ordered_assignment(costs: np.ndarray) -> tuple[tuple[str, ...], tuple[float, ...]]:
    card_count = int(costs.shape[0])

    @lru_cache(maxsize=None)
    def solve(rank_index: int, card_index: int):
        if rank_index == len(VISUAL_RANKS):
            return (0.0, ()) if card_index == card_count else (float("inf"), ())
        best = (float("inf"), ())
        maximum = min(
            _CAPACITY_BY_RANK[VISUAL_RANKS[rank_index]],
            card_count - card_index,
        )
        running = 0.0
        for amount in range(maximum + 1):
            if amount:
                running += float(costs[card_index + amount - 1, rank_index])
            tail_cost, tail = solve(rank_index + 1, card_index + amount)
            total = running + tail_cost
            if total < best[0]:
                best = (total, (amount,) + tail)
        return best

    total, amounts = solve(0, 0)
    if not np.isfinite(total):
        raise HandReadError("hand cannot satisfy deck rank capacities")
    cards: list[str] = []
    assigned: list[float] = []
    card_index = 0
    for rank_index, amount in enumerate(amounts):
        for _ in range(amount):
            cards.append(VISUAL_RANKS[rank_index])
            assigned.append(float(costs[card_index, rank_index]))
            card_index += 1
    return tuple(cards), tuple(assigned)
