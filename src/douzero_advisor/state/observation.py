from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StableObservation:
    """An immutable, confirmed snapshot of a recognized game frame."""

    cards_by_region: Mapping[str, tuple[str, ...]]
    markers: frozenset[str]
    minimum_score: float
    evidence_hash: str
    confirmation_count: int

    def cards(self, region: str) -> tuple[str, ...]:
        return self.cards_by_region.get(region, ())
