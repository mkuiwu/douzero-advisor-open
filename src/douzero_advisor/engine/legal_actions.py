"""复用固定版本 DouZero 环境计算合法动作，避免自行复制复杂牌型规则。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from douzero.env.game import GameEnv


def legal_actions_for(
    hand: Iterable[int], action_sequence: Sequence[Sequence[int]]
) -> list[list[int]]:
    """从固定原版规则实现返回当前手牌与历史下全部合法动作。"""
    env = GameEnv(players={})
    env.acting_player_position = "landlord"
    env.info_sets["landlord"].player_hand_cards = sorted(hand)
    env.card_play_action_seq = [sorted(action) for action in action_sequence]
    return [list(action) for action in env.get_legal_card_play_actions()]
