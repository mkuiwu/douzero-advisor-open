from __future__ import annotations

from types import SimpleNamespace

import pytest

from douzero_advisor.automation.preplay_buttons import PreplayButtonExecutor


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _read(*texts: str, distance: float = 0.1, margin: float = 0.2):
    buttons = tuple(
        SimpleNamespace(
            text=text,
            distance=distance,
            margin=margin,
            box=SimpleNamespace(
                left=395 + index * 170,
                top=498,
                right=534 + index * 170,
                bottom=559,
                palette="orange" if index < 2 else "blue",
                center=(464 + index * 170, 528),
            ),
        )
        for index, text in enumerate(texts)
    )
    return SimpleNamespace(texts=tuple(texts), buttons=buttons)


def _advice(action: str):
    return SimpleNamespace(action=action)


def test_clicks_only_after_three_stable_target_reads() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(
        click_client=clicks.append,
        clock=clock,
        required_confirmations=3,
    )
    read = _read("rob", "no_rob")

    assert executor.observe(read, _advice("rob")) is None
    assert executor.observe(read) is None
    outcome = executor.observe(read)

    assert outcome is not None
    assert outcome.status == "clicked"
    assert outcome.reason == "button_invoked"
    assert clicks == [(464, 528)]


def test_missing_target_times_out_without_clicking_a_neighbor() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(
        click_client=clicks.append,
        clock=clock,
        timeout_seconds=2.0,
    )

    assert executor.observe(_read("no_double"), _advice("double")) is None
    clock.now = 2.0
    outcome = executor.observe(_read("no_double"))

    assert outcome.status == "stopped"
    assert outcome.action == "double"
    assert outcome.reason == "target_button_timeout"
    assert clicks == []


def test_no_double_waits_for_timeout_without_clicking_visible_button() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(
        click_client=clicks.append,
        clock=clock,
        timeout_seconds=2.0,
    )
    read = _read("double", "super_double", "no_double")

    assert executor.observe(read, _advice("no_double")) is None
    assert executor.observe(read) is None
    assert executor.observe(read) is None
    assert clicks == []

    clock.now = 2.0
    outcome = executor.observe(read)

    assert outcome is not None
    assert outcome.status == "stopped"
    assert outcome.action == "no_double"
    assert outcome.reason == "no_double_wait_timeout"
    assert clicks == []


@pytest.mark.parametrize("action,visible", [("no_call", ("call", "no_call")),
                                             ("no_rob", ("rob", "no_rob")),
                                             ("no_double", ("double", "super_double", "no_double"))])
def test_all_negative_preplay_actions_never_click(
    action: str, visible: tuple[str, ...]
) -> None:
    """验证不叫、不抢、不加倍三类负向建议都不会触发鼠标点击。"""
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(click_client=clicks.append, clock=clock, timeout_seconds=2.0)

    assert executor.observe(_read(*visible), _advice(action)) is None
    executor.observe(_read(*visible))
    assert clicks == []

def test_low_confidence_target_is_not_clicked() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(
        click_client=clicks.append,
        clock=clock,
        timeout_seconds=2.0,
    )
    read = _read("super_double", distance=0.3, margin=0.01)

    assert executor.observe(read, _advice("super_double")) is None
    clock.now = 2.0
    outcome = executor.observe(read)

    assert outcome is not None
    assert outcome.reason == "target_button_timeout"
    assert clicks == []


def test_reader_accepted_marginal_double_button_clicks_after_three_frames() -> None:
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(click_client=clicks.append)
    read = _read("super_double", distance=0.3354, margin=0.053)

    assert executor.observe(read, _advice("super_double")) is None
    assert executor.observe(read) is None
    outcome = executor.observe(read)

    assert outcome is not None
    assert outcome.reason == "button_invoked"
    assert clicks == [(464, 528)]


def test_super_double_falls_back_to_stable_double_button_when_item_card_is_absent() -> None:
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(click_client=clicks.append)
    read = _read("double", "no_double")

    assert executor.observe(read, _advice("super_double")) is None
    assert executor.observe(read) is None
    outcome = executor.observe(read)

    assert outcome is not None
    assert outcome.status == "clicked"
    assert outcome.action == "double"
    assert outcome.reason == "button_invoked_fallback_super_double_to_double"
    assert clicks == [(464, 528)]


def test_stage_change_stops_pending_action() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(click_client=clicks.append, clock=clock)

    assert executor.observe(_read("rob", "no_rob"), _advice("rob")) is None
    outcome = executor.observe(_read("double"))

    assert outcome is not None
    assert outcome.reason == "preplay_stage_changed"
    assert clicks == []


def test_formal_play_stage_stops_missing_preplay_target_immediately() -> None:
    clicks: list[tuple[int, int]] = []
    executor = PreplayButtonExecutor(click_client=clicks.append)

    assert executor.observe(_read("double"), _advice("no_double")) is None
    outcome = executor.observe(
        SimpleNamespace(stage="playing", texts=("play", "hint"), buttons=())
    )

    assert outcome is not None
    assert outcome.status == "stopped"
    assert outcome.reason == "preplay_stage_changed"
    assert clicks == []
