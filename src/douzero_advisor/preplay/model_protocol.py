"""局前模型 ``preplay-inference.v1`` 的显式 Request/Response 适配。"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from douzero_advisor.cards import RANK_TO_ENV

CONTRACT_VERSION = "preplay-inference.v1"
STAGE_ACTIONS = {
    "call": frozenset({"call", "no_call"}),
    "rob": frozenset({"rob", "no_rob"}),
    "double": frozenset({"super_double", "double", "no_double"}),
}


class PreplayProtocolError(ValueError):
    """请求不满足 preplay-inference.v1。"""


class ScoreModel(Protocol):
    """局前模型三条 legacy 评分能力，不含 CV 或按钮状态。"""

    def score_bid(
        self, cards: Sequence[str], *, timeout_seconds: float | None = None
    ) -> float: ...

    def score_farmer(
        self, cards: Sequence[str], *, timeout_seconds: float | None = None
    ) -> float: ...

    def score_landlord(
        self,
        cards: Sequence[str],
        bottom_cards: Sequence[str],
        *,
        timeout_seconds: float | None = None,
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class PreplayModelRequest:
    """Java 已确认的一次局前建议请求。

    身份三元组用于拒绝迟到结果；手牌和底牌是稳定语义牌面；按钮阶段、可用
    动作及已见叫/抢上下文全部由 Java 提供，模型 Worker 不自行推断轮次。
    """

    request_id: str
    deal_id: str
    generation: int
    model_id: str
    deadline_ms: int
    stage: str
    hand: tuple[str, ...]
    bottom_cards: tuple[str, ...]
    available_actions: tuple[str, ...]
    call_prompt_seen: bool
    rob_prompt_seen: bool


@dataclass(frozen=True, slots=True)
class PreplayThresholds:
    """局前决策分数门槛；分数不是概率或胜率。

    当前默认值已在原始 FullAuto 校准值基础上下调 25%（每项乘 0.75），
    用于试验更激进的叫/抢/加倍倾向。如需恢复原始校准值，将各项分别
    还原为 0.2 / 0.4 / 0.5 / 0.5 / 0.4 / 0.6 / 0.5 / 1.2 / 1.0。
    """

    call: float = 0.15
    rob_after_call_stage: float = 0.3
    rob_direct: float = 0.375
    landlord_super: float = 0.375
    landlord_double: float = 0.3
    landlord_robbed_super: float = 0.45
    landlord_robbed_double: float = 0.375
    farmer_super: float = 0.9
    farmer_double: float = 0.75


def parse_request(value: Mapping[str, Any]) -> PreplayModelRequest:
    """严格解析一个扁平模型请求。"""

    if not isinstance(value, Mapping):
        raise PreplayProtocolError("request must be an object")
    expected_fields = {
        "contractVersion",
        "requestId",
        "dealId",
        "generation",
        "modelId",
        "deadlineMs",
        "stage",
        "hand",
        "bottomCards",
        "availableActions",
        "callPromptSeen",
        "robPromptSeen",
    }
    unknown = sorted(set(value).difference(expected_fields))
    missing = sorted(expected_fields.difference(value))
    if unknown or missing:
        raise PreplayProtocolError(
            f"request fields mismatch; missing={missing}, unknown={unknown}"
        )
    if _text(value, "contractVersion") != CONTRACT_VERSION:
        raise PreplayProtocolError("unsupported contractVersion")
    stage = _text(value, "stage")
    if stage not in STAGE_ACTIONS:
        raise PreplayProtocolError(f"unsupported stage: {stage}")
    hand = _cards(value, "hand")
    bottom = _cards(value, "bottomCards", allow_empty=True)
    available = _string_array(value, "availableActions")
    if not available or not set(available).issubset(STAGE_ACTIONS[stage]):
        raise PreplayProtocolError("availableActions do not match stage")
    expected_hand_size = 17 if stage in {"call", "rob"} else None
    if expected_hand_size is not None and len(hand) != expected_hand_size:
        raise PreplayProtocolError(f"{stage} requires 17 hand cards")
    if stage == "double" and len(hand) not in {17, 20}:
        raise PreplayProtocolError("double requires 17 or 20 hand cards")
    if len(hand) == 20 and len(bottom) != 3:
        raise PreplayProtocolError("landlord double requires three bottom cards")
    if len(hand) != 20 and bottom:
        raise PreplayProtocolError("bottomCards must be empty when the model does not need them")
    return PreplayModelRequest(
        request_id=_text(value, "requestId"),
        deal_id=_text(value, "dealId"),
        generation=_non_negative_int(value, "generation"),
        model_id=_text(value, "modelId"),
        deadline_ms=_positive_int(value, "deadlineMs"),
        stage=stage,
        hand=hand,
        bottom_cards=bottom,
        available_actions=available,
        call_prompt_seen=_bool(value, "callPromptSeen"),
        rob_prompt_seen=_bool(value, "robPromptSeen"),
    )


def evaluate_request(
    request: PreplayModelRequest,
    model: ScoreModel,
    *,
    thresholds: PreplayThresholds | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """调用 legacy 分数路径并按冻结阈值映射成可用动作。"""

    limits = thresholds or PreplayThresholds()
    if request.stage == "call":
        score = _score(model.score_bid(request.hand, timeout_seconds=timeout_seconds))
        threshold = limits.call
        action = "call" if score > threshold else "no_call"
        reason = "bid_score_vs_call_threshold"
    elif request.stage == "rob":
        score = _score(model.score_bid(request.hand, timeout_seconds=timeout_seconds))
        threshold = limits.rob_after_call_stage if request.call_prompt_seen else limits.rob_direct
        action = "rob" if score > threshold else "no_rob"
        reason = (
            "bid_score_vs_rob_after_call_threshold"
            if request.call_prompt_seen
            else "bid_score_vs_direct_rob_threshold"
        )
    elif len(request.hand) == 20:
        score = _score(
            model.score_landlord(
                request.hand,
                request.bottom_cards,
                timeout_seconds=timeout_seconds,
            )
        )
        high, low = (
            (limits.landlord_robbed_super, limits.landlord_robbed_double)
            if request.rob_prompt_seen
            else (limits.landlord_super, limits.landlord_double)
        )
        action, threshold, reason = _double_action(request.hand, score, high, low)
        action, threshold, reason = _adapt_double_action(
            action,
            threshold,
            reason,
            request.available_actions,
            double_threshold=low,
        )
    else:
        score = _score(model.score_farmer(request.hand, timeout_seconds=timeout_seconds))
        action, threshold, reason = _double_action(
            request.hand,
            score,
            limits.farmer_super,
            limits.farmer_double,
        )
        action, threshold, reason = _adapt_double_action(
            action,
            threshold,
            reason,
            request.available_actions,
            double_threshold=limits.farmer_double,
        )
    if action not in request.available_actions:
        raise PreplayProtocolError(
            f"recommended action {action} is absent from availableActions"
        )
    return {
        "contractVersion": CONTRACT_VERSION,
        "requestId": request.request_id,
        "dealId": request.deal_id,
        "generation": request.generation,
        "modelId": request.model_id,
        "modelVersion": "fullauto-6d20491da216c989d23cf2883498758fb78e9f8d",
        "latencyMs": 0,
        "status": "OK",
        "action": action,
        "score": score,
        "threshold": threshold,
        "decisionReason": reason,
    }


def failure_response(
    raw: Mapping[str, Any] | None,
    *,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    """尽可能回显身份的显式失败结果。"""

    raw = raw or {}
    return {
        "contractVersion": CONTRACT_VERSION,
        "requestId": str(raw.get("requestId", "UNKNOWN")),
        "dealId": str(raw.get("dealId", "UNKNOWN")),
        "generation": raw.get("generation", -1),
        "modelId": str(raw.get("modelId", "UNKNOWN")),
        "modelVersion": None,
        "latencyMs": 0,
        "status": "FAILED",
        "errorCode": error_code,
        "errorMessage": error_message,
    }


def parse_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """严格校验一个扁平局前模型响应，供双侧示例和回放使用。"""

    if not isinstance(value, Mapping):
        raise PreplayProtocolError("response must be an object")
    common = {
        "contractVersion",
        "requestId",
        "dealId",
        "generation",
        "modelId",
        "modelVersion",
        "latencyMs",
        "status",
    }
    status = _text(value, "status")
    if status == "OK":
        expected = common | {"action", "score", "threshold", "decisionReason"}
    elif status == "FAILED":
        expected = common | {"errorCode", "errorMessage"}
    else:
        raise PreplayProtocolError(f"unsupported status: {status}")
    unknown = sorted(set(value).difference(expected))
    missing = sorted(expected.difference(value))
    if unknown or missing:
        raise PreplayProtocolError(
            f"response fields mismatch; missing={missing}, unknown={unknown}"
        )
    if _text(value, "contractVersion") != CONTRACT_VERSION:
        raise PreplayProtocolError("unsupported contractVersion")
    _text(value, "requestId")
    _text(value, "dealId")
    _non_negative_int(value, "generation")
    _text(value, "modelId")
    _non_negative_int(value, "latencyMs")
    if status == "OK":
        _text(value, "modelVersion")
        action = _text(value, "action")
        if action not in set().union(*STAGE_ACTIONS.values()):
            raise PreplayProtocolError(f"unsupported action: {action}")
        _finite_number(value, "score")
        _finite_number(value, "threshold")
        _text(value, "decisionReason")
    else:
        if value.get("modelVersion") is not None:
            raise PreplayProtocolError("failed response modelVersion must be null")
        _text(value, "errorCode")
        _text(value, "errorMessage")
    return dict(value)


def _double_action(
    hand: Sequence[str], score: float, high: float, low: float
) -> tuple[str, float, str]:
    if score > high and _contains_super_double_pattern(hand):
        return "super_double", high, "score_and_hardcoded_strong_hand"
    if score > low:
        return "double", low, "score_vs_double_threshold"
    return "no_double", low, "score_below_double_threshold"


def _adapt_double_action(
    action: str,
    threshold: float,
    reason: str,
    available_actions: Sequence[str],
    *,
    double_threshold: float,
) -> tuple[str, float, str]:
    """把没有超级加倍按钮的强牌建议安全降级为普通加倍。"""

    if (
        action == "super_double"
        and "super_double" not in available_actions
        and "double" in available_actions
    ):
        return "double", double_threshold, "super_double_unavailable_fallback_to_double"
    return action, threshold, reason


def _contains_super_double_pattern(cards: Sequence[str]) -> bool:
    encoded = "".join(cards).replace("10", "T")
    return any(pattern in encoded for pattern in ("DX", "D2", "X22", "222A", "22AAA"))


def _score(value: object) -> float:
    score = float(value)
    if not math.isfinite(score):
        raise PreplayProtocolError("model score must be finite")
    return score


def _text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise PreplayProtocolError(f"{key} must be a non-empty string")
    return value


def _positive_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value <= 0:
        raise PreplayProtocolError(f"{key} must be a positive integer")
    return value


def _non_negative_int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value < 0:
        raise PreplayProtocolError(f"{key} must be a non-negative integer")
    return value


def _finite_number(source: Mapping[str, Any], key: str) -> float:
    value = source.get(key)
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise PreplayProtocolError(f"{key} must be a finite number")
    return float(value)


def _bool(source: Mapping[str, Any], key: str) -> bool:
    value = source.get(key)
    if type(value) is not bool:
        raise PreplayProtocolError(f"{key} must be a bool")
    return value


def _string_array(source: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PreplayProtocolError(f"{key} must be a string array")
    if len(set(value)) != len(value):
        raise PreplayProtocolError(f"{key} must not contain duplicates")
    return tuple(value)


def _cards(
    source: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PreplayProtocolError(f"{key} must be a semantic-card array")
    cards = tuple(value)
    capacities = {rank: 1 if rank in {"X", "D"} else 4 for rank in RANK_TO_ENV}
    if (
        (not cards and not allow_empty)
        or any(card not in RANK_TO_ENV for card in cards)
        or any(count > capacities[rank] for rank, count in Counter(cards).items())
    ):
        raise PreplayProtocolError(f"{key} contains invalid semantic cards")
    return cards
