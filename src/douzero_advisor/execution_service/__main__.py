"""``python -m douzero_advisor.execution_service`` 生产入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from douzero_advisor.execution_service.runtime import build_execution_runtime
from douzero_advisor.execution_service.worker import ExecutionWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="启动独立 Python 本方出牌执行 JSONL 服务；仅 Java 授权后点击鼠标。"
    )
    parser.add_argument(
        "--calibration-manifest",
        type=Path,
        required=True,
        help="1455x819 Windows 实时识别标定清单路径",
    )
    arguments = parser.parse_args(argv)
    runtime = build_execution_runtime(arguments.calibration_manifest)
    return ExecutionWorker(runtime).run()


if __name__ == "__main__":
    raise SystemExit(main())
