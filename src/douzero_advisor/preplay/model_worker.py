"""常驻局前模型 JSONL Worker；与 CV Worker 独立启动。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, TextIO

from douzero_advisor.preplay.legacy_model import LegacyFullAutoModel
from douzero_advisor.preplay.model_protocol import (
    CONTRACT_VERSION,
    PreplayProtocolError,
    ScoreModel,
    evaluate_request,
    failure_response,
    parse_request,
)


def serve(
    model: ScoreModel,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
    monotonic=time.monotonic,
) -> int:
    """逐行执行模型请求；stdout 始终只有紧凑 JSON。"""

    _write(
        output_stream,
        {"contractVersion": CONTRACT_VERSION, "messageType": "READY"},
    )
    for line in input_stream:
        raw: dict[str, Any] | None = None
        started = monotonic()
        try:
            value = json.loads(line)
            raw = value if isinstance(value, dict) else None
            request = parse_request(value)
            remaining_seconds = request.deadline_ms / 1000.0 - (monotonic() - started)
            if remaining_seconds <= 0:
                raise TimeoutError("preplay model deadline elapsed before inference")
            # 第三方 legacy 路径可能打印诊断，协议 stdout 必须保持干净。
            with redirect_stdout(error_stream):
                response = evaluate_request(
                    request,
                    model,
                    timeout_seconds=remaining_seconds,
                )
            elapsed_ms = max(0, round((monotonic() - started) * 1000))
            response["latencyMs"] = elapsed_ms
            if elapsed_ms >= request.deadline_ms:
                response = failure_response(
                    raw,
                    error_code="MODEL_TIMEOUT",
                    error_message="preplay model exceeded deadlineMs",
                )
        except (json.JSONDecodeError, PreplayProtocolError) as error:
            response = failure_response(
                raw,
                error_code="INVALID_REQUEST",
                error_message=str(error),
            )
        except TimeoutError:
            response = failure_response(
                raw,
                error_code="MODEL_TIMEOUT",
                error_message="preplay model exceeded deadlineMs",
            )
        except Exception as error:  # noqa: BLE001 - model boundary must fail closed
            response = failure_response(
                raw,
                error_code="MODEL_UNAVAILABLE",
                error_message=f"{type(error).__name__}:legacy model request failed",
            )
        _write(output_stream, response)
    close = getattr(model, "close", None)
    if callable(close):
        with redirect_stdout(error_stream):
            close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    model = LegacyFullAutoModel(arguments.legacy_root)
    try:
        # 在 READY 前预热，避免首局第一次局前建议承担 legacy FullAuto 的加载耗时。
        model.warmup()
        return serve(model)
    except BaseException:
        model.close()
        raise


def _write(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
