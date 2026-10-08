from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import cycle

from douzero_advisor.automation.hand_selection import (
    AutoHandSelector,
    HandSelectionExecutor,
    HandSelectionPlanner,
    HandSelectionResult,
)


@dataclass(frozen=True)
class _Hand:
    cards: tuple[str, ...]
    card_left_edges: tuple[int, ...]
    card_tops: tuple[int, ...]
    maximum_distance: float


def _hand(
    cards=("2", "A", "K", "K", "K", "7"),
    left_edges=(300, 350, 400, 450, 500, 550),
    tops=(600, 600, 600, 600, 600, 600),
) -> _Hand:
    return _Hand(
        cards=tuple(cards),
        card_left_edges=tuple(left_edges),
        card_tops=tuple(tops),
        maximum_distance=0.1,
    )


def test_planner_chooses_rightmost_duplicates_and_current_frame_points() -> None:
    plan = HandSelectionPlanner(frame_height=819).plan(_hand(), ("K", "K", "7"))

    assert [target.slot for target in plan.targets] == [5, 4, 3]
    assert [target.rank for target in plan.targets] == ["7", "K", "K"]
    assert [target.point for target in plan.targets] == [(575, 720), (525, 720), (475, 720)]


def test_planner_rejects_a_recommendation_not_contained_in_the_hand() -> None:
    plan = HandSelectionPlanner(frame_height=819).plan(_hand(), ("K", "K", "K", "K"))

    assert plan.targets == ()
    assert plan.reason == "recommendation_not_in_hand"


def test_executor_clicks_each_target_only_after_the_previous_card_rose() -> None:
    initial = _hand()
    frames = [
        replace(initial, card_tops=(600, 600, 600, 600, 600, 574)),
        replace(initial, card_tops=(600, 600, 600, 600, 574, 574)),
        replace(initial, card_tops=(600, 600, 600, 574, 574, 574)),
    ]
    clicks: list[tuple[int, int]] = []

    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )
    result = executor.select(initial, ("K", "K", "7"))

    assert result.success is True
    assert result.clicked_slots == (5, 4, 3)
    assert clicks == [(575, 720), (525, 720), (475, 720)]
    assert [
        (
            attempt.slot,
            attempt.rank,
            attempt.point,
            attempt.previous_top,
            attempt.observed_top,
            attempt.status,
        )
        for attempt in result.attempts
    ] == [
        (5, "7", (575, 720), 600, 574, "selected"),
        (4, "K", (525, 720), 600, 574, "selected"),
        (3, "K", (475, 720), 600, 574, "selected"),
    ]


def test_executor_skips_a_target_the_game_already_selected() -> None:
    initial = _hand(
        cards=("K", "K"),
        left_edges=(400, 450),
        tops=(600, 600),
    )
    frames = [
        replace(initial, card_tops=(574, 574)),
        replace(initial, card_tops=(600, 574)),
    ]
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is True
    assert result.reason == "selected"
    assert result.clicked_slots == (1,)
    assert clicks == [(475, 720)]
    assert [(attempt.slot, attempt.status) for attempt in result.attempts] == [
        (1, "selected"),
        (0, "already_selected"),
    ]


def test_executor_deselects_an_unwanted_rank_auto_selected_with_target() -> None:
    initial = _hand(
        cards=("K", "K", "Q"),
        left_edges=(400, 450, 500),
        tops=(600, 600, 600),
    )
    frames = [
        replace(initial, card_tops=(600, 574, 574)),
        replace(initial, card_tops=(600, 574, 600)),
    ]
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K",))

    assert result.success is True
    assert result.clicked_slots == (1, 2)
    assert clicks == [(475, 720), (525, 709)]
    assert [(attempt.slot, attempt.status) for attempt in result.attempts] == [
        (1, "selected"),
        (2, "deselected"),
    ]


def test_executor_deselects_an_extra_same_rank_auto_selected_with_pair() -> None:
    initial = _hand(
        cards=("K", "K", "K"),
        left_edges=(400, 450, 500),
        tops=(600, 600, 600),
    )
    frames = [
        replace(initial, card_tops=(574, 574, 574)),
        replace(initial, card_tops=(600, 574, 574)),
    ]
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is True
    assert result.clicked_slots == (2, 0)
    assert clicks == [(525, 720), (425, 709)]
    assert [(attempt.slot, attempt.status) for attempt in result.attempts] == [
        (2, "selected"),
        (0, "deselected"),
        (1, "already_selected"),
    ]


def test_executor_cleans_a_preselected_unwanted_card_before_selecting_target() -> None:
    initial = _hand(
        cards=("K", "Q"),
        left_edges=(400, 450),
        tops=(600, 574),
    )
    frames = [
        replace(initial, card_tops=(600, 600)),
        replace(initial, card_tops=(574, 600)),
    ]
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K",))

    assert result.success is True
    assert result.clicked_slots == (1, 0)
    assert clicks == [(475, 709), (425, 720)]
    assert [(attempt.slot, attempt.status) for attempt in result.attempts] == [
        (1, "deselected"),
        (0, "selected"),
    ]


def test_executor_stops_when_game_selection_keeps_oscillating() -> None:
    initial = _hand(
        cards=("K", "Q"),
        left_edges=(400, 450),
        tops=(600, 600),
    )
    frames = [
        replace(initial, card_tops=(574, 574)),
        replace(initial, card_tops=(600, 574)),
        replace(initial, card_tops=(574, 574)),
        replace(initial, card_tops=(600, 574)),
    ]
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K",))

    assert result.success is False
    assert result.reason == "selection_reconciliation_limit"
    assert result.clicked_slots == (0, 1, 1, 1)
    assert clicks == [(425, 720), (475, 709), (475, 709), (475, 709)]


def test_executor_caps_single_card_reconciliation_in_a_large_hand() -> None:
    cards = ("K", "Q") + tuple(f"R{index}" for index in range(18))
    initial = _hand(
        cards=cards,
        left_edges=tuple(range(100, 700, 30)),
        tops=(600,) * 20,
    )
    both_selected = replace(initial, card_tops=(574, 574) + (600,) * 18)
    only_extra_selected = replace(initial, card_tops=(600, 574) + (600,) * 18)
    frames = cycle((both_selected, only_extra_selected))
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: next(frames),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K",))

    assert result.success is False
    assert result.reason == "selection_reconciliation_limit"
    assert len(result.clicked_slots) == 6
    assert len(clicks) == 6


def test_executor_stops_without_next_click_when_card_did_not_rise() -> None:
    initial = _hand()
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: initial,
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is False
    assert result.reason == "clicked_card_not_selected"
    assert result.clicked_slots == ()
    assert clicks == [(525, 720)]


def test_executor_waits_a_human_paced_interval_before_each_verification() -> None:
    initial = _hand()
    frames = [
        replace(initial, card_tops=(600, 600, 600, 600, 574, 600)),
        replace(initial, card_tops=(600, 600, 600, 574, 574, 600)),
    ]
    sleeps: list[float] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=lambda _point: None,
        sleeper=sleeps.append,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is True
    # 每次 settle 0.5s 以 50ms 轮询，共 10 次 sleep；两次 settle 共 20 次。
    assert sleeps == [0.05] * 20


def test_executor_does_nothing_for_pass_recommendation() -> None:
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: _hand(),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
    )

    result = executor.select(_hand(), ())

    assert result.success is True
    assert result.reason == "pass_recommendation"
    assert clicks == []


def test_executor_aborts_before_any_click_when_user_already_pressed() -> None:
    """用户已在操作时开始选牌：立即放弃，不抢鼠标。"""
    initial = _hand()
    clicks: list[tuple[int, int]] = []
    executor = HandSelectionExecutor(
        capture_hand=lambda: initial,
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
        user_intervention_check=lambda: True,
    )

    result = executor.select(initial, ("K",))

    assert result.success is False
    assert result.reason == "user_intervention"
    assert clicks == []


def test_executor_aborts_during_settle_when_user_presses() -> None:
    """第一次点击后 settle 期间用户按下鼠标：点击已发生但未验证，立即中止后续选牌。"""
    initial = _hand()
    # 第一次点击后牌被顶起，但 settle 期间用户介入，不再验证。
    frames = [
        replace(initial, card_tops=(600, 600, 600, 600, 574, 600)),
    ]
    clicks: list[tuple[int, int]] = []
    sleeps: list[float] = []
    # 前 2 次轮询返回 False（未介入），第 3 次返回 True（用户按下）。
    # 注意：select 开始前和点击前各检测一次也会消费状态，需要预填。
    intervention_states = [False, False, False, True]

    def intervention_check() -> bool:
        if intervention_states:
            return intervention_states.pop(0)
        return False

    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=sleeps.append,
        user_intervention_check=intervention_check,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is False
    assert result.reason == "user_intervention"
    # settle 期间中止，点击已发生但尚未验证，所以 clicked_slots 为空。
    assert result.clicked_slots == ()
    assert len(clicks) == 1
    # settle 第 1 次检测 False 后 sleep，第 2 次检测 True 立即中止，所以只有 1 次 sleep。
    assert sleeps == [0.05]


def test_executor_aborts_before_next_click_when_user_presses() -> None:
    """第一次选牌成功后，第二次点击前用户按下鼠标：中止，不再点击第二张。"""
    initial = _hand()
    # 第一张 K 顶起后，第二次循环点击前用户介入。
    first_frame = replace(initial, card_tops=(600, 600, 600, 600, 574, 600))
    clicks: list[tuple[int, int]] = []
    sleeps: list[float] = []
    # 检测顺序：select 开始前 → 循环1点击前 → 循环1 settle(1次) → 循环2点击前
    # 前 3 次返回 False，第 4 次返回 True。
    intervention_calls = [False, False, False, True]

    def intervention_check() -> bool:
        return intervention_calls.pop(0)

    executor = HandSelectionExecutor(
        capture_hand=lambda: first_frame,
        click_client=clicks.append,
        sleeper=sleeps.append,
        click_settle_seconds=0.05,  # 缩短 settle 到 1 次轮询，精确控制检测次数
        user_intervention_check=intervention_check,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is False
    assert result.reason == "user_intervention"
    # 第一次点击已验证成功（clicked_slots 含 4），第二次点击前中止。
    assert result.clicked_slots == (4,)
    assert len(clicks) == 1


def test_executor_ignores_intervention_check_exceptions() -> None:
    """用户介入检测函数抛异常：降级为未检测到，不阻塞正常选牌流程。"""
    initial = _hand()
    frames = [
        replace(initial, card_tops=(600, 600, 600, 600, 574, 600)),
        replace(initial, card_tops=(600, 600, 600, 574, 574, 600)),
    ]
    clicks: list[tuple[int, int]] = []

    def bad_check() -> bool:
        raise OSError("GetAsyncKeyState failed")

    executor = HandSelectionExecutor(
        capture_hand=lambda: frames.pop(0),
        click_client=clicks.append,
        sleeper=lambda _seconds: None,
        user_intervention_check=bad_check,
    )

    result = executor.select(initial, ("K", "K"))

    assert result.success is True
    assert result.clicked_slots == (4, 3)


class _Executor:
    def __init__(self, result: HandSelectionResult) -> None:
        self.result = result
        self.calls: list[tuple[_Hand, tuple[str, ...]]] = []

    def select(
        self,
        hand: _Hand,
        recommendation: tuple[str, ...],
    ) -> HandSelectionResult:
        self.calls.append((hand, recommendation))
        return self.result


def test_auto_selector_emits_success_and_never_repeats_one_advice() -> None:
    lines: list[str] = []
    executor = _Executor(HandSelectionResult(True, "selected", (5, 4, 3)))
    selector = AutoHandSelector(executor=executor, sink=lines.append)

    first = selector.handle("advice-1", _hand(), ("K", "K", "7"))
    duplicate = selector.handle("advice-1", _hand(), ("K", "K", "7"))

    assert executor.calls == [(_hand(), ("K", "K", "7"))]
    assert first == executor.result
    assert duplicate is None
    assert lines == ["【选牌】已自动选中 K K 7；请检查后手工出牌"]


def test_auto_selector_reports_verification_failure_for_manual_takeover() -> None:
    lines: list[str] = []
    executor = _Executor(
        HandSelectionResult(False, "clicked_card_not_selected", (5,))
    )
    selector = AutoHandSelector(executor=executor, sink=lines.append)

    selector.handle("advice-2", _hand(), ("K", "K"))

    assert lines == [
        "【选牌】验证失败（clicked_card_not_selected），已停止，请手工接管"
    ]


def test_auto_selector_keeps_pass_recommendation_silent() -> None:
    lines: list[str] = []
    executor = _Executor(HandSelectionResult(True, "pass_recommendation", ()))
    selector = AutoHandSelector(executor=executor, sink=lines.append)

    selector.handle("advice-pass", _hand(), ())

    assert executor.calls == [(_hand(), ())]
    assert lines == []
