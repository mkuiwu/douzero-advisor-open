from __future__ import annotations

import json
from pathlib import Path

import pytest

from douzero_advisor.model_service.douzero_protocol import (
    parse_douzero_request,
    parse_douzero_response,
)
from douzero_advisor.model_service.resnet2_protocol import (
    ResNet2ProtocolError,
    build_protocol_error_response,
    build_success_response,
    parse_resnet2_request,
    parse_resnet2_response,
)

ROOT = Path(__file__).parents[1]
REQUEST_EXAMPLE = (
    ROOT / "contracts" / "inference" / "v1" / "resnet2.request.example.json"
)
RESPONSE_EXAMPLE = (
    ROOT / "contracts" / "inference" / "v1" / "resnet2.response.example.json"
)
DOUZERO_REQUEST_EXAMPLE = (
    ROOT / "contracts" / "inference" / "v1" / "douzero.request.example.json"
)
DOUZERO_RESPONSE_EXAMPLE = (
    ROOT / "contracts" / "inference" / "v1" / "douzero.response.example.json"
)


def test_request_example_builds_complete_resnet2_infoset() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))

    assert set(request) == {
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
    parsed = parse_resnet2_request(request)

    assert parsed.legal_actions == ((20, 30), ())
    assert parsed.infoset.player_position == "landlord_down"
    assert parsed.infoset.num_cards_left_dict == {
        "landlord": 18,
        "landlord_down": 17,
        "landlord_up": 17,
    }
    assert parsed.infoset.card_play_action_seq == [["landlord", [17, 17]]]
    assert parsed.infoset.legal_actions == [[20, 30], []]


def test_response_example_is_a_complete_strict_resnet2_response() -> None:
    response = json.loads(RESPONSE_EXAMPLE.read_text(encoding="utf-8"))

    assert parse_resnet2_response(response) == response


def test_original_douzero_examples_are_directly_and_semantically_parsed() -> None:
    request = json.loads(DOUZERO_REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    response = json.loads(DOUZERO_RESPONSE_EXAMPLE.read_text(encoding="utf-8"))

    assert parse_douzero_request(request) == request
    assert parse_douzero_response(response) == response


def test_original_douzero_response_distinguishes_pass_from_failure() -> None:
    response = json.loads(DOUZERO_RESPONSE_EXAMPLE.read_text(encoding="utf-8"))
    pass_response = dict(response)
    pass_response.update(
        action=[],
        actionValue=0.82,
        actionMargin=0.72,
        actionScores=[
            {"action": [20, 30], "value": 0.1},
            {"action": [], "value": 0.82},
        ],
    )
    failure_response = dict(response)
    failure_response.update(
        status="ERROR",
        action=None,
        actionValue=None,
        actionMargin=None,
        actionScores=None,
        modelVersion=None,
        errorCode="MODEL_TIMEOUT",
        errorMessage="model deadline elapsed",
    )

    assert parse_douzero_response(pass_response)["action"] == []
    assert parse_douzero_response(failure_response)["action"] is None

    failure_with_pass = dict(failure_response, action=[])
    ok_without_action = dict(response, action=None)
    with pytest.raises(ValueError, match="不能携带动作评分"):
        parse_douzero_response(failure_with_pass)
    with pytest.raises(TypeError, match="action 必须是数组"):
        parse_douzero_response(ok_without_action)


def test_original_douzero_request_rejects_unknown_fields() -> None:
    request = json.loads(DOUZERO_REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    request["legalActions"] = [[]]

    with pytest.raises(ResNet2ProtocolError, match="字段不匹配"):
        parse_douzero_request(request)


def test_request_rejects_unknown_fields() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    request["legalActions"] = [[]]

    with pytest.raises(ResNet2ProtocolError, match="字段不匹配") as caught:
        parse_resnet2_request(request)

    assert caught.value.error_code == "INVALID_SNAPSHOT"


def test_parsed_infoset_builds_the_pinned_resnet2_tensor_shapes() -> None:
    pytest.importorskip("torch")
    from douzero_advisor.engine.resnet2_observation import build_resnet2_observation

    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    parsed = parse_resnet2_request(request)

    observation = build_resnet2_observation(parsed.infoset)
    assert observation["x_batch"].shape == (2, 15)
    assert observation["z_batch"].shape == (2, 40, 54)


def test_empty_action_remains_a_successful_pass_response() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    parsed = parse_resnet2_request(request)

    response = build_success_response(
        parsed,
        [],
        [0.3, 0.8],
        model_version="resnet2-v1",
        latency_ms=18,
    )

    assert response["status"] == "OK"
    assert response["action"] == []
    assert response["actionValue"] == pytest.approx(0.8)
    assert response["actionMargin"] == pytest.approx(0.5)
    assert response["actionScores"] == [
        {"action": [20, 30], "value": pytest.approx(0.3)},
        {"action": [], "value": pytest.approx(0.8)},
    ]


def test_response_rejects_action_outside_java_legal_set() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    parsed = parse_resnet2_request(request)

    with pytest.raises(ResNet2ProtocolError, match="合法动作") as caught:
        build_success_response(
            parsed,
            [3],
            [0.3, 0.8],
            model_version="resnet2-v1",
            latency_ms=18,
        )

    assert caught.value.error_code == "ILLEGAL_ACTION"


def test_protocol_validation_error_has_a_stable_java_error_code() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    request["contractVersion"] = "inference.v0"

    with pytest.raises(ResNet2ProtocolError) as caught:
        parse_resnet2_request(request)

    assert caught.value.error_code == "UNSUPPORTED_CONTRACT"
    response = build_protocol_error_response(
        request_id=request["requestId"],
        deal_id=request["dealId"],
        error=caught.value,
        latency_ms=1,
    )
    assert response["status"] == "ERROR"
    assert response["errorCode"] == "UNSUPPORTED_CONTRACT"
    assert response["modelVersion"] is None
    assert response["actionScores"] is None


def test_response_rejects_model_value_count_mismatch() -> None:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    parsed = parse_resnet2_request(request)

    with pytest.raises(ValueError, match="数量"):
        build_success_response(
            parsed,
            [],
            [0.8],
            model_version="resnet2-v1",
            latency_ms=18,
        )
