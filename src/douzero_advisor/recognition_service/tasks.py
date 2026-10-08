"""六种 RecognitionPort 的无历史帧稳定器。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from douzero_advisor.recognition_service.protocol import (
    PromptEntryMode,
    RecognitionCommand,
    TaskType,
    TurnEntryMode,
    failure_message,
    success_message,
    validate_cards,
)

_REGION_MAPS: dict[str, dict[str, str]] = {
    "landlord": {"left_play": "landlord_up", "right_play": "landlord_down"},
    "landlord_down": {"left_play": "landlord", "right_play": "landlord_up"},
    "landlord_up": {"left_play": "landlord_down", "right_play": "landlord"},
}
_LOCAL_TURN_ABSENT_FRAMES = 2


@dataclass(slots=True)
class RecognitionTask:
    """一个请求自己的候选、边沿和截止时间。"""

    command: RecognitionCommand
    deadline_at: float
    candidate: object | None = None
    candidate_count: int = 0
    capture_generation: int | None = None
    edge_armed: bool = False
    absent_count: int = 0
    unreadable_count: int = 0
    observe_deadline_started: bool = False
    # 慢边界任务（新局、结算）的最小采样间隔，单位为秒；0 表示不节流，每帧都观察。
    min_observation_interval: float = 0.0
    # 上次实际观察该任务时的单调时钟时间，单位为秒；用于采样节流判断。
    last_observed_at: float | None = None

    def reset_candidates(self) -> None:
        self.candidate = None
        self.candidate_count = 0

    def observe(self, facts: Any, now: float) -> dict[str, Any] | None:
        # 慢边界任务按固定间隔采样，避免每帧执行 reader 造成不必要的 CPU 占用。
        # 节流跳过的帧不更新任何候选状态，下一次到间隔后再完整处理。
        if (
            self.min_observation_interval > 0
            and self.last_observed_at is not None
            and now - self.last_observed_at < self.min_observation_interval
        ):
            return None
        self.last_observed_at = now
        if self.capture_generation != facts.generation:
            self.capture_generation = facts.generation
            self.reset_candidates()
            self.absent_count = 0
            self.unreadable_count = 0
            if self.command.prompt_entry_mode is PromptEntryMode.REQUIRE_CHANGE_THEN_NEW:
                self.edge_armed = False
            if (
                self.command.turn_entry_mode
                is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER
            ):
                self.edge_armed = False
                self.observe_deadline_started = False
                self.deadline_at = float("inf")
        unreadable = self._unreadable_local_turn_failure(facts)
        if unreadable is not None:
            return unreadable
        candidate = self._candidate(facts)
        self._arm_local_turn_observe_deadline(facts, now)
        if candidate is None:
            self.reset_candidates()
            return None
        signature, fields, required = candidate
        if signature == self.candidate:
            self.candidate_count += 1
        else:
            self.candidate = signature
            self.candidate_count = 1
        if self.candidate_count < required:
            return None
        return success_message(self.command, **fields)

    def _arm_local_turn_observe_deadline(self, facts: Any, now: float) -> None:
        command = self.command
        if command.task_type is not TaskType.LOCAL_TURN or self.observe_deadline_started:
            return
        if (
            command.turn_entry_mode is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER
            and not self.edge_armed
        ):
            return
        if facts.action_buttons_present is not True:
            return
        assert command.deadline_ms is not None
        self.deadline_at = now + command.deadline_ms / 1000.0
        self.observe_deadline_started = True

    def _candidate(
        self, facts: Any
    ) -> tuple[object, dict[str, Any], int] | None:
        task_type = self.command.task_type
        if task_type is TaskType.NEW_GAME:
            return _new_game_candidate(facts)
        if task_type is TaskType.PREPLAY_PROMPT:
            return self._preplay_candidate(facts)
        if task_type is TaskType.DEAL:
            return _deal_candidate(facts)
        if task_type is TaskType.LOCAL_TURN:
            return self._local_turn_candidate(facts)
        if task_type is TaskType.TURN_END:
            return self._turn_end_candidate(facts)
        if task_type is TaskType.SETTLEMENT:
            return _settlement_candidate(facts)
        raise AssertionError(f"unsupported task type: {task_type}")

    def _preplay_candidate(
        self, facts: Any
    ) -> tuple[object, dict[str, Any], int] | None:
        prompt = _prompt(facts.lifecycle)
        command = self.command
        if (
            command.prompt_entry_mode is PromptEntryMode.REQUIRE_CHANGE_THEN_NEW
            and not self.edge_armed
        ):
            if prompt is None:
                self.edge_armed = True
                return None
            if command.previous_prompt_type is None:
                return None
            if prompt[0] != command.previous_prompt_type:
                self.edge_armed = True
            if prompt[0] == command.previous_prompt_type:
                return None
        if prompt is None:
            return None
        prompt_type, available_actions = prompt
        hand = facts.hand
        if hand is None or len(hand) not in {17, 20} or not validate_cards(hand):
            return None
        bottom: tuple[str, ...] = ()
        if prompt_type == "double" and len(hand) == 20:
            bottom_value = facts.bottom_cards
            if bottom_value is None or not validate_cards(bottom_value, expected=3):
                return None
            bottom = bottom_value
        signature = (prompt_type, hand, bottom, available_actions)
        return signature, {
            "promptType": prompt_type,
            "hand": list(hand),
            "bottomCards": list(bottom),
            "availableActions": list(available_actions),
            "observedAt": facts.observed_at,
        }, 3

    def _local_turn_candidate(
        self, facts: Any
    ) -> tuple[object, dict[str, Any], int] | None:
        command = self.command
        buttons_present = facts.action_buttons_present
        ready = buttons_present is True
        snapshot = facts.self_turn
        if (
            command.turn_entry_mode
            is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER
            and not self.edge_armed
        ):
            if buttons_present is not False:
                self.absent_count = 0
                return None
            self.absent_count += 1
            if self.absent_count < _LOCAL_TURN_ABSENT_FRAMES:
                return None
            self.edge_armed = True
            self.absent_count = 0
            self.reset_candidates()
            return None
        if not ready:
            return None
        hand_value = getattr(getattr(snapshot, "hand", None), "value", None)
        hand = tuple(getattr(hand_value, "cards", ()))
        if not validate_cards(hand):
            return None
        local_seat = command.local_seat
        assert local_seat is not None
        actions, _unreadable = _local_turn_actions(
            snapshot, local_seat, command.required_actors
        )
        if any(actor not in actions for actor in command.required_actors):
            return None
        wire_actions = {
            seat: ([] if kind == "PASS" else list(cards))
            for seat, (kind, cards) in sorted(actions.items())
        }
        signature = (hand, tuple((key, item[0], item[1]) for key, item in sorted(actions.items())))
        return signature, {
            "currentHand": list(hand),
            "actionsBySeat": wire_actions,
            "observedAt": facts.observed_at,
        }, 2

    def _unreadable_local_turn_failure(self, facts: Any) -> dict[str, Any] | None:
        command = self.command
        if command.task_type is not TaskType.LOCAL_TURN:
            return None
        if (
            command.turn_entry_mode is TurnEntryMode.REQUIRE_EXIT_THEN_REENTER
            and not self.edge_armed
        ):
            self.unreadable_count = 0
            return None
        if facts.action_buttons_present is not True:
            self.unreadable_count = 0
            return None
        _actions, unreadable = _local_turn_actions(
            facts.self_turn, command.local_seat, command.required_actors
        )
        blocked = [actor for actor in command.required_actors if actor in unreadable]
        if not blocked:
            self.unreadable_count = 0
            return None
        self.unreadable_count += 1
        if self.unreadable_count < 2:
            return None
        return failure_message(
            command,
            "OBSERVATION_UNCERTAIN",
            "required actor action unreadable: " + ",".join(blocked),
        )

    def _turn_end_candidate(
        self, facts: Any
    ) -> tuple[object, dict[str, Any], int] | None:
        """只确认建议所在的本方回合已离开，不把中间画面当成下一次本方回合。"""

        command = self.command
        snapshot = facts.self_turn
        hand_value = getattr(getattr(snapshot, "hand", None), "value", None)
        current_hand = tuple(getattr(hand_value, "cards", ()))
        # 手牌是否可读是区分"画面稳定"和"动画遮挡"的关键信号：
        # 飞机/炸弹动画会短暂遮挡手牌导致 OCR 读不到，此时任何回合结束信号都不可信。
        hand_readable = validate_cards(current_hand)
        hand_changed = hand_readable and Counter(current_hand) != Counter(command.baseline_hand)

        if facts.action_buttons_present is False:
            # 出牌动画会短暂遮挡出牌按钮，此时手牌通常也不可读，不能判定回合结束，
            # 必须等待画面稳定。手牌一旦可读（无论出牌后减少还是 Pass 后不变），
            # 说明动画已结束，按钮消失构成回合结束证据。
            if not hand_readable:
                return None
            return ("buttons_absent",), {"observedAt": facts.observed_at}, 2

        if hand_changed:
            return ("hand_changed", tuple(sorted(Counter(current_hand).items()))), {
                "observedAt": facts.observed_at
            }, 2

        baseline_actions = dict(command.baseline_actions_by_seat)
        if not baseline_actions:
            return None
        current_actions, unreadable = _side_actions(
            snapshot, command.local_seat, tuple(baseline_actions)
        )
        if unreadable:
            return None
        observed_actions = {
            seat: ([] if kind == "PASS" else list(cards))
            for seat, (kind, cards) in current_actions.items()
        }
        if observed_actions != {
            seat: list(cards) for seat, cards in baseline_actions.items()
        }:
            signature = tuple(
                (seat, tuple(cards)) for seat, cards in sorted(observed_actions.items())
            )
            return ("side_actions_changed", signature), {
                "observedAt": facts.observed_at
            }, 2
        return None


def _new_game_candidate(facts: Any) -> tuple[object, dict[str, Any], int] | None:
    lifecycle = facts.lifecycle
    if getattr(lifecycle, "stage", None) != "preplay":
        return None
    texts = tuple(getattr(lifecycle, "texts", ()))
    if not texts:
        return None
    return (texts,), {"observedAt": facts.observed_at}, 2


def _prompt(lifecycle: Any) -> tuple[str, tuple[str, ...]] | None:
    if getattr(lifecycle, "stage", None) != "preplay":
        return None
    texts = tuple(getattr(lifecycle, "texts", ()))
    if "call" in texts:
        expected = ("call", "no_call")
        return ("call", expected) if set(expected).issubset(texts) else None
    if "rob" in texts:
        expected = ("rob", "no_rob")
        return ("rob", expected) if set(expected).issubset(texts) else None
    if any(text in texts for text in ("double", "super_double", "no_double")):
        # 超级加倍是可选按钮；只有普通加倍和不加倍时，这两个按钮就是完整布局。
        expected = (
            ("super_double", "double", "no_double")
            if "super_double" in texts
            else ("double", "no_double")
        )
        return ("double", expected) if set(expected).issubset(texts) else None
    return None


def _deal_candidate(facts: Any) -> tuple[object, dict[str, Any], int] | None:
    """识别正式出牌的首轮定位事实：地主、相对座位和地主首手只在此处确认一次。"""
    # 只要仍是叫/抢/加倍按钮，20 张手牌也不得提前闭合成地主 Deal。
    if getattr(facts.lifecycle, "stage", None) == "preplay":
        return None
    hand = facts.hand
    bottom = facts.bottom_cards
    if hand is None or bottom is None or not validate_cards(bottom, expected=3):
        return None
    if len(hand) == 20 and validate_cards(hand, expected=20):
        if facts.turn_ready is not True and getattr(facts.lifecycle, "stage", None) != "playing":
            return None
        fields = {
            "localSeat": "landlord",
            "hand": list(hand),
            "bottomCards": list(bottom),
            "landlordOpeningPlay": [],
            "observedAt": facts.observed_at,
        }
        return ("landlord", hand, bottom), fields, 3
    if len(hand) != 17 or not validate_cards(hand, expected=17):
        return None
    snapshot = facts.self_turn
    action_results = getattr(snapshot, "action_results", None)
    states = getattr(action_results, "states", {})
    table = getattr(getattr(snapshot, "table", None), "value", None)
    if table is None:
        return None
    plays = []
    for region in ("left_play", "right_play"):
        cards = tuple(table.cards(region))
        if _state_name(states.get(region)) == "play" and validate_cards(cards):
            plays.append((region, cards))
    if len(plays) != 1:
        return None
    region, opening = plays[0]
    local_seat = "landlord_down" if region == "left_play" else "landlord_up"
    fields = {
        "localSeat": local_seat,
        "hand": list(hand),
        "bottomCards": list(bottom),
        "landlordOpeningPlay": list(opening),
        "observedAt": facts.observed_at,
    }
    return (local_seat, hand, bottom, opening), fields, 3


def _side_actions(
    snapshot: Any,
    local_seat: str,
    required_actors: tuple[str, ...],
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], tuple[str, ...]]:
    action_results = getattr(snapshot, "action_results", None)
    states = getattr(action_results, "states", {})
    table = getattr(getattr(snapshot, "table", None), "value", None)
    if table is None:
        return {}, ()
    actions: dict[str, tuple[str, tuple[str, ...]]] = {}
    unreadable: list[str] = []
    for region, seat in _REGION_MAPS[local_seat].items():
        if seat not in required_actors:
            continue
        state = _state_name(states.get(region))
        if state == "pass":
            actions[seat] = ("PASS", ())
            continue
        if state == "play":
            cards = tuple(table.cards(region))
            if validate_cards(cards):
                actions[seat] = ("PLAY", cards)
            else:
                unreadable.append(seat)
            continue
        if state == "wait":
            continue
        if state == "unreadable":
            unreadable.append(seat)
    return actions, tuple(unreadable)


def _local_turn_actions(
    snapshot: Any,
    local_seat: str,
    required_actors: tuple[str, ...],
) -> tuple[dict[str, tuple[str, tuple[str, ...]]], tuple[str, ...]]:
    """汇总本方回合前两侧事实：桌面牌优先，不出标记只补充无牌区域。"""

    action_results = getattr(snapshot, "action_results", None)
    states = getattr(action_results, "states", {})
    table = getattr(getattr(snapshot, "table", None), "value", None)
    if table is None:
        return {}, ()
    actions: dict[str, tuple[str, tuple[str, ...]]] = {}
    unreadable: list[str] = []
    for region, seat in _REGION_MAPS[local_seat].items():
        if seat not in required_actors:
            continue
        cards = tuple(table.cards(region))
        # 桌面 Reader 已给出完整合法牌点时，该事实直接构成本座位的出牌；
        # 动作区域仅能补充无牌的 PASS 或报告牌面未读全，不能否决已读牌面。
        if validate_cards(cards):
            actions[seat] = ("PLAY", cards)
            continue
        state = _state_name(states.get(region))
        if state == "pass":
            actions[seat] = ("PASS", ())
        elif state in {"play", "unreadable", "ambiguous"}:
            unreadable.append(seat)
    return actions, tuple(unreadable)


def _state_name(value: object) -> str:
    """只读现有 Reader 的字符串枚举值，不导入其状态/模型依赖图。"""

    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else ""


def _settlement_candidate(facts: Any) -> tuple[object, dict[str, Any], int] | None:
    read = facts.settlement
    names = tuple(getattr(button, "name", None) for button in getattr(read, "buttons", ()))
    if getattr(read, "stage", None) != "settlement" or set(names) != {
        "reveal_start",
        "continue_game",
    }:
        return None
    return names, {"observedAt": facts.observed_at}, 2
