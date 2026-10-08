"""构造与固定 ResNet2 上游编码器完全兼容的 ``InfoSet`` 与观测张量。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from douzero.env.game import InfoSet

from douzero_advisor._vendor.resnet2.encoder import get_resnet2_observation
from douzero_advisor.cards import FULL_DECK
from douzero_advisor.engine.legal_actions import legal_actions_for
from douzero_advisor.state.models import GameState, Seat, TrackerPhase


def build_resnet2_infoset(state: GameState) -> InfoSet:
    """Adapt a verified tracker snapshot to the pinned ResNet 2.0 contract."""
    if (
        state.phase is not TrackerPhase.TRACKING
        or state.role is None
        or state.current_player is not state.role
    ):
        raise ValueError("an InfoSet can only be built for the tracked advisor turn")

    player_position = state.role.value
    action_sequence = [
        [event.seat.value, list(event.cards)] for event in state.history
    ]
    plain_action_sequence = [list(event.cards) for event in state.history]
    played_cards = {seat.value: list(state.played_cards[seat]) for seat in Seat}

    aggregate_other_cards = Counter(FULL_DECK)
    aggregate_other_cards.subtract(state.my_cards)
    for cards in played_cards.values():
        aggregate_other_cards.subtract(cards)
    if any(count < 0 for count in aggregate_other_cards.values()):
        raise ValueError("tracked cards exceed the full deck")

    last_move_dict = {seat.value: [] for seat in Seat}
    for event in state.history:
        last_move_dict[event.seat.value] = list(event.cards)

    infoset = InfoSet(player_position)
    infoset.player_hand_cards = list(state.my_cards)
    infoset.num_cards_left_dict = {
        seat.value: state.remaining_cards[seat] for seat in Seat
    }
    infoset.three_landlord_cards = list(state.three_landlord_cards)
    infoset.card_play_action_seq = action_sequence
    infoset.other_hand_cards = list(aggregate_other_cards.elements())
    infoset.legal_actions = legal_actions_for(state.my_cards, plain_action_sequence)
    infoset.last_move = list(state.last_move)
    infoset.last_two_moves = [list(move) for move in state.last_two_moves]
    infoset.last_move_dict = last_move_dict
    infoset.played_cards = played_cards
    infoset.all_handcards = {seat.value: [] for seat in Seat}
    infoset.last_pid = state.last_pid.value if state.last_pid is not None else None
    infoset.bomb_num = state.bomb_num
    return infoset


def build_resnet2_observation(infoset: Any) -> dict[str, Any]:
    """Build x[N,15] and z[N,40,54] using the pinned upstream encoder."""
    return get_resnet2_observation(infoset)
