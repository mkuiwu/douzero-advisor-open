"""可选的本地回放目录；公开仓库不包含这些个人捕获文件。"""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ASSET_ROOT = Path(
    os.environ.get("DOUZERO_PRIVATE_ASSET_ROOT", REPOSITORY_ROOT / "private-assets")
)
PRIVATE_SPIKES_ROOT = PRIVATE_ASSET_ROOT / "douzero-advisor-spikes"
PRIVATE_BENCHMARK_ROOT = PRIVATE_ASSET_ROOT / "resnet2-benchmark"
