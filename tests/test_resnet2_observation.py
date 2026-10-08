from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from private_asset_paths import PRIVATE_BENCHMARK_ROOT
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from torch import nn

from douzero_advisor._vendor.resnet2.encoder import get_resnet2_observation
from douzero_advisor.engine.resnet2_agent import SafeResNet2Agent
from douzero_advisor.engine.resnet2_observation import (
    build_resnet2_infoset,
    build_resnet2_observation,
)
from douzero_advisor.state.models import (
    GameState,
    MoveEvent,
    Seat,
    TrackerPhase,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models" / "resnet2" / "manifest.json"
GOLDEN = ROOT / "tests" / "fixtures" / "resnet2_encoder_golden.json"
PINNED_ENV_RES = PRIVATE_BENCHMARK_ROOT / "upstream/Douzero_Resnet/douzero/env/env_res.py"
PINNED_ENV_RES_SHA256 = "86ec7b7259ff728acf42ee1f61c48840ea35e8ecf946334fe4ae6bea4986ea5a"


def _event(seat: Seat, cards: tuple[int, ...], index: int) -> MoveEvent:
    return MoveEvent(
        seat=seat,
        cards=cards,
        is_pass=not cards,
        evidence_hash=f"event-{index}",
    )


def _state(
    *,
    role: Seat = Seat.LANDLORD_UP,
    history: tuple[MoveEvent, ...] | None = None,
) -> GameState:
    if history is None:
        history = (
            _event(Seat.LANDLORD, (13,), 0),
            _event(Seat.LANDLORD_DOWN, (), 1),
        )
    hand_size = 20 if role is Seat.LANDLORD else 17
    hand = (
        3,
        3,
        4,
        4,
        5,
        5,
        6,
        6,
        7,
        7,
        8,
        8,
        9,
        10,
        11,
        14,
        17,
        9,
        10,
        11,
    )[:hand_size]
    played_cards = {
        seat: tuple(card for event in history if event.seat is seat for card in event.cards)
        for seat in Seat
    }
    last_two = tuple(event.cards for event in history[-2:])
    last_non_pass = next(
        (event.cards for event in reversed(history) if event.cards), ()
    )
    return GameState(
        phase=TrackerPhase.TRACKING,
        uncertain_reason=None,
        role=role,
        my_cards=hand,
        three_landlord_cards=(12, 13, 14),
        played_cards=played_cards,
        history=history,
        remaining_cards={
            Seat.LANDLORD: 19,
            Seat.LANDLORD_DOWN: 17,
            Seat.LANDLORD_UP: 17,
        },
        current_player=role,
        last_move=last_non_pass,
        last_two_moves=last_two,
        last_pid=history[-1].seat if history else None,
        bomb_num=0,
        evidence_hashes=frozenset(event.evidence_hash for event in history),
    )


def _reference_infoset(position: str) -> SimpleNamespace:
    seats = ["landlord", "landlord_down", "landlord_up"]
    history = []
    actions = [[3], [], [4, 4], [20], [], [5, 6, 7, 8, 9]]
    for index in range(37):
        history.append([seats[index % 3], list(actions[index % len(actions)])])
    return SimpleNamespace(
        player_position=position,
        player_hand_cards=[3, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 20, 30],
        other_hand_cards=[3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 9, 10, 11, 12, 13, 14, 17],
        three_landlord_cards=[7, 17, 30],
        legal_actions=[[], [3], [4, 4], [20, 30]],
        num_cards_left_dict={"landlord": 9, "landlord_down": 11, "landlord_up": 7},
        all_handcards={"landlord": [], "landlord_down": [], "landlord_up": []},
        played_cards={
            "landlord": [6, 6],
            "landlord_down": [8],
            "landlord_up": [12, 12, 12],
        },
        card_play_action_seq=history,
    )


def _load_pinned_upstream_env_res():
    if not PINNED_ENV_RES.is_file():
        pytest.skip("pinned external ResNet 2.0 checkout is not available")
    assert hashlib.sha256(PINNED_ENV_RES.read_bytes()).hexdigest() == PINNED_ENV_RES_SHA256
    spec = importlib.util.spec_from_file_location("pinned_resnet2_env_res", PINNED_ENV_RES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("position", ["landlord", "landlord_down", "landlord_up"])
def test_encoder_is_array_identical_to_pinned_upstream_for_long_history(
    position: str,
) -> None:
    upstream = _load_pinned_upstream_env_res()
    infoset = _reference_infoset(position)

    expected = upstream.get_obs_res(infoset, model_type="resnet")
    actual = get_resnet2_observation(infoset)

    for name in ("x_batch", "z_batch", "x_no_action", "z"):
        np.testing.assert_array_equal(actual[name], expected[name])
        assert actual[name].dtype == expected[name].dtype
    assert actual["position"] == expected["position"]
    assert actual["legal_actions"] == expected["legal_actions"]
    assert actual["x_batch"].shape == (4, 15)
    assert actual["z_batch"].shape == (4, 40, 54)


@pytest.mark.parametrize("position", ["landlord", "landlord_down", "landlord_up"])
def test_encoder_matches_repository_golden_without_external_checkout(
    position: str,
) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["upstream_commit"] == "85afd773abd01c411f543d6ade5b99a4fde327d2"
    assert golden["upstream_source"] == "Douzero_Resnet/douzero/env/env_res.py"
    assert golden["upstream_source_sha256"] == PINNED_ENV_RES_SHA256

    observation = get_resnet2_observation(_reference_infoset(position))
    for name, expected in golden["arrays"][position].items():
        actual = observation[name]
        assert list(actual.shape) == expected["shape"]
        assert str(actual.dtype) == expected["dtype"]
        assert hashlib.sha256(actual.tobytes()).hexdigest() == expected["sha256"]


@pytest.mark.parametrize(
    ("position", "expected_x"),
    [
        (
            "landlord",
            [1, 0.5, 1, 1, 1, 1, 1, 5, -4, 1, 1, 1, 1, 1, 1],
        ),
        (
            "landlord_down",
            [1, 0.2, 1, 1, 3.5, 1, 1, 5, 4, 1.035, 1, 0.15, 1, 2.5, 1.2],
        ),
        (
            "landlord_up",
            [1, 0.2, 1, 1, 3.5, 1, 1, 5, 4, 1.035, 1, 0.15, 1, 2.5, 1.2],
        ),
    ],
)
def test_hardcoded_bid_and_multiply_features_match_upstream(
    position: str, expected_x: list[float]
) -> None:
    observation = get_resnet2_observation(_reference_infoset(position))

    np.testing.assert_allclose(observation["x_batch"][0], expected_x)


def test_infoset_history_keeps_seats_and_a_real_pass_without_guessing_hands() -> None:
    state = _state()

    infoset = build_resnet2_infoset(state)

    assert infoset.card_play_action_seq == [
        ["landlord", [13]],
        ["landlord_down", []],
    ]
    assert infoset.all_handcards == {
        "landlord": [],
        "landlord_down": [],
        "landlord_up": [],
    }
    assert infoset.other_hand_cards
    assert [] in infoset.legal_actions


def test_real_pass_row_is_zero_while_short_history_padding_is_minus_one() -> None:
    observation = build_resnet2_observation(build_resnet2_infoset(_state()))

    np.testing.assert_array_equal(observation["z"][36], np.full(54, -1, dtype=np.int8))
    np.testing.assert_array_equal(observation["z"][37], np.zeros(54, dtype=np.int8))


def test_all_handcards_is_dead_but_other_hand_cards_is_model_input() -> None:
    infoset = build_resnet2_infoset(_state())
    baseline = build_resnet2_observation(infoset)

    infoset.all_handcards = {
        "landlord": [20, 30],
        "landlord_down": [3, 3, 3, 3],
        "landlord_up": [17, 17, 17, 17],
    }
    all_hands_changed = build_resnet2_observation(infoset)
    np.testing.assert_array_equal(all_hands_changed["x_batch"], baseline["x_batch"])
    np.testing.assert_array_equal(all_hands_changed["z_batch"], baseline["z_batch"])

    infoset.other_hand_cards = []
    other_cards_changed = build_resnet2_observation(infoset)
    assert not np.array_equal(other_cards_changed["z_batch"], baseline["z_batch"])


def test_safe_agent_uses_the_formal_observation_builder() -> None:
    infoset = build_resnet2_infoset(_state())
    agent = SafeResNet2Agent.from_manifest("landlord_up", MANIFEST)

    class FixedValues(nn.Module):
        def forward(self, z, x, return_value=False):
            del x, return_value
            values = torch.arange(z.shape[0], dtype=torch.float32).reshape(-1, 1)
            return {"values": values}

    agent.model = FixedValues()

    assert agent.act(infoset) == infoset.legal_actions[-1]
