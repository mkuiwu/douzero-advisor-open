from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from douzero_advisor.vision.action_button_reader import ActionButtonReader


ROOT = Path(__file__).parents[1]
COLLECTION = ROOT / "tests" / "fixtures" / "action_button_play_collection.json"


def test_curated_real_frames_match_their_reviewed_button_layouts() -> None:
    """Catches palette/geometry tuning that passes synthetic but not real UI frames."""
    collection = json.loads(COLLECTION.read_text(encoding="utf-8"))
    roots = {
        name: path if path.is_absolute() else ROOT / path
        for name, path in ((key, Path(value)) for key, value in collection["roots"].items())
    }
    reader = ActionButtonReader()
    mismatches: list[tuple[str, str, str]] = []
    seen = 0
    for sample in collection["samples"]:
        path = roots[sample["root"]] / sample["path"]
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            continue
        seen += 1
        actual = reader.read(frame).layout
        if actual != sample["expected_layout"]:
            mismatches.append((str(path), sample["expected_layout"], actual))
    if seen == 0:
        pytest.skip("reviewed button replay frames are not available")

    assert mismatches == []
