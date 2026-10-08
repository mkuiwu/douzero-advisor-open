from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import pytest

from douzero_advisor.vision.three_cards_reader import BottomTemplateCatalog, ThreeCardsReader


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
CORE_ROOT = FIXTURE_ROOT / "core_cases" / "bottom"
CORE_MANIFEST = json.loads(
    (FIXTURE_ROOT / "core_bottom_cases.json").read_text(encoding="utf-8")
)
pytestmark = [pytest.mark.core, pytest.mark.bottom, pytest.mark.private_assets]


def _load_hash_verified(path: Path, expected_sha256: str):
    assert path.is_file(), f"core OCR asset is missing: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None, f"core OCR asset is unreadable: {path}"
    return frame


def _core_reader() -> ThreeCardsReader:
    samples = []
    for item in CORE_MANIFEST["calibration"]:
        samples.append(
            (
                _load_hash_verified(CORE_ROOT / item["path"], item["sha256"]),
                tuple(item["cards"]),
            )
        )
    return ThreeCardsReader(BottomTemplateCatalog.from_labeled_frames(samples))


@pytest.mark.parametrize(
    "case",
    CORE_MANIFEST["cases"],
    ids=[case["name"] for case in CORE_MANIFEST["cases"]],
)
def test_core_bottom_cases_are_immutable_holdouts(
    case: dict[str, object],
) -> None:
    path = CORE_ROOT / str(case["path"])
    frame = _load_hash_verified(path, str(case["sha256"]))

    result = _core_reader().read(frame)

    assert result is not None
    assert result.cards == tuple(case["cards"])
