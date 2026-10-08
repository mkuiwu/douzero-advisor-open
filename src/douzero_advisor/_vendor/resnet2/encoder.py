"""Pinned ResNet 2.0 card-play observation encoder.

This is the inference-only subset of ``douzero/env/env_res.py`` from
EdwardPooh/douzero-resnet-2.0 at commit
85afd773abd01c411f543d6ade5b99a4fde327d2. Feature values and ordering are
kept identical to the upstream ``_get_obs_resnet`` contract.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np


CARD_TO_COLUMN = {
    3: 0,
    4: 1,
    5: 2,
    6: 3,
    7: 4,
    8: 5,
    9: 6,
    10: 7,
    11: 8,
    12: 9,
    13: 10,
    14: 11,
    17: 12,
}
NUM_ONES_TO_ARRAY = {
    0: np.array([0, 0, 0, 0]),
    1: np.array([1, 0, 0, 0]),
    2: np.array([1, 1, 0, 0]),
    3: np.array([1, 1, 1, 0]),
    4: np.array([1, 1, 1, 1]),
}


def _get_one_hot_array(num_left_cards: int, max_num_cards: int) -> np.ndarray:
    one_hot = np.zeros(max_num_cards)
    one_hot[num_left_cards - 1] = 1
    return one_hot


def _cards2array(list_cards: list[int]) -> np.ndarray:
    if len(list_cards) == 0:
        return np.zeros(54, dtype=np.int8)

    matrix = np.zeros([4, 13], dtype=np.int8)
    jokers = np.zeros(2, dtype=np.int8)
    counter = Counter(list_cards)
    for card, num_times in counter.items():
        if card < 20:
            matrix[:, CARD_TO_COLUMN[card]] = NUM_ONES_TO_ARRAY[num_times]
        elif card == 20:
            jokers[0] = 1
        elif card == 30:
            jokers[1] = 1
    return np.concatenate((matrix.flatten("F"), jokers))


def _action_seq_list2array(action_seq_list: list[list[Any]]) -> np.ndarray:
    action_seq_array = np.ones((len(action_seq_list), 54)) * -1
    for row, list_cards in enumerate(action_seq_list):
        if list_cards:
            action_seq_array[row, :] = _cards2array(list_cards[1])
    return action_seq_array


def _process_action_seq(sequence: list[list[Any]], length: int = 15) -> list[list[Any]]:
    sequence = sequence[-length:].copy()
    sequence = sequence[::-1]
    if len(sequence) < length:
        empty_sequence = [[] for _ in range(length - len(sequence))]
        empty_sequence.extend(sequence)
        sequence = empty_sequence
    return sequence


def get_resnet2_observation(infoset: Any) -> dict[str, Any]:
    """Return the pinned upstream ResNet card-play observation."""
    position = infoset.player_position
    if position not in {"landlord", "landlord_up", "landlord_down"}:
        raise ValueError(f"unsupported ResNet 2.0 position: {position}")

    num_legal_actions = len(infoset.legal_actions)
    my_handcards = _cards2array(infoset.player_hand_cards)
    my_handcards_batch = np.repeat(
        my_handcards[np.newaxis, :], num_legal_actions, axis=0
    )

    other_handcards = _cards2array(infoset.other_hand_cards)
    if position == "landlord":
        bid_info = np.array(
            [[1, 0.5, 1], [1, 1, 1], [1, 5, -4], [1, 1, 1]]
        ).flatten()
    else:
        bid_info = np.array(
            [[1, 0.2, 1], [1, 3.5, 1], [1, 5, 4], [1.035, 1, 0.15]]
        ).flatten()
    bid_info_batch = np.repeat(bid_info[np.newaxis, :], num_legal_actions, axis=0)

    if position == "landlord":
        multiply_info = np.array([1, 1, 1])
    else:
        multiply_info = np.array([1, 2.5, 1.2])
    multiply_info_batch = np.repeat(
        multiply_info[np.newaxis, :], num_legal_actions, axis=0
    )

    three_landlord_cards = _cards2array(infoset.three_landlord_cards)

    my_action_batch = np.zeros(my_handcards_batch.shape)
    for index, action in enumerate(infoset.legal_actions):
        my_action_batch[index, :] = _cards2array(action)

    landlord_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict["landlord"], 20
    )
    landlord_up_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict["landlord_up"], 17
    )
    landlord_down_num_cards_left = _get_one_hot_array(
        infoset.num_cards_left_dict["landlord_down"], 17
    )

    # Pinned dead access from upstream. The resulting list is intentionally not
    # encoded; the adapter therefore supplies empty lists and never guesses hands.
    other_handcards_left_list: list[int] = []
    for other_position in ["landlord", "landlord_up", "landlord_up"]:
        if other_position != position:
            other_handcards_left_list.extend(infoset.all_handcards[other_position])

    landlord_played_cards = _cards2array(infoset.played_cards["landlord"])
    landlord_up_played_cards = _cards2array(infoset.played_cards["landlord_up"])
    landlord_down_played_cards = _cards2array(infoset.played_cards["landlord_down"])

    num_cards_left = np.hstack(
        (
            landlord_num_cards_left,
            landlord_up_num_cards_left,
            landlord_down_num_cards_left,
        )
    )

    x_batch = np.hstack((bid_info_batch, multiply_info_batch))
    x_no_action = np.hstack((bid_info, multiply_info))
    z = np.vstack(
        (
            num_cards_left,
            my_handcards,
            other_handcards,
            three_landlord_cards,
            landlord_played_cards,
            landlord_up_played_cards,
            landlord_down_played_cards,
            _action_seq_list2array(
                _process_action_seq(infoset.card_play_action_seq, 32)
            ),
        )
    )
    repeated_z = np.repeat(z[np.newaxis, :, :], num_legal_actions, axis=0)
    my_action_batch = my_action_batch[:, np.newaxis, :]
    z_batch = np.zeros([len(repeated_z), 40, 54], int)
    for index in range(len(repeated_z)):
        z_batch[index] = np.vstack((my_action_batch[index], repeated_z[index]))

    return {
        "position": position,
        "x_batch": x_batch.astype(np.float32),
        "z_batch": z_batch.astype(np.float32),
        "legal_actions": infoset.legal_actions,
        "x_no_action": x_no_action.astype(np.int8),
        "z": z.astype(np.int8),
    }
