from types import SimpleNamespace

from douzero_advisor.recognition_service.tasks import _local_turn_actions


class _Table:
    def __init__(self, cards: dict[str, tuple[str, ...]]) -> None:
        self._cards = cards

    def cards(self, region: str) -> tuple[str, ...]:
        return self._cards.get(region, ())


def _snapshot(
    cards: dict[str, tuple[str, ...]], states: dict[str, str]
) -> SimpleNamespace:
    return SimpleNamespace(
        table=SimpleNamespace(value=_Table(cards)),
        action_results=SimpleNamespace(states=states),
    )


def test_local_turn_uses_read_table_cards_even_when_action_presence_waits() -> None:
    """本方出牌前，已读到的左右桌面牌必须直接入历史，不能被动作区 wait 否决。"""

    actions, unreadable = _local_turn_actions(
        _snapshot(
            {"left_play": ("D",), "right_play": ("K",)},
            {"left_play": "wait", "right_play": "play"},
        ),
        "landlord_down",
        ("landlord", "landlord_up"),
    )

    assert actions == {
        "landlord": ("PLAY", ("D",)),
        "landlord_up": ("PLAY", ("K",)),
    }
    assert unreadable == ()


def test_local_turn_uses_pass_only_when_the_table_has_no_cards() -> None:
    """不出标记只在对应桌面区没有有效牌点时补充 PASS，不能覆盖已读出牌。"""

    actions, unreadable = _local_turn_actions(
        _snapshot(
            {"left_play": ("D",), "right_play": ()},
            {"left_play": "pass", "right_play": "pass"},
        ),
        "landlord_down",
        ("landlord", "landlord_up"),
    )

    assert actions == {
        "landlord": ("PLAY", ("D",)),
        "landlord_up": ("PASS", ()),
    }
    assert unreadable == ()


def test_local_turn_marks_play_without_cards_as_unreadable() -> None:
    """动作区仅证明有人出牌而桌面牌未读全时，必须等待而不能臆造牌点或不出。"""

    actions, unreadable = _local_turn_actions(
        _snapshot({"left_play": ()}, {"left_play": "play"}),
        "landlord_down",
        ("landlord",),
    )

    assert actions == {}
    assert unreadable == ("landlord",)
