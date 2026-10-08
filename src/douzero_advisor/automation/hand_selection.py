"""按已确认的手牌槽位选择牌；选择成功不等于已提交出牌。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from statistics import median
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from douzero_advisor.vision.hand_reader import HandRead


@dataclass(frozen=True, slots=True)
class HandSelectionTarget:
    slot: int
    rank: str
    point: tuple[int, int]


@dataclass(frozen=True, slots=True)
class HandSelectionPlan:
    targets: tuple[HandSelectionTarget, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class HandSelectionAttempt:
    slot: int
    rank: str
    point: tuple[int, int]
    previous_top: int
    observed_top: int | None
    status: str


@dataclass(frozen=True, slots=True)
class HandSelectionResult:
    """记录物理选择结果，供上层与最终提交确认严格区分。"""

    success: bool
    reason: str
    clicked_slots: tuple[int, ...]
    attempts: tuple[HandSelectionAttempt, ...] = ()


class HandSelectionPlanner:
    """Map a rank recommendation onto dynamically observed hand slots."""

    def __init__(self, *, frame_height: int, click_depth: float = 0.55) -> None:
        if type(frame_height) is not int or frame_height <= 0:
            raise ValueError("frame_height must be positive")
        if not 0.25 <= click_depth <= 0.8:
            raise ValueError("click_depth must be between 0.25 and 0.8")
        self._frame_height = frame_height
        self._click_depth = click_depth

    def plan(
        self,
        hand: HandRead,
        recommendation: Sequence[str],
    ) -> HandSelectionPlan:
        if not recommendation:
            return HandSelectionPlan((), "pass_recommendation")
        if Counter(recommendation) - Counter(hand.cards):
            return HandSelectionPlan((), "recommendation_not_in_hand")
        slots: list[int] = []
        for rank, amount in Counter(recommendation).items():
            matching = [index for index, card in enumerate(hand.cards) if card == rank]
            slots.extend(reversed(matching[-amount:]))
        slots.sort(reverse=True)
        targets = tuple(
            HandSelectionTarget(
                slot=slot,
                rank=hand.cards[slot],
                point=self.point_for_slot(hand, slot),
            )
            for slot in slots
        )
        return HandSelectionPlan(targets)

    def point_for_slot(self, hand: HandRead, slot: int) -> tuple[int, int]:
        if slot not in range(len(hand.cards)):
            raise ValueError("slot is outside the observed hand")
        if not (
            len(hand.cards)
            == len(hand.card_left_edges)
            == len(hand.card_tops)
        ):
            raise ValueError("hand card geometry is not aligned")
        gaps = [
            right - left
            for left, right in zip(
                hand.card_left_edges,
                hand.card_left_edges[1:],
            )
            if right > left
        ]
        exposed_width = (
            hand.card_left_edges[slot + 1] - hand.card_left_edges[slot]
            if slot + 1 < len(hand.card_left_edges)
            else int(round(median(gaps))) if gaps else 40
        )
        exposed_width = max(20, exposed_width)
        left = hand.card_left_edges[slot]
        top = hand.card_tops[slot]
        x = left + exposed_width // 2
        y = top + int(round((self._frame_height - top) * self._click_depth))
        return x, min(self._frame_height - 1, y)


class HandSelectionExecutor:
    """Reconcile the visually selected cards to the exact recommendation."""

    def __init__(
        self,
        *,
        capture_hand: Callable[[], HandRead | None],
        click_client: Callable[[tuple[int, int]], object],
        sleeper: Callable[[float], object] = time.sleep,
        frame_height: int = 819,
        click_settle_seconds: float = 0.5,
        minimum_raise_pixels: int = 12,
        maximum_edge_drift: int = 5,
        user_intervention_check: Callable[[], bool] | None = None,
        intervention_poll_seconds: float = 0.05,
    ) -> None:
        if click_settle_seconds < 0:
            raise ValueError("click_settle_seconds must be non-negative")
        if minimum_raise_pixels < 1:
            raise ValueError("minimum_raise_pixels must be positive")
        if maximum_edge_drift < 0:
            raise ValueError("maximum_edge_drift must be non-negative")
        if intervention_poll_seconds <= 0:
            raise ValueError("intervention_poll_seconds must be positive")
        self._capture_hand = capture_hand
        self._click_client = click_client
        self._sleeper = sleeper
        self._planner = HandSelectionPlanner(frame_height=frame_height)
        self._click_settle_seconds = click_settle_seconds
        self._minimum_raise_pixels = minimum_raise_pixels
        self._maximum_edge_drift = maximum_edge_drift
        self._user_intervention_check = user_intervention_check
        self._intervention_poll_seconds = intervention_poll_seconds

    def _is_user_intervening(self) -> bool:
        """检测用户是否正在按下鼠标左键（人工接管信号）。

        注意：GetAsyncKeyState 检测驱动层按键状态，自动化合成点击（mouse_event）也会触发该状态。
        因此本检测只能在合成点击的 LEFTUP 之后（即 settle 期间）或点击之前调用，
        绝不能在 LEFT_DOWN 到 LEFT_UP 的按住期间调用，否则会误判自己的点击为用户介入。
        """
        if self._user_intervention_check is None:
            return False
        try:
            return self._user_intervention_check()
        except Exception:
            # 检测异常不能阻塞选牌流程，降级为未检测到。
            return False

    def _settle_with_intervention_check(self) -> bool:
        """等待 UI 响应，期间每 50ms 检测用户介入；返回 True 表示检测到用户接管。"""
        # 用整数轮询次数避免浮点累加误差（0.05 * 10 不一定精确等于 0.5）。
        total_polls = max(
            1, int(round(self._click_settle_seconds / self._intervention_poll_seconds))
        )
        for _ in range(total_polls):
            if self._is_user_intervening():
                return True
            self._sleeper(self._intervention_poll_seconds)
        return False

    def select(
        self,
        initial: HandRead,
        recommendation: Sequence[str],
    ) -> HandSelectionResult:
        plan = self._planner.plan(initial, recommendation)
        if plan.reason is not None:
            return HandSelectionResult(
                success=plan.reason == "pass_recommendation",
                reason=plan.reason,
                clicked_slots=(),
            )
        # 开始选牌前检测用户是否已在操作；用户优先，立即放弃自动化。
        if self._is_user_intervening():
            return HandSelectionResult(False, "user_intervention", (), ())
        current = initial
        resting_top = max(initial.card_tops)
        target_counts = Counter(recommendation)
        preferred_slots = tuple(target.slot for target in plan.targets)
        preferred_slot_set = frozenset(preferred_slots)
        maximum_adjustments = min(
            len(initial.cards) * 2,
            max(4, len(recommendation) * 2 + 4),
        )
        clicked: list[int] = []
        selection_attempt_slots: set[int] = set()
        attempts: list[HandSelectionAttempt] = []

        def selected_slots(hand: HandRead) -> tuple[int, ...]:
            return tuple(
                slot
                for slot, top in enumerate(hand.card_tops)
                if top <= resting_top - self._minimum_raise_pixels
            )

        for _ in range(maximum_adjustments):
            selected_before = selected_slots(current)
            selected_counts = Counter(current.cards[slot] for slot in selected_before)
            if selected_counts == target_counts:
                for slot in preferred_slots:
                    if slot in selected_before and slot not in selection_attempt_slots:
                        point = self._planner.point_for_slot(current, slot)
                        top = current.card_tops[slot]
                        attempts.append(
                            HandSelectionAttempt(
                                slot,
                                current.cards[slot],
                                point,
                                top,
                                top,
                                "already_selected",
                            )
                        )
                return HandSelectionResult(
                    True,
                    "selected",
                    tuple(clicked),
                    tuple(attempts),
                )

            excess = selected_counts - target_counts
            if excess:
                candidates = [
                    slot
                    for slot in selected_before
                    if excess[current.cards[slot]] > 0
                ]
                candidates.sort(key=lambda slot: (slot in preferred_slot_set, slot))
                slot = candidates[0]
                selecting = False
            else:
                missing = target_counts - selected_counts
                selected_set = frozenset(selected_before)
                candidates = [
                    slot
                    for slot in preferred_slots
                    if slot not in selected_set and missing[current.cards[slot]] > 0
                ]
                if not candidates:
                    candidates = [
                        slot
                        for slot, rank in reversed(tuple(enumerate(current.cards)))
                        if slot not in selected_set and missing[rank] > 0
                    ]
                if not candidates:
                    return HandSelectionResult(
                        False,
                        "selection_reconciliation_unavailable",
                        tuple(clicked),
                        tuple(attempts),
                    )
                slot = candidates[0]
                selecting = True

            # 点击前再次检测用户是否已介入，避免在用户操作时抢鼠标。
            if self._is_user_intervening():
                return HandSelectionResult(
                    False, "user_intervention", tuple(clicked), tuple(attempts)
                )

            rank = current.cards[slot]
            point = self._planner.point_for_slot(current, slot)
            previous_top = current.card_tops[slot]
            try:
                self._click_client(point)
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"
                attempts.append(
                    HandSelectionAttempt(
                        slot,
                        rank,
                        point,
                        previous_top,
                        None,
                        reason,
                    )
                )
                return HandSelectionResult(
                    False,
                    reason,
                    tuple(clicked),
                    tuple(attempts),
                )
            # settle 期间每 50ms 轮询检测用户介入；检测到则立即放弃，不再拍牌验证。
            if self._settle_with_intervention_check():
                return HandSelectionResult(
                    False, "user_intervention", tuple(clicked), tuple(attempts)
                )
            observed = self._capture_hand()
            if observed is None:
                attempts.append(
                    HandSelectionAttempt(
                        slot,
                        rank,
                        point,
                        previous_top,
                        None,
                        "hand_unreadable_after_click",
                    )
                )
                return HandSelectionResult(
                    False,
                    "hand_unreadable_after_click",
                    tuple(clicked),
                    tuple(attempts),
                )
            observed_top = observed.card_tops[slot] if slot < len(observed.card_tops) else None
            if observed.cards != initial.cards or len(observed.card_left_edges) != len(
                initial.card_left_edges
            ) or observed_top is None:
                attempts.append(
                    HandSelectionAttempt(
                        slot,
                        rank,
                        point,
                        previous_top,
                        observed_top,
                        "hand_changed_during_selection",
                    )
                )
                return HandSelectionResult(
                    False,
                    "hand_changed_during_selection",
                    tuple(clicked),
                    tuple(attempts),
                )
            if any(
                abs(after - before) > self._maximum_edge_drift
                for before, after in zip(
                    initial.card_left_edges,
                    observed.card_left_edges,
                    strict=True,
                )
            ):
                attempts.append(
                    HandSelectionAttempt(
                        slot,
                        rank,
                        point,
                        previous_top,
                        observed_top,
                        "hand_geometry_shifted",
                    )
                )
                return HandSelectionResult(
                    False,
                    "hand_geometry_shifted",
                    tuple(clicked),
                    tuple(attempts),
                )

            selected_after = selected_slots(observed)
            expected_change = (
                slot in selected_after if selecting else slot not in selected_after
            )
            if expected_change:
                status = "selected" if selecting else "deselected"
            elif selected_after != selected_before:
                status = "selection_redirected" if selecting else "deselection_redirected"
            else:
                reason = (
                    "clicked_card_not_selected"
                    if selecting
                    else "clicked_card_not_deselected"
                )
                attempts.append(
                    HandSelectionAttempt(
                        slot,
                        rank,
                        point,
                        previous_top,
                        observed_top,
                        reason,
                    )
                )
                return HandSelectionResult(
                    False,
                    reason,
                    tuple(clicked),
                    tuple(attempts),
                )
            attempts.append(
                HandSelectionAttempt(
                    slot,
                    rank,
                    point,
                    previous_top,
                    observed_top,
                    status,
                )
            )
            clicked.append(slot)
            if selecting:
                selection_attempt_slots.add(slot)
            current = observed
        return HandSelectionResult(
            False,
            "selection_reconciliation_limit",
            tuple(clicked),
            tuple(attempts),
        )


class AutoHandSelector:
    """Run selection at most once for each emitted advice result."""

    def __init__(
        self,
        *,
        executor: HandSelectionExecutor,
        sink: Callable[[str], object] = print,
        json_mode: bool = False,
    ) -> HandSelectionResult | None:
        self._executor = executor
        self._sink = sink
        self._json_mode = json_mode
        self._handled_advice_keys: set[str] = set()

    def handle(
        self,
        advice_key: str,
        hand: HandRead,
        recommendation: Sequence[str],
    ) -> None:
        if advice_key in self._handled_advice_keys:
            return
        self._handled_advice_keys.add(advice_key)
        ranks = tuple(recommendation)
        try:
            result = self._executor.select(hand, ranks)
        except Exception as error:
            result = HandSelectionResult(
                False,
                f"{type(error).__name__}: {error}",
                (),
            )
            self._emit_failure(result.reason)
            return result
        if result.reason == "pass_recommendation":
            return result
        if not result.success:
            self._emit_failure(result.reason)
            return result
        if self._json_mode:
            self._sink(
                json.dumps(
                    {
                        "auto_select": {
                            "status": "selected",
                            "cards": list(ranks),
                        }
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            self._sink(
                f"【选牌】已自动选中 {' '.join(ranks)}；请检查后手工出牌"
            )
        return result

    def _emit_failure(self, reason: str) -> None:
        if self._json_mode:
            self._sink(
                json.dumps(
                    {
                        "auto_select": {
                            "status": "stopped",
                            "reason": reason,
                        }
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            self._sink(f"【选牌】验证失败（{reason}），已停止，请手工接管")
