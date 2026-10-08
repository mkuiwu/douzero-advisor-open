"""legacy 模型子进程入口：仅用 JSONL stdin/stdout 传输请求和结果。"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import sys
import warnings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    # 该进程故意与主包隔离，避免同名 douzero 模块和 CUDA 探测影响监听器。
    root = _parser().parse_args(argv).legacy_root.resolve()
    # The reference modules probe CUDA while importing every checkpoint.  The
    # live advisor is CPU-bound and a driver probe can stall the entire
    # pre-play window, so the isolated worker deliberately stays on CPU.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    warnings.filterwarnings("ignore", category=FutureWarning)
    os.chdir(root)
    sys.path.insert(0, str(root))
    print(json.dumps({"ready": True}), flush=True)
    bid_model = None
    farmer_model = None
    landlord_model = None
    landlord_ready = False
    for line in sys.stdin:
        try:
            request = json.loads(line)
            operation = request["operation"]
            cards = "".join(request["cards"])
            # Reference modules print heuristic messages to stdout.  Keep the
            # JSON-lines transport private and discard those non-protocol
            # messages inside the worker.
            with redirect_stdout(io.StringIO()):
                if operation == "bid":
                    if bid_model is None:
                        bid_model = importlib.import_module("BidModel")
                    score = bid_model.predict_score(cards)
                elif operation == "farmer":
                    if farmer_model is None:
                        farmer_model = importlib.import_module("FarmerModel")
                    score = farmer_model.predict(cards, "up")
                elif operation == "landlord":
                    if landlord_model is None:
                        landlord_model = importlib.import_module("LandlordModel")
                    if not landlord_ready:
                        landlord_model.init_model("baselines/resnet/resnet_landlord.ckpt")
                        landlord_ready = True
                    bottom = "".join(request["bottom_cards"])
                    score = landlord_model.predict_by_model(cards, bottom)
                else:
                    raise ValueError(f"unknown operation: {operation}")
            response = {"ok": True, "score": float(score)}
        except Exception as error:
            response = {"ok": False, "error": _error(error)}
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


def _error(error: Exception) -> str:
    return f"{type(error).__name__}:{error}"


if __name__ == "__main__":
    raise SystemExit(main())
