from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Sequence, TypeVar


class FailureStage(str, Enum):
    PRESENCE = "presence"
    GEOMETRY = "geometry"
    GLYPH_EXTRACTION = "glyph_extraction"
    CLASSIFICATION = "classification"
    ASSIGNMENT = "assignment"
    RULE_VALIDATION = "rule_validation"


@dataclass(frozen=True, slots=True)
class RankCandidate:
    label: str
    distance: float


def top_candidates(
    distances: Sequence[float], labels: Sequence[str], *, k: int = 3
) -> tuple[RankCandidate, ...]:
    if len(distances) != len(labels):
        raise ValueError("distances and labels must be aligned")
    if k < 0:
        raise ValueError("k must be non-negative")
    return tuple(
        RankCandidate(label, float(distance))
        for distance, label in sorted(zip(distances, labels, strict=True))[: min(k, 3)]
    )


@dataclass(frozen=True, slots=True)
class SlotDiagnostics:
    slot: int
    box: tuple[int, int, int, int] | None
    candidates: tuple[RankCandidate, ...]
    margin: float | None
    raw_candidate: str | None
    raw_distance: float | None
    assigned_candidate: str | None
    assigned_distance: float | None
    failure_stage: FailureStage | None
    reason: str | None

    @classmethod
    def classified(
        cls,
        *,
        slot: int,
        box: tuple[int, int, int, int] | None,
        distances: Sequence[float],
        labels: Sequence[str],
        assigned_candidate: str | None,
        assigned_distance: float | None,
        k: int = 3,
    ) -> SlotDiagnostics:
        candidates = top_candidates(distances, labels, k=k)
        return cls(
            slot=slot,
            box=box,
            candidates=candidates,
            margin=(
                candidates[1].distance - candidates[0].distance
                if len(candidates) > 1
                else None
            ),
            raw_candidate=candidates[0].label if candidates else None,
            raw_distance=candidates[0].distance if candidates else None,
            assigned_candidate=assigned_candidate,
            assigned_distance=assigned_distance,
            failure_stage=None,
            reason=None,
        )


@dataclass(frozen=True, slots=True)
class ReaderDiagnostics:
    reader: str
    region: str
    success: bool
    failure_stage: FailureStage | None
    reason: str | None
    slots: tuple[SlotDiagnostics, ...]


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ReaderResult(Generic[T]):
    value: T | None
    diagnostics: ReaderDiagnostics
