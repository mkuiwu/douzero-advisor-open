"""``python -m douzero_advisor.recognition_service.model_worker`` 生产入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from douzero_advisor.recognition_service.runtime import build_recognition_worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="启动独立 Python CV JSONL 识别服务；不加载模型或输入执行器。"
    )
    parser.add_argument(
        "--calibration-manifest",
        type=Path,
        required=True,
        help="1455x819 Windows 实时识别标定清单路径",
    )
    arguments = parser.parse_args(argv)
    return build_recognition_worker(arguments.calibration_manifest).run()


if __name__ == "__main__":
    raise SystemExit(main())
