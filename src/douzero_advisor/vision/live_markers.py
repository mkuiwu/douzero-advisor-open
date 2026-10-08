from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from douzero_advisor.vision.hand_reader import FRAME_HEIGHT, FRAME_WIDTH
from douzero_advisor.vision.live_observation import LiveMarkerRead


Box = tuple[int, int, int, int]

_PASS_SEARCH_BOXES: dict[str, Box] = {
    "my_pass": (660, 450, 795, 545),
    "left_pass": (365, 365, 500, 440),
    "right_pass": (955, 365, 1090, 440),
}


@dataclass(frozen=True)
class PassMarkerCatalog:
    templates: tuple[np.ndarray, ...]

    @classmethod
    def from_labeled_frames(
        cls, samples: Sequence[tuple[np.ndarray, str, Box]]
    ) -> PassMarkerCatalog:
        templates: list[np.ndarray] = []
        for frame, marker, box in samples:
            _validate_frame(frame)
            if marker not in _PASS_SEARCH_BOXES:
                raise ValueError(f"unknown pass marker: {marker}")
            left, top, right, bottom = _validate_box(box)
            template = np.ascontiguousarray(frame[top:bottom, left:right])
            if template.size == 0 or float(template.std()) < 1.0:
                raise ValueError(f"pass marker template is empty or uniform: {marker}")
            templates.append(template)
        if not templates:
            raise ValueError("at least one pass marker template is required")
        return cls(tuple(templates))


class PassMarkerReader:
    # The text is rendered over each player's live avatar/background rather
    # than a solid panel.  A real left-side ``不出`` from the current client
    # theme scores 0.841 against the original calibration while the matching
    # right-side text scores 0.864.  0.85 therefore made one missing marker
    # block an otherwise stable local turn forever.  This is a fixed
    # play-result classifier: a visible card group wins as PLAY, a matched
    # ``不出`` marker is PASS, and a genuinely empty region remains WAIT.
    # 0.75 remains far above
    # the observed no-marker ceiling (0.35 in the retained live clips), and
    # the snapshot gate still requires two matching frames before committing
    # a pass.
    def __init__(self, catalog: PassMarkerCatalog, minimum_score: float = 0.75) -> None:
        if not 0.0 < minimum_score <= 1.0:
            raise ValueError("minimum_score must be in (0, 1]")
        self._catalog = catalog
        self._minimum_score = minimum_score

    def read(self, frame: np.ndarray) -> LiveMarkerRead | None:
        _validate_frame(frame)
        detected: set[str] = set()
        distances: list[float] = []
        for marker, box in _PASS_SEARCH_BOXES.items():
            left, top, right, bottom = box
            crop = frame[top:bottom, left:right]
            score = max(
                _template_score(crop, template)
                for template in self._catalog.templates
                if template.shape[0] <= crop.shape[0]
                and template.shape[1] <= crop.shape[1]
            )
            if score >= self._minimum_score:
                detected.add(marker)
                distances.append(1.0 - score)
        if not detected:
            return None
        return LiveMarkerRead(
            markers=frozenset(detected),
            maximum_distance=max(distances),
        )


def _template_score(
    crop: np.ndarray,
    template: np.ndarray,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> float:
    result = cv2.matchTemplate(crop, template, method)
    score = float(np.nanmax(result))
    return score if np.isfinite(score) else -1.0


def _validate_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.shape != (
        FRAME_HEIGHT,
        FRAME_WIDTH,
        3,
    ):
        raise ValueError("frame must be a 1455x819 3-channel BGR image")


def _validate_box(box: Box) -> Box:
    left, top, right, bottom = box
    if not (0 <= left < right <= FRAME_WIDTH and 0 <= top < bottom <= FRAME_HEIGHT):
        raise ValueError(f"invalid marker box: {box}")
    return box
