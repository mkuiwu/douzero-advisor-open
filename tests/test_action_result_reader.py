from __future__ import annotations

from pathlib import Path
from private_asset_paths import PRIVATE_SPIKES_ROOT

import cv2
import numpy as np
import pytest

from douzero_advisor.recognition_types import ActionResultState
from douzero_advisor.vision.action_result_reader import ActionResultReader
from douzero_advisor.vision.live_markers import PassMarkerCatalog, PassMarkerReader
from douzero_advisor.vision.live_observation import LiveMarkerRead

pytestmark = pytest.mark.table

CAPTURE_ROOT = PRIVATE_SPIKES_ROOT / "wgc/captures"
TABLE_ROOT = CAPTURE_ROOT / "game-20260815-223120"
PASS_ROOT = CAPTURE_ROOT / "game-20260815-233322"
EMPTY_FRAME = TABLE_ROOT / "frame_0006_stable_after_change_0025.052.png"
RIGHT_PLAY_FRAME = TABLE_ROOT / "frame_0011_stable_after_change_0042.646.png"
MY_PLAY_FRAME = TABLE_ROOT / "frame_0021_stable_after_change_0068.864.png"
LEFT_PLAY_FRAME = TABLE_ROOT / "frame_0026_stable_after_change_0091.146.png"
MY_PASS_FRAME = PASS_ROOT / "frame_0040_stable_after_change_0128.854.png"
OPPONENT_PASS_FRAME = PASS_ROOT / "frame_0080_stable_after_change_0257.198.png"
GREEN_SMALL_JOKER_FRAME = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787059967518409800/frame_01199_seq_001200.jpg"
PASS_WITH_ANCHOR_ARTWORK_FRAME = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787059967518409800/frame_00053_seq_000054.jpg"
MY_PASS_BOX = (672, 461, 783, 534)
LEFT_PASS_BOX = (376, 377, 488, 430)
RIGHT_PASS_BOX = (968, 377, 1080, 430)


def load(path: Path) -> np.ndarray:
    if not path.exists():
        pytest.skip(f"live action-result frame is unavailable: {path}")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


@pytest.fixture(scope="module")
def reader() -> ActionResultReader:
    self_pass = load(MY_PASS_FRAME)
    opponent_pass = load(OPPONENT_PASS_FRAME)
    pass_reader = PassMarkerReader(
        PassMarkerCatalog.from_labeled_frames(
            (
                (self_pass, "my_pass", MY_PASS_BOX),
                (opponent_pass, "left_pass", LEFT_PASS_BOX),
                (opponent_pass, "right_pass", RIGHT_PASS_BOX),
            )
        )
    )
    return ActionResultReader(pass_reader)


def test_clean_result_region_is_wait_without_turn_ready(
    reader: ActionResultReader,
) -> None:
    """Catches a clean canonical result ROI being treated as unreadable."""
    result = reader.read(load(EMPTY_FRAME))

    assert set(result.states.values()) == {ActionResultState("wait")}


@pytest.mark.parametrize(
    ("path", "region"),
    (
        (LEFT_PLAY_FRAME, "left_play"),
        (RIGHT_PLAY_FRAME, "right_play"),
        (MY_PLAY_FRAME, "my_play"),
    ),
)
def test_real_table_cards_need_only_play_presence(
    reader: ActionResultReader,
    path: Path,
    region: str,
) -> None:
    """Catches reintroducing rank classification into the action gate."""
    result = reader.read(load(path))

    assert result.states[region] is ActionResultState.PLAY


def test_unverified_themed_small_joker_stays_wait_without_word_template(
    reader: ActionResultReader,
) -> None:
    """未纳入完整 JOKER 字样标定的主题小王，不能凭卡面颜色推进为出牌。"""
    result = reader.read(load(GREEN_SMALL_JOKER_FRAME))

    assert result.states["right_play"] is ActionResultState.WAIT
    assert result.visible_counts["right_play"] is None


def test_anchor_artwork_does_not_turn_a_real_pass_into_ambiguous_play(
    reader: ActionResultReader,
) -> None:
    """固定锚点的装饰图案没有完整 JOKER 字样时，必须保留已读到的不出。"""
    result = reader.read(load(PASS_WITH_ANCHOR_ARTWORK_FRAME))

    assert result.states["left_play"] is ActionResultState.PASS
    assert result.visible_counts["left_play"] is None


def test_real_pass_text_is_separate_from_play_presence(reader: ActionResultReader) -> None:
    """Catches treating the Chinese Pass glyph as a white card group."""
    self_result = reader.read(load(MY_PASS_FRAME))
    opponent_result = reader.read(load(OPPONENT_PASS_FRAME))

    assert self_result.states["my_play"] is ActionResultState.PASS
    assert opponent_result.states["left_play"] is ActionResultState.PASS
    assert opponent_result.states["right_play"] is ActionResultState.PASS


def test_cards_and_pass_in_one_region_are_ambiguous(reader: ActionResultReader) -> None:
    """Catches choosing Play or Pass when contradictory result pixels coexist."""

    class FixedPassReader:
        def read(self, _frame: np.ndarray) -> LiveMarkerRead:
            return LiveMarkerRead(frozenset({"my_pass"}), maximum_distance=0.01)

    result = ActionResultReader(FixedPassReader()).read(load(MY_PLAY_FRAME))

    assert result.states["my_play"] is ActionResultState.AMBIGUOUS


def test_vertical_card_seams_replace_a_coarse_visible_count_undercount(
    monkeypatch,
) -> None:
    """Card seams, not rank glyphs, determine the visible card count."""
    group_calls: list[str] = []
    seam_calls: list[str] = []

    def locate_group(_frame, spec):
        region = {
            "left": "left_play",
            "right": "right_play",
            "center": "my_play",
        }[spec.anchor_mode]
        group_calls.append(region)
        if region == "left_play":
            # Deliberately undercount the regularly spaced corner slots below.
            return ((101,), 300)
        return None

    def locate_card_seams(_frame, spec):
        region = {
            "left": "left_play",
            "right": "right_play",
            "center": "my_play",
        }[spec.anchor_mode]
        seam_calls.append(region)
        if region == "left_play":
            return ((101, 137, 173), 300)
        return None

    class NoPassReader:
        def read(self, _frame):
            return None

    monkeypatch.setattr(
        "douzero_advisor.vision.action_result_reader._locate_group",
        locate_group,
    )
    monkeypatch.setattr(
        "douzero_advisor.vision.action_result_reader._locate_card_seams",
        locate_card_seams,
    )

    result = ActionResultReader(NoPassReader()).read(np.zeros((819, 1455, 3), dtype=np.uint8))

    assert result.states["left_play"] is ActionResultState.PLAY
    assert result.visible_counts == {
        "left_play": 3,
        "right_play": None,
        "my_play": None,
    }
    assert group_calls == ["left_play", "right_play", "my_play"]
    assert seam_calls == ["left_play"]


def test_vertical_card_seams_report_nine_visible_cards() -> None:
    path = PRIVATE_SPIKES_ROOT / "deal-recordings/run-1787163276660790600/frame_03767_seq_006487.jpg"
    if not path.exists():
        pytest.skip("current nine-card geometry regression replay is unavailable")
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None

    class NoPassReader:
        def read(self, _frame):
            return None

    result = ActionResultReader(NoPassReader()).read(frame)

    assert result.visible_counts["left_play"] == 9
