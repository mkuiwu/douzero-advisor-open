"""``recognition.v1`` 扁平 JSONL 合同的解析和结果构造。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from douzero_advisor.cards import RANK_TO_ENV

CONTRACT_VERSION = "recognition.v1"


class ContractError(ValueError):
    """一行命令不满足 recognition.v1 合同。"""


class MessageType(str, Enum):
    """Java 发给 CV Worker 的命令种类。"""

    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"


class TaskType(str, Enum):
    """Java 可调度的六种端到端语义识别能力。"""

    NEW_GAME = "NEW_GAME"
    PREPLAY_PROMPT = "PREPLAY_PROMPT"
    DEAL = "DEAL"
    LOCAL_TURN = "LOCAL_TURN"
    TURN_END = "TURN_END"
    SETTLEMENT = "SETTLEMENT"


class PromptEntryMode(str, Enum):
    """局前按钮任务接受当前画面还是先等待旧提示离开。"""

    ACCEPT_CURRENT_PROMPT = "accept_current_stable_prompt"
    REQUIRE_CHANGE_THEN_NEW = "require_change_then_new"


class TurnEntryMode(str, Enum):
    """本方回合任务接受当前入口还是要求先离开再重新进入。"""

    ACCEPT_CURRENT_STABLE_TURN = "accept_current_stable_turn"
    REQUIRE_EXIT_THEN_REENTER = "require_exit_then_reenter"


SEATS = frozenset({"landlord", "landlord_down", "landlord_up"})
PROMPT_TYPES = frozenset({"call", "rob", "double"})
PROMPT_ACTIONS = {
    "call": frozenset({"call", "no_call"}),
    "rob": frozenset({"rob", "no_rob"}),
    "double": frozenset({"super_double", "double", "no_double"}),
}


@dataclass(frozen=True, slots=True)
class RecognitionCommand:
    """一个已校验命令。

    ``deal_id``/``generation`` 是 Java 本局身份。``deadline_ms`` 对首次
    ``accept_current_stable_turn`` 从 Worker 收到 SUBMIT 时起计；对后续
    ``require_exit_then_reenter``，只在出牌按钮离开后重新出现时开始计时。其它
    任务从 Worker 收到 SUBMIT 时起计。局前和回合边沿字段只约束传感器，不代表
    Python 保存业务轮次。
    """

    message_type: MessageType
    task_type: TaskType
    request_id: str
    deadline_ms: int | None = None
    deal_id: str | None = None
    generation: int | None = None
    prompt_entry_mode: PromptEntryMode | None = None
    previous_prompt_type: str | None = None
    local_seat: str | None = None
    required_actors: tuple[str, ...] = ()
    turn_entry_mode: TurnEntryMode | None = None
    baseline_hand: tuple[str, ...] = ()
    baseline_actions_by_seat: tuple[tuple[str, tuple[str, ...]], ...] = ()


def parse_command(value: Mapping[str, Any]) -> RecognitionCommand:
    """解析一条命令；拒绝未知字段，防止合同悄悄漂移。"""

    if not isinstance(value, Mapping):
        raise ContractError("command must be an object")
    version = _text(value, "contractVersion")
    if version != CONTRACT_VERSION:
        raise ContractError(f"unsupported contractVersion: {version}")
    try:
        message_type = MessageType(_text(value, "messageType"))
        task_type = TaskType(_text(value, "taskType"))
    except ValueError as error:
        raise ContractError(str(error)) from error
    request_id = _text(value, "requestId")
    common = {"contractVersion", "messageType", "taskType", "requestId"}
    if message_type is MessageType.CANCEL:
        deal_id = None
        generation = None
        if task_type is not TaskType.NEW_GAME:
            deal_id = _text(value, "dealId")
            generation = _positive_int(value, "generation")
            common |= {"dealId", "generation"}
        _only_fields(value, common)
        return RecognitionCommand(
            message_type,
            task_type,
            request_id,
            deal_id=deal_id,
            generation=generation,
        )

    deadline_ms = _positive_int(value, "deadlineMs")
    allowed = common | {"deadlineMs"}
    deal_id: str | None = None
    generation: int | None = None
    if task_type is not TaskType.NEW_GAME:
        deal_id = _text(value, "dealId")
        generation = _positive_int(value, "generation")
        allowed |= {"dealId", "generation"}

    prompt_entry_mode = None
    previous_prompt_type = None
    local_seat = None
    required_actors: tuple[str, ...] = ()
    turn_entry_mode = None
    baseline_hand: tuple[str, ...] = ()
    baseline_actions_by_seat: tuple[tuple[str, tuple[str, ...]], ...] = ()
    if task_type is TaskType.PREPLAY_PROMPT:
        try:
            prompt_entry_mode = PromptEntryMode(_text(value, "entryMode"))
        except ValueError as error:
            raise ContractError(str(error)) from error
        allowed |= {"entryMode"}
    elif task_type is TaskType.LOCAL_TURN:
        local_seat = _enum_text(value, "localSeat", SEATS)
        required_actors = _enum_list(value, "requiredActors", SEATS)
        if local_seat in required_actors or len(required_actors) > 2:
            raise ContractError("requiredActors must contain at most two opponents")
        try:
            turn_entry_mode = TurnEntryMode(_text(value, "turnEntryMode"))
        except ValueError as error:
            raise ContractError(str(error)) from error
        allowed |= {"localSeat", "requiredActors", "turnEntryMode"}
    elif task_type is TaskType.TURN_END:
        local_seat = _enum_text(value, "localSeat", SEATS)
        baseline_hand = _card_array(value, "baselineHand")
        raw_actions = value.get("baselineActionsBySeat")
        if not isinstance(raw_actions, Mapping):
            raise ContractError("baselineActionsBySeat must be an object")
        if len(raw_actions) > 2 or any(
            seat not in SEATS or seat == local_seat for seat in raw_actions
        ):
            raise ContractError("baselineActionsBySeat must contain at most two opponents")
        decoded_actions: list[tuple[str, tuple[str, ...]]] = []
        for seat, cards in raw_actions.items():
            if not isinstance(cards, list):
                raise ContractError("baselineActionsBySeat values must be card arrays")
            card_values = tuple(cards)
            if card_values and not validate_cards(card_values):
                raise ContractError("baselineActionsBySeat contains invalid cards")
            decoded_actions.append((seat, card_values))
        baseline_actions_by_seat = tuple(sorted(decoded_actions))
        allowed |= {"localSeat", "baselineHand", "baselineActionsBySeat"}
    _only_fields(value, allowed)
    return RecognitionCommand(
        message_type=message_type,
        task_type=task_type,
        request_id=request_id,
        deadline_ms=deadline_ms,
        deal_id=deal_id,
        generation=generation,
        prompt_entry_mode=prompt_entry_mode,
        previous_prompt_type=previous_prompt_type,
        local_seat=local_seat,
        required_actors=required_actors,
        turn_entry_mode=turn_entry_mode,
        baseline_hand=baseline_hand,
        baseline_actions_by_seat=baseline_actions_by_seat,
    )


def ready_message() -> dict[str, Any]:
    """Worker 进程启动握手。"""

    return {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "READY",
        "taskTypes": [task_type.value for task_type in TaskType],
    }


def parse_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """严格校验 Worker 输出的扁平结果，供示例、回放和合同测试使用。"""

    if not isinstance(value, Mapping):
        raise ContractError("result must be an object")
    if _text(value, "contractVersion") != CONTRACT_VERSION:
        raise ContractError("unsupported contractVersion")
    if _text(value, "messageType") != "RESULT":
        raise ContractError("messageType must be RESULT")
    try:
        task_type = TaskType(_text(value, "taskType"))
    except ValueError as error:
        raise ContractError(str(error)) from error
    _text(value, "requestId")
    common = {
        "contractVersion",
        "messageType",
        "taskType",
        "requestId",
        "status",
    }
    if task_type is not TaskType.NEW_GAME:
        _text(value, "dealId")
        _positive_int(value, "generation")
        common |= {"dealId", "generation"}
    status = _text(value, "status")
    if status == "FAILED":
        expected = common | {"errorCode", "errorMessage"}
        _only_fields(value, expected)
        if set(value) != expected:
            raise ContractError("failed result fields are incomplete")
        _text(value, "errorCode")
        _text(value, "errorMessage")
        return dict(value)
    if status != "OK":
        raise ContractError(f"unsupported status: {status}")

    task_fields = {
        TaskType.NEW_GAME: {"observedAt"},
        TaskType.PREPLAY_PROMPT: {
            "promptType",
            "hand",
            "bottomCards",
            "availableActions",
            "observedAt",
        },
        TaskType.DEAL: {
            "localSeat",
            "hand",
            "bottomCards",
            "landlordOpeningPlay",
            "observedAt",
        },
        TaskType.LOCAL_TURN: {"currentHand", "actionsBySeat", "observedAt"},
        TaskType.TURN_END: {"observedAt"},
        TaskType.SETTLEMENT: {"observedAt"},
    }[task_type]
    expected = common | task_fields
    _only_fields(value, expected)
    if set(value) != expected:
        raise ContractError("successful result fields are incomplete")
    _observed_at(value)
    if task_type is TaskType.PREPLAY_PROMPT:
        prompt = _enum_text(value, "promptType", PROMPT_TYPES)
        hand = _card_array(value, "hand")
        if len(hand) not in {17, 20}:
            raise ContractError("preplay hand must contain 17 or 20 cards")
        bottom = _card_array(value, "bottomCards", allow_empty=True)
        if bottom and len(bottom) != 3:
            raise ContractError("preplay bottomCards must be empty or contain three cards")
        actions = _enum_list(value, "availableActions", PROMPT_ACTIONS[prompt])
        if not actions:
            raise ContractError("availableActions must not be empty")
    elif task_type is TaskType.DEAL:
        _enum_text(value, "localSeat", SEATS)
        hand = _card_array(value, "hand")
        if len(hand) not in {17, 20}:
            raise ContractError("deal hand must contain 17 or 20 cards")
        _card_array(value, "bottomCards", expected=3)
        _card_array(value, "landlordOpeningPlay", allow_empty=True)
    elif task_type is TaskType.LOCAL_TURN:
        _card_array(value, "currentHand")
        actions = value.get("actionsBySeat")
        if not isinstance(actions, Mapping) or not actions:
            raise ContractError("actionsBySeat must be a non-empty object")
        if any(seat not in SEATS for seat in actions):
            raise ContractError("actionsBySeat contains an unsupported seat")
        for cards in actions.values():
            if not isinstance(cards, list):
                raise ContractError("actionsBySeat values must be card arrays")
            if cards and not validate_cards(tuple(cards)):
                raise ContractError("actionsBySeat contains invalid cards")
    return dict(value)


def success_message(command: RecognitionCommand, **fields: Any) -> dict[str, Any]:
    """构造一个扁平成功结果。"""

    result: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "RESULT",
        "taskType": command.task_type.value,
        "requestId": command.request_id,
        "status": "OK",
    }
    if command.task_type is not TaskType.NEW_GAME:
        result["dealId"] = command.deal_id
        result["generation"] = command.generation
    result.update(fields)
    return result


def failure_message(
    command: RecognitionCommand,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    """构造一个扁平明确失败结果。"""

    result: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "RESULT",
        "taskType": command.task_type.value,
        "requestId": command.request_id,
        "status": "FAILED",
        "errorCode": error_code,
        "errorMessage": error_message,
    }
    if command.task_type is not TaskType.NEW_GAME:
        result["dealId"] = command.deal_id
        result["generation"] = command.generation
    return result


def _text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value


def _positive_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value <= 0:
        raise ContractError(f"{key} must be a positive integer")
    return value


def _non_negative_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value < 0:
        raise ContractError(f"{key} must be a non-negative integer")
    return value


def _enum_text(source: Mapping[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _text(source, key)
    if value not in allowed:
        raise ContractError(f"{key} contains an unsupported value: {value}")
    return value


def _optional_enum(
    source: Mapping[str, Any], key: str, allowed: frozenset[str]
) -> str | None:
    if key not in source or source[key] is None:
        return None
    return _enum_text(source, key, allowed)


def _enum_list(
    source: Mapping[str, Any], key: str, allowed: frozenset[str]
) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or item not in allowed for item in value
    ):
        raise ContractError(f"{key} must be an array of supported values")
    if len(set(value)) != len(value):
        raise ContractError(f"{key} must not contain duplicates")
    return tuple(value)


def _observed_at(source: Mapping[str, Any]) -> datetime:
    text = _text(source, "observedAt")
    try:
        value = datetime.fromisoformat(text)
    except ValueError as error:
        raise ContractError("observedAt must be an ISO-8601 timestamp") from error
    if value.tzinfo is None:
        raise ContractError("observedAt must include a timezone")
    return value


def _card_array(
    source: Mapping[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
    expected: int | None = None,
) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or any(not isinstance(card, str) for card in value):
        raise ContractError(f"{key} must be a semantic-card array")
    cards = tuple(value)
    if not cards and allow_empty:
        return cards
    if not validate_cards(cards, expected=expected):
        raise ContractError(f"{key} contains invalid semantic cards")
    return cards


def validate_cards(cards: tuple[str, ...], *, expected: int | None = None) -> bool:
    """检查 OCR 牌面和可选固定张数。"""

    capacities = {rank: 1 if rank in {"X", "D"} else 4 for rank in RANK_TO_ENV}
    return (
        (expected is None or len(cards) == expected)
        and bool(cards)
        and all(card in RANK_TO_ENV for card in cards)
        and all(count <= capacities[rank] for rank, count in Counter(cards).items())
    )


def _only_fields(source: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(source).difference(allowed))
    if unknown:
        raise ContractError(f"unknown fields: {', '.join(unknown)}")
