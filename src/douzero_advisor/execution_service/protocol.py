"""``execution.v1`` 扁平 JSONL 合同的解析和结果构造。

合同同时支持 CARD_PLAY 和 PREPLAY_BUTTON。局前任务只接收明确的正向动作；
不叫、不抢和不加倍不应形成执行命令。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from douzero_advisor.cards import RANK_TO_ENV

CONTRACT_VERSION = "execution.v1"


class ContractError(ValueError):
    """一行命令不满足 execution.v1 合同。"""


class MessageType(str, Enum):
    """Java 发给执行 Worker 的命令种类。"""

    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"


class TaskType(str, Enum):
    """Java 可调度的执行能力。"""

    CARD_PLAY = "CARD_PLAY"
    PREPLAY_BUTTON = "PREPLAY_BUTTON"


class ActionType(str, Enum):
    """出牌动作类型。"""

    PLAY = "PLAY"
    PASS = "PASS"


SEATS = frozenset({"landlord", "landlord_down", "landlord_up"})
PREPLAY_STAGES = frozenset({"call", "rob", "double"})
PREPLAY_POSITIVE_ACTIONS = frozenset({"call", "rob", "double", "super_double"})


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    """一个已校验的执行命令。

    ``deal_id``/``generation`` 是 Java 本局身份；Python 在点击前必须校验
    当前授权代际仍匹配。``deadline_ms`` 从 Worker 收到 SUBMIT 时起计。
    ``auto_submit`` 控制出牌选牌验证通过后是否真正点击提交按钮。
    PREPLAY_BUTTON 命令使用 ``preplay_stage`` 和 ``preplay_action`` 字段。
    """

    message_type: MessageType
    task_type: TaskType
    request_id: str
    deal_id: str
    generation: int
    deadline_ms: int
    local_seat: str = ""
    authoritative_hand: tuple[str, ...] = ()
    recommended_cards: tuple[str, ...] = ()
    action_type: ActionType | None = None
    auto_submit: bool = False
    click_delay_ms: int = 0
    preplay_stage: str | None = None
    preplay_action: str | None = None


def parse_command(value: Mapping[str, Any]) -> ExecutionCommand:
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
    deal_id = _text(value, "dealId")
    generation = _positive_int(value, "generation")
    common = {
        "contractVersion",
        "messageType",
        "taskType",
        "requestId",
        "dealId",
        "generation",
    }
    if message_type is MessageType.CANCEL:
        _only_fields(value, common)
        return ExecutionCommand(
            message_type=message_type,
            task_type=task_type,
            request_id=request_id,
            deal_id=deal_id,
            generation=generation,
            deadline_ms=0,
            local_seat="",
            authoritative_hand=(),
            recommended_cards=(),
            action_type=ActionType.PASS,
            auto_submit=False,
        )

    deadline_ms = _positive_int(value, "deadlineMs")
    if task_type is TaskType.PREPLAY_BUTTON:
        stage = _enum_text(value, "stage", PREPLAY_STAGES)
        action = _text(value, "action").lower()
        if action not in PREPLAY_POSITIVE_ACTIONS:
            raise ContractError("局前不叫、不抢和不加倍动作禁止自动点击")
        if ((stage == "call" and action != "call")
                or (stage == "rob" and action != "rob")
                or (stage == "double" and action not in {"double", "super_double"})):
            raise ContractError("局前动作与阶段不匹配")
        _only_fields(value, common | {"deadlineMs", "stage", "action"})
        return ExecutionCommand(
            message_type=message_type,
            task_type=task_type,
            request_id=request_id,
            deal_id=deal_id,
            generation=generation,
            deadline_ms=deadline_ms,
            preplay_stage=stage,
            preplay_action=action,
        )

    allowed = common | {"deadlineMs"}

    local_seat = _enum_text(value, "localSeat", SEATS)
    authoritative_hand = _card_array(value, "authoritativeHand")
    try:
        action_type = ActionType(_text(value, "actionType"))
    except ValueError as error:
        raise ContractError(str(error)) from error
    recommended_cards = ()
    if action_type is ActionType.PLAY:
        recommended_cards = _card_array(value, "recommendedCards")
        if not _is_subset(recommended_cards, authoritative_hand):
            raise ContractError("recommendedCards must be a subset of authoritativeHand")
    auto_submit = _bool(value, "autoSubmit")
    click_delay_ms = _nonnegative_int(value, "clickDelayMs") if "clickDelayMs" in value else 0
    allowed = common | {
        "deadlineMs",
        "localSeat",
        "authoritativeHand",
        "actionType",
        "autoSubmit",
    }
    if action_type is ActionType.PLAY:
        allowed.add("recommendedCards")
    elif click_delay_ms > 0 and not auto_submit:
        raise ContractError("PASS 延迟点击必须同时启用 autoSubmit")
    if action_type is ActionType.PASS and "clickDelayMs" in value:
        allowed.add("clickDelayMs")
    elif "clickDelayMs" in value:
        raise ContractError("clickDelayMs 只允许用于 PASS")
    _only_fields(value, allowed)
    return ExecutionCommand(
        message_type=message_type,
        task_type=task_type,
        request_id=request_id,
        deal_id=deal_id,
        generation=generation,
        deadline_ms=deadline_ms,
        local_seat=local_seat,
        authoritative_hand=authoritative_hand,
        recommended_cards=recommended_cards,
        action_type=action_type,
        auto_submit=auto_submit,
        click_delay_ms=click_delay_ms,
    )


def ready_message() -> dict[str, Any]:
    """Worker 进程启动握手。"""

    return {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "READY",
        "taskTypes": [task_type.value for task_type in TaskType],
    }


def success_message(command: ExecutionCommand, **fields: Any) -> dict[str, Any]:
    """构造一个扁平成功结果。"""

    result: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "RESULT",
        "taskType": command.task_type.value,
        "requestId": command.request_id,
        "dealId": command.deal_id,
        "generation": command.generation,
        "status": "OK",
    }
    if command.task_type is TaskType.CARD_PLAY:
        result["autoSubmitEcho"] = command.auto_submit
    result.update(fields)
    return result


def uncertain_message(command: ExecutionCommand, detail: str) -> dict[str, Any]:
    """构造一个不确定结果：可能已点击但无法确认，Java 必须重新识别。"""

    return {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "RESULT",
        "taskType": command.task_type.value,
        "requestId": command.request_id,
        "dealId": command.deal_id,
        "generation": command.generation,
        "status": "UNCERTAIN",
        "detail": detail,
    }


def failure_message(
    command: ExecutionCommand,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    """构造一个扁平明确失败结果。"""

    return {
        "contractVersion": CONTRACT_VERSION,
        "messageType": "RESULT",
        "taskType": command.task_type.value,
        "requestId": command.request_id,
        "dealId": command.deal_id,
        "generation": command.generation,
        "status": "FAILED",
        "errorCode": error_code,
        "errorMessage": error_message,
    }


def utc_now_iso() -> str:
    """返回当前 UTC ISO-8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


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


def _nonnegative_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value < 0:
        raise ContractError(f"{key} must be a non-negative integer")
    return value


def _bool(source: Mapping[str, Any], key: str) -> bool:
    value = source.get(key)
    if not isinstance(value, bool):
        raise ContractError(f"{key} must be a boolean")
    return value


def _enum_text(source: Mapping[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _text(source, key)
    if value not in allowed:
        raise ContractError(f"{key} contains an unsupported value: {value}")
    return value


def _card_array(source: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or any(not isinstance(card, str) for card in value):
        raise ContractError(f"{key} must be a semantic-card array")
    cards = tuple(value)
    if not _validate_cards(cards):
        raise ContractError(f"{key} contains invalid semantic cards")
    return cards


def _validate_cards(cards: tuple[str, ...]) -> bool:
    capacities = {rank: 1 if rank in {"X", "D"} else 4 for rank in RANK_TO_ENV}
    return (
        bool(cards)
        and all(card in RANK_TO_ENV for card in cards)
        and all(count <= capacities[rank] for rank, count in Counter(cards).items())
    )


def _is_subset(subset: tuple[str, ...], superset: tuple[str, ...]) -> bool:
    return Counter(subset) <= Counter(superset)


def _only_fields(source: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(source).difference(allowed))
    if unknown:
        raise ContractError(f"unknown fields: {', '.join(unknown)}")
