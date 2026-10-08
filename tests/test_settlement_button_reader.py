from __future__ import annotations

import cv2
import numpy as np

from douzero_advisor.vision.settlement_button_reader import SettlementButtonReader


def _frame(*, include_blue: bool = True, include_orange: bool = True) -> np.ndarray:
    height, width = 819, 1455
    frame = np.full((height, width, 3), (145, 105, 70), dtype=np.uint8)
    if include_blue:
        cv2.rectangle(frame, (507, 661), (698, 719), (230, 195, 105), thickness=-1)
    if include_orange:
        cv2.rectangle(frame, (757, 661), (948, 719), (35, 205, 250), thickness=-1)
    return frame


def test_reads_the_two_settlement_controls_from_their_dedicated_layout() -> None:
    result = SettlementButtonReader().read(_frame())

    assert result.stage == "settlement"
    assert result.texts == ("明牌开始×5", "继续游戏")
    assert result.buttons[0].name == "reveal_start"
    assert result.buttons[1].name == "continue_game"
    assert all(button.confidence > 0.8 for button in result.buttons)


def test_keeps_partial_settlement_layout_fail_closed() -> None:
    result = SettlementButtonReader().read(_frame(include_orange=False))

    assert result.stage == "settlement"
    assert result.texts == ("明牌开始×5",)
    assert result.reason == "partial_settlement_layout"


def test_does_not_classify_play_area_as_settlement() -> None:
    result = SettlementButtonReader().read(
        np.full((819, 1455, 3), (145, 105, 70), dtype=np.uint8)
    )

    assert result.stage == "unknown"
    assert result.buttons == ()
