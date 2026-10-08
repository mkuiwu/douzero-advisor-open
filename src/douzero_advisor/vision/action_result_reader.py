from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

from douzero_advisor.recognition_types import ActionResultState
from douzero_advisor.vision.hand_reader import FRAME_HEIGHT, FRAME_WIDTH
from douzero_advisor.vision.table_reader import (
    DESKTOP_CARD_WIDTH,
    REGIONS,
    JokerWordCatalog,
    _locate_card_seams,
    _locate_group,
)


@dataclass(frozen=True, slots=True)
class ActionResultRead:
    states: Mapping[str, ActionResultState]
    pass_maximum_distance: float | None
    visible_counts: Mapping[str, int | None] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.visible_counts, Mapping) or any(
            not isinstance(region, str)
            or (count is not None and (type(count) is not int or count < 0))
            for region, count in self.visible_counts.items()
        ):
            raise ValueError("visible_counts must map region names to non-negative int or None")


class ActionResultReader:
    """Classify the fixed result regions without TurnReady evidence."""

    def __init__(self, pass_reader, joker_word_catalog: JokerWordCatalog | None = None) -> None:
        self._pass_reader = pass_reader
        self._joker_word_catalog = joker_word_catalog

    def read(
        self,
        frame: np.ndarray,
        *,
        expected_region: str | None = None,
    ) -> ActionResultRead:
        if not isinstance(frame, np.ndarray) or frame.shape != (
            FRAME_HEIGHT,
            FRAME_WIDTH,
            3,
        ):
            raise ValueError("frame must be a 1455x819 3-channel BGR image")
        if expected_region is not None and expected_region not in REGIONS:
            raise ValueError("expected_region must be a known action-result region")
        pass_read = self._pass_reader.read(frame)
        pass_markers = pass_read.markers if pass_read is not None else frozenset()
        states: dict[str, ActionResultState] = {}
        visible_counts: dict[str, int | None] = {}
        for region, spec in REGIONS.items():
            group = _locate_group(frame, spec)
            anchored_joker = (
                self._anchored_joker(frame, spec)
                if group is None
                else None
            )
            play_visible = group is not None or anchored_joker is not None
            # The broad white face is reliable presence evidence, but its
            # total width is not a semantic card count. Continuous vertical
            # card seams determine side-action slots without consulting rank
            # or suit pixels.
            seams = _locate_card_seams(frame, spec) if group is not None else None
            geometry = seams or group
            visible_counts[region] = (
                len(geometry[0])
                if geometry is not None
                else 1
                if anchored_joker is not None
                else None
            )
            pass_visible = f"{region.removesuffix('_play')}_pass" in pass_markers
            if play_visible and pass_visible:
                state = ActionResultState.AMBIGUOUS
            elif play_visible:
                state = ActionResultState.PLAY
            elif pass_visible:
                state = ActionResultState.PASS
            else:
                state = ActionResultState.WAIT
            states[region] = state
        return ActionResultRead(
            states=MappingProxyType(states),
            pass_maximum_distance=(pass_read.maximum_distance if pass_read is not None else None),
            visible_counts=MappingProxyType(visible_counts),
        )

    def _anchored_joker(self, frame: np.ndarray, spec) -> str | None:
        """动作区无普通卡面时，只接受完整 JOKER 字样作为出牌可见证据。"""
        if self._joker_word_catalog is None:
            return None
        if spec.anchor_mode == "left":
            left = round(spec.anchor)
        elif spec.anchor_mode == "right":
            left = round(spec.anchor - DESKTOP_CARD_WIDTH)
        else:
            left = round(spec.anchor - DESKTOP_CARD_WIDTH / 2)
        return self._joker_word_catalog.rank_near(frame, left, spec.expected_top)
