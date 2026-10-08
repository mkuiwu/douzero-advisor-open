from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Seat(str, Enum):
    LANDLORD = "landlord"
    LANDLORD_DOWN = "landlord_down"
    LANDLORD_UP = "landlord_up"

    @property
    def next(self) -> Seat:
        return {
            Seat.LANDLORD: Seat.LANDLORD_DOWN,
            Seat.LANDLORD_DOWN: Seat.LANDLORD_UP,
            Seat.LANDLORD_UP: Seat.LANDLORD,
        }[self]


class TrackerPhase(str, Enum):
    WAITING_DEAL = "waiting_deal"
    TRACKING = "tracking"
    COMPLETE = "complete"
    UNCERTAIN = "uncertain"


class UncertainReason(str, Enum):
    INVALID_DEAL = "invalid_deal"
    NOT_TRACKING = "not_tracking"
    INVALID_EVIDENCE = "invalid_evidence"
    DUPLICATE_EVIDENCE = "duplicate_evidence"
    WRONG_TURN = "wrong_turn"
    EMPTY_MOVE = "empty_move"
    LEADING_PASS = "leading_pass"
    CARD_OVERDRAW = "card_overdraw"
    SELF_HAND_MISMATCH = "self_hand_mismatch"


@dataclass(frozen=True)
class MoveEvent:
    seat: Seat
    cards: tuple[int, ...]
    is_pass: bool
    evidence_hash: str


@dataclass(frozen=True)
class GameState:
    phase: TrackerPhase
    uncertain_reason: UncertainReason | None
    role: Seat | None
    my_cards: tuple[int, ...]
    three_landlord_cards: tuple[int, ...]
    played_cards: Mapping[Seat, tuple[int, ...]]
    history: tuple[MoveEvent, ...]
    remaining_cards: Mapping[Seat, int]
    current_player: Seat | None
    last_move: tuple[int, ...]
    last_two_moves: tuple[tuple[int, ...], ...]
    last_pid: Seat | None
    bomb_num: int
    evidence_hashes: frozenset[str]
