from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np

from douzero_advisor.cards import RANK_TO_ENV
from douzero_advisor.recognition_types import ActionResultState, RecoveryCheckpoint
from douzero_advisor.state.observation import StableObservation
from douzero_advisor.vision.action_button_reader import ActionButtonReader
from douzero_advisor.vision.action_result_reader import ActionResultRead
from douzero_advisor.vision.diagnostics import ReaderResult
from douzero_advisor.vision.hand_reader import FRAME_HEIGHT, FRAME_WIDTH, HandRead
from douzero_advisor.vision.table_reader import TableRead
from douzero_advisor.vision.turn_ready_reader import has_local_turn_indicator

class LiveObservationIncomplete(RuntimeError):
    """A live frame lacks evidence required for a trustworthy observation."""


@dataclass(frozen=True, slots=True)
class RegionCardsRead:
    cards: tuple[str, ...]
    maximum_distance: float


@dataclass(frozen=True, slots=True)
class LiveMarkerRead:
    markers: frozenset[str]
    maximum_distance: float


@dataclass(frozen=True, slots=True)
class ActionDeltaSnapshot:
    """Action-result evidence plus only the dependent heavy-reader result."""

    action_results: ActionResultRead
    hand: ReaderResult[HandRead] | None
    tracker: ReaderResult[Any] | None
    evidence_hash: str
    markers: LiveMarkerRead | None = None
    table: ReaderResult[TableRead] | None = None


@dataclass(frozen=True, slots=True)
class SelfTurnReadSnapshot:
    """All fixed ROIs needed for one local-turn reconstruction attempt."""

    hand: ReaderResult[HandRead]
    action_results: ActionResultRead
    table: ReaderResult[TableRead]
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class ElementSnapshot:
    """All per-frame reader evidence, including failures kept as diagnostics."""

    hand: ReaderResult[HandRead]
    tracker: ReaderResult[Any] | None
    table: ReaderResult[TableRead]
    three_cards: ReaderResult[RegionCardsRead]
    markers: LiveMarkerRead | None
    evidence_hash: str
    maximum_success_distance: float
    action_results: ActionResultRead | None = None

    def to_live_observation(self, *, confirmation_count: int = 1) -> LiveObservation:
        if confirmation_count < 1:
            raise ValueError("confirmation_count must be positive")
        table = self.table.value
        hand = self.hand.value
        tracker = self.tracker.value if self.tracker is not None else None
        three_cards = self.three_cards.value
        cards_by_region = dict(table.detections) if table is not None else {}
        cards_by_region["my_hand"] = tuple(hand.cards) if hand is not None else ()
        cards_by_region["three_cards"] = (
            tuple(three_cards.cards)
            if three_cards is not None and len(three_cards.cards) == 3
            else ()
        )
        return LiveObservation(
            cards_by_region=MappingProxyType(cards_by_region),
            markers=(frozenset(self.markers.markers) if self.markers is not None else frozenset()),
            tracker_counts=tuple(tracker.counts) if tracker is not None else (),
            minimum_score=max(0.0, min(1.0, 1.0 - self.maximum_success_distance)),
            evidence_hash=self.evidence_hash,
            confirmation_count=confirmation_count,
            action_results=MappingProxyType(
                dict(_action_states(self.action_results, table, self.markers))
            ),
            action_visible_counts=(
                self.action_results.visible_counts
                if self.action_results is not None
                else MappingProxyType({})
            ),
        )


@dataclass(frozen=True)
class LiveObservation:
    """Confirmed live evidence that includes public card-counter state."""

    cards_by_region: Mapping[str, tuple[str, ...]]
    markers: frozenset[str]
    tracker_counts: tuple[int, ...]
    minimum_score: float
    evidence_hash: str
    confirmation_count: int
    action_results: Mapping[str, ActionResultState] = field(
        default_factory=lambda: MappingProxyType({})
    )
    action_visible_counts: Mapping[str, int | None] = field(
        default_factory=lambda: MappingProxyType({})
    )
    sequence: int = 0
    capture_generation: int = 0

    def cards(self, region: str) -> tuple[str, ...]:
        return self.cards_by_region.get(region, ())

    def to_stable(self) -> StableObservation:
        return StableObservation(
            cards_by_region=self.cards_by_region,
            markers=self.markers,
            minimum_score=self.minimum_score,
            evidence_hash=self.evidence_hash,
            confirmation_count=self.confirmation_count,
        )

    def to_recovery_checkpoint(
        self,
        evidence_hashes: tuple[str, ...] = (),
    ) -> RecoveryCheckpoint:
        """Return the full immutable persistent state used by same-deal recovery."""
        return RecoveryCheckpoint(
            generation=self.capture_generation,
            sequence=self.sequence,
            evidence_hashes=tuple(evidence_hashes),
            my_hand=tuple(sorted(RANK_TO_ENV[rank] for rank in self.cards("my_hand"))),
            tracker_counts=tuple(self.tracker_counts),
        )


class LiveObservationReader:
    """Combine calibrated read-only readers without guessing missing evidence."""

    def __init__(
        self,
        *,
        hand_reader,
        tracker_reader=None,
        table_reader,
        three_cards_reader,
        marker_reader,
        action_result_reader=None,
        turn_ready_reader=None,
        action_button_reader=None,
        observe_action_cards: bool = False,
    ) -> None:
        self._hand_reader = hand_reader
        self._tracker_reader = tracker_reader
        self._table_reader = table_reader
        self._three_cards_reader = three_cards_reader
        self._marker_reader = marker_reader
        self._action_result_reader = action_result_reader
        # This reader is deliberately advisory-output-only.  Action tracking
        # continues to rely on result/table/hand evidence; a visual turn
        # affordance merely prevents the UI from showing advice while an
        # animation or settlement screen is still covering the local controls.
        self._turn_ready_reader = turn_ready_reader
        self._action_button_reader = action_button_reader or ActionButtonReader()
        self._observe_action_cards = observe_action_cards

    def is_my_turn_ready(self, frame: np.ndarray) -> bool | None:
        """Return whether the local controls are visibly actionable.

        ``None`` means this installation has no calibration for the optional
        affordance reader, preserving compatibility with older manifests.
        A configured reader fails closed: an unknown image is not a safe point
        to show a recommendation.
        """
        reader = self._turn_ready_reader
        if reader is None:
            return True if has_local_turn_indicator(frame) else None
        return bool(reader.is_ready(frame, "my_play"))

    def local_action_buttons_present(self, frame: np.ndarray) -> bool:
        """Return whether play-stage 出牌/要不起 controls are on screen.

        This is the Java LOCAL_TURN on/off switch.  The countdown circle is
        ignored: it is animated and is not a wide action button.
        """
        return self._action_button_reader.read(frame).actionable

    def read_my_hand_with_diagnostics(self, frame: np.ndarray) -> ReaderResult[HandRead]:
        """Read the current local hand for the final advice safety check."""
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        return self._hand_reader.read_with_diagnostics(frame)

    def read_bottom_cards_with_diagnostics(
        self,
        frame: np.ndarray,
    ) -> ReaderResult[RegionCardsRead]:
        """Read visible bottom cards during the pre-play doubling stage."""
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        return self._three_cards_reader.read_with_diagnostics(frame)

    def read_action_snapshot(
        self,
        frame: np.ndarray,
        *,
        expected_region: str,
        expected_self: bool,
        probe: bool = False,
        dependent_ready: bool = False,
    ) -> ActionDeltaSnapshot:
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        if expected_region not in {"left_play", "right_play", "my_play"}:
            raise ValueError("expected_region must be a known action-result region")
        if not isinstance(expected_self, bool):
            raise ValueError("expected_self must be a bool")  # noqa: TRY004 - stable API
        action_reader = self._action_result_reader
        if action_reader is None:
            raise LiveObservationIncomplete("action result reader is not configured")
        action_results = action_reader.read(frame, expected_region=expected_region)
        state = action_results.states.get(expected_region, ActionResultState.UNREADABLE)
        actionable = state in {ActionResultState.PASS, ActionResultState.PLAY}
        run_probe = probe and not actionable
        # Our own hand is the only persistent OCR source that can repair a
        # missed one-card local animation.  Read it throughout the user's
        # turn, even when the table/result edge itself was too brief to be
        # detected. Opponent turns remain on the inexpensive action path.
        need_hand = expected_self or run_probe
        # Opponent card values are recovered in the experimental live path
        # from one settled public-counter diff after a confirmed PLAY.  The
        # counter is a fixed, cheap ROI; it is intentionally read before and
        # throughout an opponent action window, rather than demanding table
        # card glyph OCR through bomb/rocket animations.
        need_tracker = not expected_self
        hand = self._hand_reader.read_with_diagnostics(frame) if need_hand else None
        tracker = (
            self._tracker_reader.read_with_diagnostics(frame)
            if need_tracker and self._tracker_reader is not None
            else None
        )
        # This is observer-only evidence: it makes the live audit readable
        # without letting unconfirmed table glyphs mutate game state. Keeping
        # it opt-in preserves the lean action path for callers that do not
        # need card-value audit output.
        if actionable and self._observe_action_cards:
            read_expected_table = getattr(
                self._table_reader,
                "read_region_with_diagnostics",
                None,
            )
            # Keep compatibility with small test doubles and third-party
            # readers.  The production TableReader implements the region-only
            # method, which prevents unrelated table regions from vetoing a
            # concrete expected play.
            table = (
                read_expected_table(frame, expected_region)
                if callable(read_expected_table)
                else self._table_reader.read_with_diagnostics(frame)
            )
        else:
            table = None
        markers = self._marker_reader.read(frame) if run_probe else None
        return ActionDeltaSnapshot(
            action_results=action_results,
            hand=hand,
            tracker=tracker,
            evidence_hash=hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest(),
            markers=markers,
            table=table,
        )

    def read_self_turn_snapshot(self, frame: np.ndarray) -> SelfTurnReadSnapshot:
        """Read a stable decision screen, rather than a transient action edge."""
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        if self._action_result_reader is None:
            raise LiveObservationIncomplete("action result reader is not configured")
        return SelfTurnReadSnapshot(
            hand=self._hand_reader.read_with_diagnostics(frame),
            action_results=self._action_result_reader.read(frame),
            # A local action is committed at its button-departure boundary.
            # This snapshot then rebuilds only the two opponents' settled
            # table regions; stale or animated cards in ``my_play`` must not
            # veto advice.
            table=self._table_reader.read_regions_with_diagnostics(
                frame,
                ("left_play", "right_play"),
            ),
            evidence_hash=hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest(),
        )

    def read(self, frame: np.ndarray, *, confirmation_count: int = 1) -> LiveObservation:
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        if confirmation_count < 1:
            raise ValueError("confirmation_count must be positive")
        hand = self._hand_reader.read(frame)
        if hand is None or not hand.cards:
            raise LiveObservationIncomplete("my_hand is not visible")
        tracker = self._tracker_reader.read(frame) if self._tracker_reader is not None else None
        table = self._table_reader.read(frame)
        three_cards = self._three_cards_reader.read(frame)
        markers = self._marker_reader.read(frame)
        action_results = (
            self._action_result_reader.read(frame)
            if self._action_result_reader is not None
            else None
        )
        cards_by_region = dict(table.detections)
        cards_by_region["my_hand"] = tuple(hand.cards)
        cards_by_region["three_cards"] = (
            tuple(three_cards.cards)
            if three_cards is not None and len(three_cards.cards) == 3
            else ()
        )
        distances = [
            hand.maximum_distance,
            table.maximum_distance,
        ]
        if three_cards is not None and len(three_cards.cards) == 3:
            distances.append(three_cards.maximum_distance)
        if markers is not None:
            distances.append(markers.maximum_distance)
        maximum_distance = max(distances)
        digest = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
        return LiveObservation(
            cards_by_region=MappingProxyType(cards_by_region),
            markers=frozenset(markers.markers) if markers is not None else frozenset(),
            tracker_counts=tuple(tracker.counts) if tracker is not None else (),
            minimum_score=max(0.0, min(1.0, 1.0 - maximum_distance)),
            evidence_hash=digest,
            confirmation_count=confirmation_count,
            action_results=MappingProxyType(dict(_action_states(action_results, table, markers))),
            action_visible_counts=(
                action_results.visible_counts
                if action_results is not None
                else MappingProxyType({})
            ),
        )

    def read_snapshot(self, frame: np.ndarray) -> ElementSnapshot:
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")

        hand = self._hand_reader.read_with_diagnostics(frame)
        tracker = (
            self._tracker_reader.read_with_diagnostics(frame)
            if self._tracker_reader is not None
            else None
        )
        table = self._table_reader.read_with_diagnostics(frame)
        three_cards = self._three_cards_reader.read_with_diagnostics(frame)
        markers = self._marker_reader.read(frame)
        action_results = (
            self._action_result_reader.read(frame)
            if self._action_result_reader is not None
            else None
        )
        distances = [
            result.value.maximum_distance
            for result in (hand, tracker, table, three_cards)
            if result is not None and result.value is not None
        ]
        if markers is not None:
            distances.append(markers.maximum_distance)
        digest = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
        return ElementSnapshot(
            hand=hand,
            tracker=tracker,
            table=table,
            three_cards=three_cards,
            markers=markers,
            evidence_hash=digest,
            maximum_success_distance=max(distances, default=0.0),
            action_results=action_results,
        )


def _action_states(
    action_results: ActionResultRead | None,
    table: TableRead | None,
    markers: LiveMarkerRead | None,
) -> Mapping[str, ActionResultState]:
    if action_results is not None:
        return action_results.states
    marker_names = markers.markers if markers is not None else frozenset()
    states: dict[str, ActionResultState] = {}
    for region in ("left_play", "right_play", "my_play"):
        play_visible = table is not None and bool(table.cards(region))
        pass_visible = f"{region.removesuffix('_play')}_pass" in marker_names
        if play_visible and pass_visible:
            state = ActionResultState.AMBIGUOUS
        elif play_visible:
            state = ActionResultState.PLAY
        elif pass_visible:
            state = ActionResultState.PASS
        else:
            state = ActionResultState.UNREADABLE
        states[region] = state
    return states
