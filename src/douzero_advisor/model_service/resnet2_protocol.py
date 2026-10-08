"""把 inference.v1 的 ResNet2 线协议转换为现有模型 ``InfoSet``。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any

from douzero.env.game import InfoSet

from douzero_advisor.cards import ENV_TO_RANK, FULL_DECK
from douzero_advisor.engine.legal_actions import legal_actions_for
from douzero_advisor.state.models import Seat

CONTRACT_VERSION = "inference.v1"
MODEL_ID = "resnet2"
INITIAL_COUNTS = {
    Seat.LANDLORD: 20,
    Seat.LANDLORD_DOWN: 17,
    Seat.LANDLORD_UP: 17,
}
REQUEST_FIELDS = frozenset(
    {
        "contractVersion",
        "requestId",
        "dealId",
        "modelId",
        "deadlineMs",
        "position",
        "hand",
        "bottomCards",
        "actionHistory",
    }
)
RESPONSE_FIELDS = frozenset(
    {
        "contractVersion",
        "requestId",
        "dealId",
        "modelId",
        "status",
        "action",
        "actionValue",
        "actionMargin",
        "actionScores",
        "modelVersion",
        "latencyMs",
        "errorCode",
        "errorMessage",
    }
)
ERROR_CODES = frozenset(
    {
        "NONE",
        "DEAL_MISMATCH",
        "INVALID_SNAPSHOT",
        "MODEL_UNAVAILABLE",
        "UNSUPPORTED_MODEL",
        "MODEL_TIMEOUT",
        "ILLEGAL_ACTION",
        "UNSUPPORTED_CONTRACT",
        "NO_RECOMMENDATION",
    }
)


class InferenceProtocolError(ValueError):
    """带稳定错误码的 inference.v1 请求或响应校验失败。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# 保留旧名称兼容现有 ResNet2 worker 和测试调用方。
ResNet2ProtocolError = InferenceProtocolError


@dataclass(frozen=True, slots=True)
class ParsedInferenceRequest:
    """已验证且已构造模型公开状态的 inference.v1 请求。

    字段：
    - ``request_id``：单次请求身份，用于关联响应。
    - ``deal_id``：当前牌局身份，用于阻止跨局响应。
    - ``deadline_ms``：Java 允许的最大处理时间，单位为毫秒。
    - ``infoset``：现有 ResNet2 编码器需要的模型原生输入对象。
    - ``legal_actions``：Python 使用固定 DouZero 规则实现生成的合法动作。
    """

    request_id: str
    deal_id: str
    deadline_ms: int
    infoset: InfoSet
    legal_actions: tuple[tuple[int, ...], ...]


# 原 worker 已公开使用该名称，继续作为同一个共享解析结果的兼容别名。
ParsedResNet2Request = ParsedInferenceRequest


def parse_resnet2_request(envelope: Mapping[str, Any]) -> ParsedResNet2Request:
    """校验 inference.v1 请求并还原 ResNet2 的模型原生 ``InfoSet``。"""
    return validate_inference_request(envelope, model_id=MODEL_ID)


def validate_inference_request(
    envelope: Mapping[str, Any], *, model_id: str
) -> ParsedInferenceRequest:
    """按指定模型标识复用同一公开状态和合法动作校验。"""
    try:
        return _parse_inference_request(envelope, model_id=model_id)
    except InferenceProtocolError:
        raise
    except (TypeError, ValueError) as error:
        raise InferenceProtocolError("INVALID_SNAPSHOT", str(error)) from error


def _parse_inference_request(
    envelope: Mapping[str, Any], *, model_id: str
) -> ParsedInferenceRequest:
    request = _mapping(envelope, "request")
    _exact_fields(request, REQUEST_FIELDS, "request")
    if _text(request, "contractVersion") != CONTRACT_VERSION:
        raise InferenceProtocolError("UNSUPPORTED_CONTRACT", "不支持的推理协议版本")
    if _text(request, "modelId") != model_id:
        raise InferenceProtocolError("UNSUPPORTED_MODEL", f"请求模型不是 {model_id}")
    request_id = _text(request, "requestId")
    deal_id = _text(request, "dealId")
    deadline_ms = _positive_int(request, "deadlineMs")
    local_seat = _seat(request, "position")
    hand = _cards(request.get("hand"), "hand")
    bottom_cards = _cards(request.get("bottomCards"), "bottomCards")
    if len(bottom_cards) != 3:
        raise ValueError("模型请求必须包含三张已确认底牌")
    if not Counter(bottom_cards) <= Counter(FULL_DECK):
        raise ValueError("底牌超过完整牌堆容量")
    history = _history(request.get("actionHistory"))

    state = _derive_public_state(history)
    if state["current_seat"] is not local_seat:
        raise ValueError("动作历史推导的当前座位不是本方")
    remaining_cards = state["remaining_cards"]
    if len(hand) != remaining_cards[local_seat]:
        raise ValueError("本方手牌数量与动作历史不一致")

    known_cards = Counter(hand)
    for cards in state["played_cards"].values():
        known_cards.update(cards)
    if not known_cards <= Counter(FULL_DECK):
        raise ValueError("手牌和公开出牌超过完整牌堆容量")
    landlord_known_cards = Counter(hand)
    landlord_known_cards.update(state["played_cards"][Seat.LANDLORD])
    if local_seat is Seat.LANDLORD and not Counter(bottom_cards) <= landlord_known_cards:
        raise ValueError("本方为地主时底牌必须包含在手牌或已出牌证据中")

    legal_actions = legal_actions_for(hand, [cards for _, cards in history])
    if not legal_actions:
        raise ValueError("固定 DouZero 规则实现没有生成合法动作")

    played_cards = state["played_cards"]
    aggregate_other_cards = Counter(FULL_DECK)
    aggregate_other_cards.subtract(hand)
    for cards in played_cards.values():
        aggregate_other_cards.subtract(cards)
    if any(count < 0 for count in aggregate_other_cards.values()):
        raise ValueError("公开牌证据超过完整牌堆")

    infoset = InfoSet(local_seat.value)
    infoset.player_hand_cards = list(hand)
    infoset.num_cards_left_dict = {
        seat.value: remaining_cards[seat] for seat in Seat
    }
    infoset.three_landlord_cards = list(bottom_cards)
    infoset.card_play_action_seq = [
        [seat.value, list(cards)] for seat, cards in history
    ]
    infoset.other_hand_cards = list(aggregate_other_cards.elements())
    infoset.legal_actions = [list(action) for action in legal_actions]
    infoset.last_move = list(state["last_move"])
    infoset.last_two_moves = [list(cards) for _, cards in history[-2:]]
    infoset.last_move_dict = {
        seat.value: list(state["last_move_by_seat"][seat]) for seat in Seat
    }
    infoset.played_cards = {
        seat.value: list(played_cards[seat]) for seat in Seat
    }
    infoset.all_handcards = {seat.value: [] for seat in Seat}
    infoset.last_pid = (
        state["last_pid"].value if state["last_pid"] is not None else None
    )
    infoset.bomb_num = state["bomb_num"]
    return ParsedInferenceRequest(
        request_id=request_id,
        deal_id=deal_id,
        deadline_ms=deadline_ms,
        infoset=infoset,
        legal_actions=tuple(tuple(action) for action in legal_actions),
    )


def build_success_response(
    request: ParsedResNet2Request,
    action: list[int],
    action_values: Sequence[float],
    *,
    model_version: str,
    latency_ms: int,
) -> dict[str, Any]:
    """把模型动作写回 inference.v1；空列表是线协议中的合法不出动作。"""
    try:
        normalized = tuple(_cards(action, "action"))
    except (TypeError, ValueError) as error:
        raise ResNet2ProtocolError("ILLEGAL_ACTION", str(error)) from error
    if normalized not in request.legal_actions:
        raise ResNet2ProtocolError(
            "ILLEGAL_ACTION", "模型动作不在 Python 生成的合法动作集合内"
        )
    values = _action_values(action_values, len(request.legal_actions))
    selected_index = request.legal_actions.index(normalized)
    action_value = values[selected_index]
    runner_up = max(
        (value for index, value in enumerate(values) if index != selected_index),
        default=action_value,
    )
    action_margin = action_value - runner_up
    if action_margin < 0:
        raise ResNet2ProtocolError(
            "ILLEGAL_ACTION", "模型推荐动作不是候选动作中的最高价值动作"
        )
    action_scores = [
        {"action": list(candidate), "value": value}
        for candidate, value in zip(request.legal_actions, values, strict=True)
    ]
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("模型版本不能为空")
    if (
        not isinstance(latency_ms, int)
        or isinstance(latency_ms, bool)
        or latency_ms < 0
    ):
        raise ValueError("模型耗时不能为负数")
    return {
        "contractVersion": CONTRACT_VERSION,
        "requestId": request.request_id,
        "dealId": request.deal_id,
        "modelId": MODEL_ID,
        "status": "OK",
        "action": list(normalized),
        "actionValue": action_value,
        "actionMargin": action_margin,
        "actionScores": action_scores,
        "modelVersion": model_version,
        "latencyMs": latency_ms,
        "errorCode": "NONE",
        "errorMessage": "",
    }


def build_protocol_error_response(
    *,
    request_id: str,
    deal_id: str,
    error: ResNet2ProtocolError,
    latency_ms: int,
    model_version: str | None = None,
) -> dict[str, Any]:
    """把协议校验失败转换为 Java 可稳定处理的结构化 ERROR 响应。"""
    if (
        not isinstance(request_id, str)
        or not request_id.strip()
        or not isinstance(deal_id, str)
        or not deal_id.strip()
    ):
        raise ValueError("错误响应必须保留请求标识和牌局标识")
    if (
        not isinstance(latency_ms, int)
        or isinstance(latency_ms, bool)
        or latency_ms < 0
    ):
        raise ValueError("模型耗时不能为负数")
    if model_version is not None and (
        not isinstance(model_version, str) or not model_version.strip()
    ):
        raise ValueError("已提供的模型版本不能为空字符串")
    return {
        "contractVersion": CONTRACT_VERSION,
        "requestId": request_id,
        "dealId": deal_id,
        "modelId": MODEL_ID,
        "status": "ERROR",
        "action": None,
        "actionValue": None,
        "actionMargin": None,
        "actionScores": None,
        "modelVersion": model_version,
        "latencyMs": latency_ms,
        "errorCode": error.error_code,
        "errorMessage": str(error),
    }


def parse_resnet2_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """严格校验 inference.v1 ResNet2 响应，供示例和回放双侧解析。"""

    return validate_inference_response(value, model_id=MODEL_ID)


def validate_inference_response(
    value: Mapping[str, Any], *, model_id: str
) -> dict[str, Any]:
    """按指定模型标识严格校验共享的 inference.v1 Response 语义。"""

    response = _mapping(value, "response")
    _exact_fields(response, RESPONSE_FIELDS, "response")
    if _text(response, "contractVersion") != CONTRACT_VERSION:
        raise InferenceProtocolError("UNSUPPORTED_CONTRACT", "不支持的推理协议版本")
    if _text(response, "modelId") != model_id:
        raise InferenceProtocolError("UNSUPPORTED_MODEL", f"响应模型不是 {model_id}")
    _text(response, "requestId")
    _text(response, "dealId")
    status = _text(response, "status")
    if status not in {"OK", "WAIT", "ERROR"}:
        raise ValueError("status 必须是 OK、WAIT 或 ERROR")
    latency_ms = response.get("latencyMs")
    if (
        not isinstance(latency_ms, int)
        or isinstance(latency_ms, bool)
        or latency_ms < 0
    ):
        raise ValueError("latencyMs 必须是非负整数")
    error_code = _text(response, "errorCode")
    if error_code not in ERROR_CODES:
        raise ValueError("errorCode 不是 inference.v1 已定义错误码")

    if status == "OK":
        action = tuple(_cards(response.get("action"), "action"))
        action_value = _finite_number(response, "actionValue")
        action_margin = _finite_number(response, "actionMargin")
        if action_margin < 0:
            raise ValueError("actionMargin 必须是非负数")
        scores = _response_scores(response.get("actionScores"))
        if not scores:
            raise ValueError("OK 响应的 actionScores 不能为空")
        if error_code != "NONE" or response.get("errorMessage") != "":
            raise ValueError("OK 响应不能携带失败信息")
        _text(response, "modelVersion")
        selected_values = [score for candidate, score in scores if candidate == action]
        if action_value not in selected_values:
            raise ValueError("推荐动作与 actionScores 不一致")
        best_value = max(score for _, score in scores)
        if not isclose(action_value, best_value, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("推荐动作不是 actionScores 中的最高价值动作")
        runner_up = max(
            (score for candidate, score in scores if candidate != action),
            default=action_value,
        )
        if not isclose(
            action_margin,
            action_value - runner_up,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("actionMargin 与 actionScores 不一致")
    else:
        if any(
            response.get(field) is not None
            for field in ("action", "actionValue", "actionMargin", "actionScores")
        ):
            raise ValueError("WAIT 或 ERROR 响应不能携带动作评分")
        if error_code == "NONE":
            raise ValueError("WAIT 或 ERROR 响应必须携带失败错误码")
        _text(response, "errorMessage")
        model_version = response.get("modelVersion")
        if model_version is not None and (
            not isinstance(model_version, str) or not model_version.strip()
        ):
            raise ValueError("modelVersion 必须为 null 或非空字符串")
    return dict(response)


def _derive_public_state(
    history: list[tuple[Seat, list[int]]],
) -> dict[str, Any]:
    current_seat = Seat.LANDLORD
    last_move: list[int] = []
    last_pid: Seat | None = None
    previous_pass = False
    remaining_cards = dict(INITIAL_COUNTS)
    played_cards = {seat: [] for seat in Seat}
    last_move_by_seat = {seat: [] for seat in Seat}
    bomb_num = 0
    for seat, cards in history:
        is_pass = not cards
        if seat is not current_seat:
            raise ValueError("动作历史座位顺序不合法")
        if is_pass:
            if not last_move:
                raise ValueError("领出玩家不能不出")
            last_move_by_seat[seat] = []
            current_seat = last_pid if previous_pass else seat.next
            if previous_pass:
                last_move = []
            previous_pass = True
            continue
        remaining_cards[seat] -= len(cards)
        if remaining_cards[seat] < 0:
            raise ValueError("动作历史导致玩家剩余牌数为负数")
        played_cards[seat].extend(cards)
        last_move_by_seat[seat] = list(cards)
        last_move = list(cards)
        last_pid = seat
        current_seat = seat.next
        previous_pass = False
        bomb_num += int(_is_bomb(cards))
    return {
        "current_seat": current_seat,
        "last_move": last_move,
        "last_pid": last_pid,
        "remaining_cards": remaining_cards,
        "played_cards": played_cards,
        "last_move_by_seat": last_move_by_seat,
        "bomb_num": bomb_num,
    }


def _history(value: Any) -> list[tuple[Seat, list[int]]]:
    if not isinstance(value, list):
        raise TypeError("actionHistory 必须是数组")
    play_order = (Seat.LANDLORD, Seat.LANDLORD_DOWN, Seat.LANDLORD_UP)
    result: list[tuple[Seat, list[int]]] = []
    for index, raw in enumerate(value):
        cards = _cards(raw, f"actionHistory[{index}]")
        result.append((play_order[index % len(play_order)], cards))
    return result


def _action_values(value: Sequence[float], expected_count: int) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError("模型动作价值必须是数组")
    values: list[float] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            raise TypeError(f"模型动作价值[{index}]必须是数值")
        normalized = float(raw)
        if not isfinite(normalized):
            raise ValueError(f"模型动作价值[{index}]必须是有限数值")
        values.append(normalized)
    if len(values) != expected_count:
        raise ValueError("模型动作价值数量与候选动作数量不一致")
    return tuple(values)


def _response_scores(value: Any) -> tuple[tuple[tuple[int, ...], float], ...]:
    if not isinstance(value, list):
        raise TypeError("actionScores 必须是数组")
    scores: list[tuple[tuple[int, ...], float]] = []
    for index, raw in enumerate(value):
        score = _mapping(raw, f"actionScores[{index}]")
        _exact_fields(score, frozenset({"action", "value"}), f"actionScores[{index}]")
        action = tuple(_cards(score.get("action"), f"actionScores[{index}].action"))
        scores.append((action, _finite_number(score, "value")))
    return tuple(scores)


def _cards(value: Any, field: str) -> list[int]:
    if not isinstance(value, list):
        raise TypeError(f"{field} 必须是数组")
    cards: list[int] = []
    for card in value:
        if not isinstance(card, int) or isinstance(card, bool) or card not in ENV_TO_RANK:
            raise ValueError(f"{field} 包含未知牌面编码: {card!r}")
        cards.append(card)
    return cards


def _seat(source: Mapping[str, Any], field: str) -> Seat:
    try:
        return Seat(_text(source, field))
    except ValueError as error:
        raise ValueError(f"{field} 包含未知座位") from error


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} 必须是对象")
    return value


def _exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], field: str
) -> None:
    missing = sorted(expected.difference(value))
    unknown = sorted(set(value).difference(expected))
    if missing or unknown:
        raise ValueError(
            f"{field} 字段不匹配: missing={missing}, unknown={unknown}"
        )


def _text(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value


def _positive_int(source: Mapping[str, Any], field: str) -> int:
    value = source.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return value


def _finite_number(source: Mapping[str, Any], field: str) -> float:
    value = source.get(field)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{field} 必须是数值")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{field} 必须是有限数值")
    return normalized


def _is_bomb(cards: list[int]) -> bool:
    return (len(cards) == 4 and len(set(cards)) == 1) or sorted(cards) == [20, 30]
