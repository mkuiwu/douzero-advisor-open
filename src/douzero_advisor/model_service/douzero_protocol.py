"""严格解析 inference.v1 默认 DouZero 线协议。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from douzero_advisor.model_service.resnet2_protocol import (
    validate_inference_request,
    validate_inference_response,
)

MODEL_ID = "original"


def parse_douzero_request(value: Mapping[str, Any]) -> dict[str, Any]:
    """校验默认 DouZero Request，并保留合同原始字段和值。"""

    validate_inference_request(value, model_id=MODEL_ID)
    return dict(value)


def parse_douzero_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """校验默认 DouZero Response；空动作是 pass，非 OK 动作必须为 null。"""

    validate_inference_response(value, model_id=MODEL_ID)
    return dict(value)
