from __future__ import annotations

from dataclasses import dataclass

from douzero_advisor.automation.settlement_buttons import SettlementChangeTableExecutor
from douzero_advisor.vision.action_button_reader import ButtonBox
from douzero_advisor.vision.settlement_button_reader import SettlementButton, SettlementButtonRead


@dataclass
class Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


def _read(*, complete: bool = True, stage: str = "settlement") -> SettlementButtonRead:
    buttons = [
        SettlementButton(
            "reveal_start", "明牌开始×5", ButtonBox(507, 661, 698, 719, "blue"), 0.95
        )
    ]
    if complete:
        buttons.append(
            SettlementButton(
                "continue_game", "继续游戏", ButtonBox(757, 661, 948, 719, "orange"), 0.95
            )
        )
    return SettlementButtonRead(stage, tuple(buttons))  # type: ignore[arg-type]


def test_changes_table_after_three_complete_stable_reads() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = SettlementChangeTableExecutor(
        click_client=clicks.append,
        clock=clock,
        random_uniform=lambda _low, _high: 1.75,
    )

    assert executor.observe(_read()) is None
    assert executor.observe(_read()) is None
    assert executor.observe(_read()) is None
    clock.value = 1.74
    assert executor.observe(_read()) is None
    clock.value = 1.75
    outcome = executor.observe(_read())

    assert outcome is not None
    assert outcome.reason == "button_invoked"
    assert clicks == [(1116, 68)]


def test_does_not_click_partial_layout_and_times_out() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = SettlementChangeTableExecutor(
        click_client=clicks.append,
        timeout_seconds=2.0,
        clock=clock,
    )

    for _ in range(3):
        assert executor.observe(_read(complete=False)) is None
    clock.value = 2.0
    outcome = executor.observe(_read(complete=False))

    assert outcome is not None
    assert outcome.reason == "settlement_page_timeout"
    assert clicks == []


def test_requires_transition_after_click() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = SettlementChangeTableExecutor(
        click_client=clicks.append,
        clock=clock,
        minimum_click_delay_seconds=0,
        maximum_click_delay_seconds=0,
    )
    for _ in range(3):
        outcome = executor.observe(_read())
    outcome = executor.observe(_read())
    assert outcome is not None and outcome.reason == "button_invoked"

    clock.value = 1.0
    assert executor.observe(_read()) is None
    clock.value = 2.1
    outcome = executor.observe(_read())

    assert outcome is not None
    assert outcome.reason == "post_click_not_confirmed"


def test_evidence_change_during_delay_cancels_scheduled_click() -> None:
    clock = Clock()
    clicks: list[tuple[int, int]] = []
    executor = SettlementChangeTableExecutor(
        click_client=clicks.append,
        clock=clock,
        random_uniform=lambda _low, _high: 1.5,
    )
    for _ in range(3):
        assert executor.observe(_read()) is None

    clock.value = 1.5
    assert executor.observe(_read(complete=False)) is None
    assert clicks == []

    for _ in range(3):
        assert executor.observe(_read()) is None
    clock.value = 3.0
    outcome = executor.observe(_read())

    assert outcome is not None
    assert outcome.reason == "button_invoked"
    assert clicks == [(1116, 68)]
