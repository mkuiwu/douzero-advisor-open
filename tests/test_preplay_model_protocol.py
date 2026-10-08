from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import douzero_advisor.preplay.model_worker as model_worker_module
from douzero_advisor.preplay.legacy_model import LegacyFullAutoModel
from douzero_advisor.preplay.model_protocol import (
    PreplayProtocolError,
    evaluate_request,
    parse_request,
    parse_response,
)
from douzero_advisor.preplay.model_worker import serve

HAND17 = ["D", "X", "2", "2", "A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "3"]
HAND20 = HAND17 + ["A", "K", "Q"]
CONTRACT_ROOT = Path(__file__).parents[1] / "contracts" / "preplay-inference" / "v1"


class FakeModel:
    def __init__(self, score: float) -> None:
        self.score = score
        self.calls: list[str] = []

    def score_bid(self, cards, *, timeout_seconds=None):
        self.calls.append("bid")
        return self.score

    def score_farmer(self, cards, *, timeout_seconds=None):
        self.calls.append("farmer")
        return self.score

    def score_landlord(self, cards, bottom_cards, *, timeout_seconds=None):
        self.calls.append("landlord")
        return self.score


def request(**changes):
    value = {
        "contractVersion": "preplay-inference.v1",
        "requestId": "model-1",
        "dealId": "deal-1",
        "generation": 1,
        "modelId": "legacy-preplay",
        "deadlineMs": 8000,
        "stage": "call",
        "hand": HAND17,
        "bottomCards": [],
        "availableActions": ["call", "no_call"],
        "callPromptSeen": False,
        "robPromptSeen": False,
    }
    value.update(changes)
    return value


def test_call_and_rob_use_legacy_threshold_context() -> None:
    """验证叫地主和抢地主都使用 bid 评分，且抢地主在已见叫地主提示时使用 after_call 阈值。

    阈值已在原始 FullAuto 校准值基础上下调 25%（call=0.15，rob_after_call=0.3），
    0.45 分在两个阶段均跨过阈值，分别推荐 call 和 rob。
    """
    model = FakeModel(0.45)
    call = evaluate_request(parse_request(request()), model)
    rob = evaluate_request(
        parse_request(
            request(
                stage="rob",
                availableActions=["rob", "no_rob"],
                callPromptSeen=True,
            )
        ),
        model,
    )
    assert (call["action"], call["threshold"]) == ("call", 0.15)
    assert (rob["action"], rob["threshold"]) == ("rob", 0.3)
    assert call["decisionReason"] == "bid_score_vs_call_threshold"
    assert model.calls == ["bid", "bid"]


def test_landlord_and_farmer_use_separate_score_paths() -> None:
    """验证地主加倍和农民加倍分别走 landlord/farmer 评分路径，且农民分数落在 double 区间。

    阈值下调后 farmer_double=0.75、farmer_super=0.9，农民 0.8 分跨过 double 但未达
    super_double，仍推荐 double；地主 0.55 分跨过 landlord_super=0.375 且手牌含强牌型，
    推荐 super_double 或 double 均合法。
    """
    landlord_model = FakeModel(0.55)
    landlord = evaluate_request(
        parse_request(
            request(
                stage="double",
                hand=HAND20,
                bottomCards=["A", "K", "Q"],
                availableActions=["super_double", "double", "no_double"],
            )
        ),
        landlord_model,
    )
    farmer_model = FakeModel(0.8)
    farmer = evaluate_request(
        parse_request(
            request(
                stage="double",
                availableActions=["super_double", "double", "no_double"],
            )
        ),
        farmer_model,
    )
    assert landlord["action"] in {"super_double", "double"}
    assert farmer["action"] == "double"
    assert landlord_model.calls == ["landlord"]
    assert farmer_model.calls == ["farmer"]


# 验证模型命中超级加倍条件但画面只提供普通加倍时，返回可点击的普通加倍动作。
def test_super_double_downgrades_when_only_double_is_available() -> None:
    model = FakeModel(0.55)
    result = evaluate_request(
        parse_request(
            request(
                stage="double",
                hand=HAND20,
                bottomCards=["A", "K", "Q"],
                availableActions=["double", "no_double"],
            )
        ),
        model,
    )

    assert result["action"] == "double"
    assert result["threshold"] == 0.3
    assert result["decisionReason"] == "super_double_unavailable_fallback_to_double"


def test_recommended_action_must_be_available() -> None:
    parsed = parse_request(request(availableActions=["no_call"]))
    with pytest.raises(PreplayProtocolError, match="absent"):
        evaluate_request(parsed, FakeModel(0.9))


def test_worker_stdout_contains_only_jsonl_and_can_inject_model() -> None:
    class NoisyModel(FakeModel):
        def score_bid(self, cards, *, timeout_seconds=None):
            print("legacy evaluate diagnostic")
            return super().score_bid(cards, timeout_seconds=timeout_seconds)

        def close(self) -> None:
            print("legacy close diagnostic")

    source = io.StringIO(json.dumps(request()) + "\n")
    target = io.StringIO()
    diagnostics = io.StringIO()
    assert (
        serve(
            NoisyModel(0.3),
            input_stream=source,
            output_stream=target,
            error_stream=diagnostics,
        )
        == 0
    )
    lines = [json.loads(line) for line in target.getvalue().splitlines()]
    assert lines[0] == {
        "contractVersion": "preplay-inference.v1",
        "messageType": "READY",
    }
    assert lines[1]["status"] == "OK"
    assert lines[1]["action"] == "call"
    assert lines[1]["modelVersion"].startswith("fullauto-")
    assert lines[1]["latencyMs"] >= 0
    assert diagnostics.getvalue().splitlines() == [
        "legacy evaluate diagnostic",
        "legacy close diagnostic",
    ]


def test_contract_examples_are_fully_loaded_and_semantically_parsed() -> None:
    paths = sorted(CONTRACT_ROOT.glob("*.example.json"))
    assert len(paths) == 2
    examples = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    parsed_request = parse_request(examples["request.example.json"])
    parsed_response = parse_response(examples["response.example.json"])
    assert parsed_request.stage == "double"
    assert parsed_response["status"] == "OK"
    assert parsed_response["action"] in parsed_request.available_actions


def test_worker_passes_remaining_deadline_into_score_model() -> None:
    class DeadlineModel(FakeModel):
        timeout_seconds: float | None = None

        def score_bid(self, cards, *, timeout_seconds=None):
            self.timeout_seconds = timeout_seconds
            return super().score_bid(cards, timeout_seconds=timeout_seconds)

    ticks = iter((10.0, 10.1, 10.2))
    model = DeadlineModel(0.3)
    target = io.StringIO()
    assert serve(
        model,
        input_stream=io.StringIO(json.dumps(request(deadlineMs=1000)) + "\n"),
        output_stream=target,
        monotonic=lambda: next(ticks),
    ) == 0
    assert model.timeout_seconds == pytest.approx(0.9)
    assert json.loads(target.getvalue().splitlines()[1])["status"] == "OK"


def test_worker_maps_legacy_wait_timeout_to_model_timeout() -> None:
    class TimeoutModel(FakeModel):
        def score_bid(self, cards, *, timeout_seconds=None):
            raise TimeoutError("legacy wait expired")

    target = io.StringIO()
    assert serve(
        TimeoutModel(0.3),
        input_stream=io.StringIO(json.dumps(request(deadlineMs=10)) + "\n"),
        output_stream=target,
        monotonic=lambda: 0.0,
    ) == 0
    result = json.loads(target.getvalue().splitlines()[1])
    assert result["status"] == "FAILED"
    assert result["errorCode"] == "MODEL_TIMEOUT"


def test_legacy_model_read_wait_uses_request_remaining_budget(monkeypatch) -> None:
    ticks = iter((10.0, 10.2))
    model = LegacyFullAutoModel(
        ".",
        request_timeout_seconds=8.0,
        monotonic=lambda: next(ticks),
    )
    process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO())
    observed: dict[str, float] = {}
    monkeypatch.setattr(model, "_ensure_process", lambda deadline_at: process)

    def fake_readline(_process, *, timeout_seconds, stage):
        observed[stage] = timeout_seconds
        return '{"ok":true,"score":0.3}\n'

    monkeypatch.setattr(model, "_readline", fake_readline)
    assert model.score_bid(HAND17, timeout_seconds=0.5) == pytest.approx(0.3)
    assert observed["bid"] == pytest.approx(0.3)


# 验证 FullAuto 预热直接复用 bid 评分入口，并使用启动阶段预算，不污染真实牌局请求。
def test_legacy_model_warmup_loads_bid_branch_with_startup_budget(monkeypatch) -> None:
    model = LegacyFullAutoModel(".", startup_timeout_seconds=3.0)
    observed: dict[str, object] = {}

    def fake_score_bid(cards, *, timeout_seconds=None):
        observed["cards"] = tuple(cards)
        observed["timeout_seconds"] = timeout_seconds
        return 0.0

    monkeypatch.setattr(model, "score_bid", fake_score_bid)
    model.warmup()

    assert observed["cards"] == tuple(HAND17)
    assert observed["timeout_seconds"] == pytest.approx(3.0)


# 验证局前模型 Worker 在发送 READY 前完成预热，Java 首次真实请求不再触发加载。
def test_model_worker_prewarms_fullauto_before_ready(monkeypatch) -> None:
    events: list[str] = []

    class WarmupModel:
        def __init__(self, root) -> None:
            events.append(f"init:{root}")

        def warmup(self) -> None:
            events.append("warmup")

        def close(self) -> None:
            events.append("close")

    def fake_serve(model) -> int:
        assert isinstance(model, WarmupModel)
        events.append("serve")
        return 0

    monkeypatch.setattr(model_worker_module, "LegacyFullAutoModel", WarmupModel)
    monkeypatch.setattr(model_worker_module, "serve", fake_serve)

    assert model_worker_module.main(["--legacy-root", "legacy-root"]) == 0
    assert events == ["init:legacy-root", "warmup", "serve"]
