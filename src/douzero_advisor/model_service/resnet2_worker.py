"""正式出牌 ResNet2 持久 JSONL worker。"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Protocol, TextIO

from douzero_advisor.model_service.resnet2_protocol import (
    CONTRACT_VERSION,
    MODEL_ID,
    ParsedResNet2Request,
    ResNet2ProtocolError,
    build_protocol_error_response,
    build_success_response,
    parse_resnet2_request,
)

POSITION_ORDER = ("landlord", "landlord_down", "landlord_up")


class ResNet2Evaluator(Protocol):
    """已加载模型的最小推理能力；生命周期跨越整个 worker 进程。"""

    @property
    def model_version(self) -> str:
        """返回当前三个座位共同对应的固定制品版本。"""

    def evaluate(
        self, request: ParsedResNet2Request
    ) -> tuple[list[int], tuple[float, ...]]:
        """按请求座位评分全部合法动作，并返回最高分动作。"""


class LoadedResNet2Evaluator:
    """一次加载并复用三个座位的 ``SafeResNet2Agent``。"""

    def __init__(self, agents: Mapping[str, Any], model_version: str) -> None:
        if set(agents) != set(POSITION_ORDER):
            raise ValueError("ResNet2 evaluator 必须恰好加载三个座位模型")
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("ResNet2 evaluator 模型版本不能为空")
        self._agents = dict(agents)
        self._model_version = model_version

    @property
    def model_version(self) -> str:
        return self._model_version

    def evaluate(
        self, request: ParsedResNet2Request
    ) -> tuple[list[int], tuple[float, ...]]:
        position = request.infoset.player_position
        try:
            agent = self._agents[position]
        except KeyError as error:
            raise ValueError(f"未加载请求座位的 ResNet2 模型: {position}") from error
        return agent.act_with_values(request.infoset)


def load_evaluator(
    manifest_path: str | Path, *, device: str = "cpu"
) -> LoadedResNet2Evaluator:
    """从可信 manifest 加载三个座位模型；任何制品问题都在 READY 前失败。"""

    # 延迟导入让协议/worker 单测可在未安装 PyTorch 的环境注入 fake evaluator。
    from douzero_advisor.engine.resnet2_agent import (
        PINNED_UPSTREAM_COMMIT,
        SafeResNet2Agent,
    )

    agents = {
        position: SafeResNet2Agent.from_manifest(
            position,
            manifest_path,
            device=device,
        )
        for position in POSITION_ORDER
    }
    return LoadedResNet2Evaluator(
        agents,
        model_version=f"resnet2-{PINNED_UPSTREAM_COMMIT[:12]}",
    )


def serve(
    evaluator: ResNet2Evaluator,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """处理多次串行请求；stdout 始终只有 READY 和一行一个响应。"""

    _write(
        output_stream,
        {
            "contractVersion": CONTRACT_VERSION,
            "messageType": "READY",
            "modelId": MODEL_ID,
        },
    )
    try:
        for line in input_stream:
            _write(
                output_stream,
                _handle_line(
                    line,
                    evaluator=evaluator,
                    error_stream=error_stream,
                    monotonic=monotonic,
                ),
            )
    finally:
        close = getattr(evaluator, "close", None)
        if callable(close):
            with redirect_stdout(error_stream):
                close()
    return 0


def _handle_line(
    line: str,
    *,
    evaluator: ResNet2Evaluator,
    error_stream: TextIO,
    monotonic: Callable[[], float],
) -> dict[str, Any]:
    started = monotonic()
    raw: Mapping[str, Any] | None = None
    try:
        decoded = json.loads(line)
        raw = decoded if isinstance(decoded, Mapping) else None
        request = parse_resnet2_request(decoded)
        if _expired(started, request.deadline_ms, monotonic()):
            return _failure(
                raw,
                error_code="MODEL_TIMEOUT",
                message="ResNet2 inference deadline elapsed before model call",
                latency_ms=_latency_ms(started, monotonic()),
                model_version=evaluator.model_version,
            )
        with redirect_stdout(error_stream):
            action, values = evaluator.evaluate(request)
        finished = monotonic()
        latency_ms = _latency_ms(started, finished)
        if _expired(started, request.deadline_ms, finished):
            return _failure(
                raw,
                error_code="MODEL_TIMEOUT",
                message="ResNet2 inference exceeded deadlineMs",
                latency_ms=latency_ms,
                model_version=evaluator.model_version,
            )
        return build_success_response(
            request,
            action,
            values,
            model_version=evaluator.model_version,
            latency_ms=latency_ms,
        )
    except json.JSONDecodeError as error:
        return _failure(
            raw,
            error_code="INVALID_SNAPSHOT",
            message=f"invalid JSON request: {error.msg}",
            latency_ms=_latency_ms(started, monotonic()),
        )
    except ResNet2ProtocolError as error:
        return _failure(
            raw,
            error_code=error.error_code,
            message=str(error),
            latency_ms=_latency_ms(started, monotonic()),
            model_version=evaluator.model_version,
        )
    except Exception as error:  # noqa: BLE001 - model process boundary must fail closed
        print(
            f"resnet2_inference_failed:{type(error).__name__}:{error}",
            file=error_stream,
            flush=True,
        )
        return _failure(
            raw,
            error_code="MODEL_UNAVAILABLE",
            message=f"{type(error).__name__}: ResNet2 inference failed",
            latency_ms=_latency_ms(started, monotonic()),
            model_version=evaluator.model_version,
        )


def _failure(
    raw: Mapping[str, Any] | None,
    *,
    error_code: str,
    message: str,
    latency_ms: int,
    model_version: str | None = None,
) -> dict[str, Any]:
    request_id, deal_id = _identity(raw)
    return build_protocol_error_response(
        request_id=request_id,
        deal_id=deal_id,
        error=ResNet2ProtocolError(error_code, message),
        latency_ms=latency_ms,
        model_version=model_version,
    )


def _identity(raw: Mapping[str, Any] | None) -> tuple[str, str]:
    raw = raw or {}
    request_id = raw.get("requestId")
    deal_id = raw.get("dealId")
    return (
        request_id if isinstance(request_id, str) and request_id.strip() else "UNKNOWN",
        deal_id if isinstance(deal_id, str) and deal_id.strip() else "UNKNOWN",
    )


def _expired(started: float, deadline_ms: int, now: float) -> bool:
    return now - started >= deadline_ms / 1000.0


def _latency_ms(started: float, finished: float) -> int:
    return max(0, math.ceil((finished - started) * 1000.0))


def _write(stream: TextIO, value: Mapping[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="启动独立 ResNet2 JSONL 模型服务；stdout 仅输出协议消息。"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="受信任的 ResNet2 模型清单路径",
    )
    parser.add_argument("--device", default="cpu", help="PyTorch 推理设备，默认 cpu")
    arguments = parser.parse_args(argv)
    # checkpoint 加载期间的第三方诊断也不得进入协议 stdout。
    with redirect_stdout(sys.stderr):
        evaluator = load_evaluator(arguments.manifest, device=arguments.device)
    return serve(evaluator)


if __name__ == "__main__":
    raise SystemExit(main())
