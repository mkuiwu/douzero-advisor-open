"""用固定样例验证局前模型协议和隔离 worker 的生命周期。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from douzero_advisor.preplay import LegacyFullAutoModel


FARMER_HAND = (
    "X", "A", "K", "Q", "J", "10", "9", "8", "8",
    "7", "7", "6", "6", "5", "5", "4", "4",
)
LANDLORD_HAND = (
    "D", "X", "2", "2", "A", "A", "K", "Q", "J", "10",
    "9", "8", "7", "6", "5", "5", "4", "4", "3", "3",
)
BOTTOM_CARDS = ("2", "K", "5")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, required=True)
    args = parser.parse_args()
    model = LegacyFullAutoModel(args.legacy_root)
    try:
        payload = {"operation": "bid", "score": model.score_bid(FARMER_HAND)}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        payload = {"operation": "farmer", "score": model.score_farmer(FARMER_HAND)}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        payload = {
            "operation": "landlord",
            "score": model.score_landlord(LANDLORD_HAND, BOTTOM_CARDS),
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
    finally:
        model.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
