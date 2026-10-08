from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import douzero_advisor.model_service.resnet2_worker as worker_module
from douzero_advisor.model_service.resnet2_worker import (
    _handle_line,
    load_evaluator,
    main,
)

ROOT = Path(__file__).parents[1]
REQUEST_EXAMPLE = (
    ROOT / "contracts" / "inference" / "v1" / "resnet2.request.example.json"
)


class FakeEvaluator:
    model_version = "fake-resnet2-v1"

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, _request: Any) -> tuple[list[int], tuple[float, ...]]:
        self.calls += 1
        return [], (0.3, 0.8)


def _request(**updates: Any) -> dict[str, Any]:
    request = json.loads(REQUEST_EXAMPLE.read_text(encoding="utf-8"))
    request.update(updates)
    return request


def _clock(*values: float):
    iterator = iter(values)
    return lambda: next(iterator)


def test_real_subprocess_emits_exact_ready_and_reuses_one_evaluator() -> None:
    first = _request(requestId="request-1")
    second = _request(requestId="request-2")
    source = """
from douzero_advisor.model_service.resnet2_worker import serve

class Evaluator:
    model_version = "fake-resnet2-subprocess"

    def __init__(self):
        self.calls = 0

    def evaluate(self, request):
        self.calls += 1
        print(f"fake diagnostic {self.calls}")
        if self.calls == 1:
            return [], (0.3, 0.8)
        return [20, 30], (0.9, 0.2)

raise SystemExit(serve(Evaluator()))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(ROOT / "src"),
            str(ROOT / "vendor" / "DouZero"),
            environment.get("PYTHONPATH", ""),
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=environment,
        input="\n".join((json.dumps(first), json.dumps(second), "")),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    messages = [json.loads(line) for line in completed.stdout.splitlines()]
    assert messages[0] == {
        "contractVersion": "inference.v1",
        "messageType": "READY",
        "modelId": "resnet2",
    }
    assert [message["requestId"] for message in messages[1:]] == [
        "request-1",
        "request-2",
    ]
    assert messages[1]["action"] == []
    assert messages[2]["action"] == [20, 30]
    assert completed.stderr.splitlines() == ["fake diagnostic 1", "fake diagnostic 2"]


def test_unknown_request_field_fails_without_calling_model() -> None:
    evaluator = FakeEvaluator()
    request = _request(extraField=True)

    response = _handle_line(
        json.dumps(request),
        evaluator=evaluator,
        error_stream=io.StringIO(),
        monotonic=_clock(0.0, 0.001),
    )

    assert response["requestId"] == request["requestId"]
    assert response["status"] == "ERROR"
    assert response["errorCode"] == "INVALID_SNAPSHOT"
    assert response["action"] is None
    assert response["actionScores"] is None
    assert evaluator.calls == 0


def test_malformed_json_returns_explicit_null_action_failure() -> None:
    evaluator = FakeEvaluator()

    response = _handle_line(
        "{not-json}",
        evaluator=evaluator,
        error_stream=io.StringIO(),
        monotonic=_clock(0.0, 0.001),
    )

    assert response["requestId"] == "UNKNOWN"
    assert response["dealId"] == "UNKNOWN"
    assert response["status"] == "ERROR"
    assert response["errorCode"] == "INVALID_SNAPSHOT"
    assert response["action"] is None
    assert response["actionValue"] is None
    assert response["actionMargin"] is None
    assert response["actionScores"] is None
    assert evaluator.calls == 0


def test_deadline_gate_before_model_call_returns_null_action() -> None:
    evaluator = FakeEvaluator()
    request = _request(deadlineMs=1)

    response = _handle_line(
        json.dumps(request),
        evaluator=evaluator,
        error_stream=io.StringIO(),
        monotonic=_clock(0.0, 0.001, 0.001),
    )

    assert response["errorCode"] == "MODEL_TIMEOUT"
    assert response["action"] is None
    assert evaluator.calls == 0


def test_deadline_gate_after_model_call_discards_late_action() -> None:
    evaluator = FakeEvaluator()
    request = _request(deadlineMs=1)

    response = _handle_line(
        json.dumps(request),
        evaluator=evaluator,
        error_stream=io.StringIO(),
        monotonic=_clock(0.0, 0.0, 0.002),
    )

    assert response["errorCode"] == "MODEL_TIMEOUT"
    assert response["action"] is None
    assert response["actionValue"] is None
    assert response["actionMargin"] is None
    assert response["actionScores"] is None
    assert evaluator.calls == 1


def test_production_loader_builds_all_positions_once_from_safe_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Path, str]] = []

    class FakeSafeAgent:
        @classmethod
        def from_manifest(
            cls, position: str, manifest_path: Path, *, device: str
        ) -> object:
            calls.append((position, manifest_path, device))
            return object()

    module = ModuleType("douzero_advisor.engine.resnet2_agent")
    module.PINNED_UPSTREAM_COMMIT = "1234567890abcdef"
    module.SafeResNet2Agent = FakeSafeAgent
    monkeypatch.setitem(sys.modules, module.__name__, module)
    manifest = Path("models/resnet2/manifest.json")

    evaluator = load_evaluator(manifest, device="cpu")

    assert calls == [
        ("landlord", manifest, "cpu"),
        ("landlord_down", manifest, "cpu"),
        ("landlord_up", manifest, "cpu"),
    ]
    assert evaluator.model_version == "resnet2-1234567890ab"


def test_worker_cli_requires_manifest_before_ready(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main([])

    captured = capsys.readouterr()
    assert caught.value.code == 2
    assert captured.out == ""


def test_worker_does_not_emit_ready_when_model_loading_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_loading(_manifest: Path, *, device: str) -> None:
        raise FileNotFoundError(f"missing ResNet2 manifest for {device}")

    monkeypatch.setattr(worker_module, "load_evaluator", fail_loading)

    with pytest.raises(FileNotFoundError, match="missing ResNet2 manifest"):
        main(["--manifest", "missing.json", "--device", "cpu"])

    captured = capsys.readouterr()
    assert captured.out == ""
