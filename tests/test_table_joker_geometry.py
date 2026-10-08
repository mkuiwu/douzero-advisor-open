from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from douzero_advisor.recognition_service.runtime_assembly import (
    build_live_observation_reader,
)


_ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.private_assets
_MANIFEST = _ROOT / "tests" / "fixtures" / "live_capture_manifest.json"
_JOKER_ROOT = _ROOT / "config" / "live-calibration" / "joker-word"


def _production_table_reader():
    """使用和实机相同的标定构造桌面牌 Reader。"""
    return build_live_observation_reader(_MANIFEST)._table_reader


def test_reads_literal_small_joker_word_from_user_capture() -> None:
    """用户打出灰黑小王时，必须逐字匹配 J、O、K、E、R 后才写入 X。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "small-joker-full-word.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "my_play")

    assert read.value is not None
    assert read.value.cards("my_play") == ("X",)


def test_reads_second_small_joker_template_from_left_seat() -> None:
    """左侧小王采用不同抗锯齿细节时，仍须由第二套完整字样模板确认 X。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "small-joker-left-full-word.png"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("X",)


def test_reads_literal_big_joker_word_when_avatar_hides_card_face() -> None:
    """角色遮住部分大王卡面时，完整红色 JOKER 字样仍可直接确认 D。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-full-word.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("D",)


def test_reads_wgc_left_big_joker_from_five_glyph_structure() -> None:
    """左侧大王的五字形完整时，不得因单个抗锯齿字母而拒绝 D。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-wgc-e079.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "left_play")

    assert read.value is not None
    assert read.value.cards("left_play") == ("D",)


def test_reads_wgc_right_big_joker_from_five_glyph_structure() -> None:
    """右侧大王的五字形完整时，不得因单个抗锯齿字母而拒绝 D。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-right-five-glyphs.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "right_play")

    assert read.value is not None
    assert read.value.cards("right_play") == ("D",)


def test_action_reader_reads_wgc_big_joker_from_five_glyph_structure() -> None:
    """同一张右侧大王在动作判断中也必须是 PLAY，不能只被桌面牌 Reader 接受。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-right-five-glyphs.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    result = build_live_observation_reader(_MANIFEST)._action_result_reader.read(frame)

    assert result.states["right_play"].value == "play"
    assert result.visible_counts["right_play"] == 1


def test_rejects_right_pass_art_without_the_full_joker_word() -> None:
    """同一帧右侧的“不出”图案含红色笔画，也不能构成 JOKER 或桌面出牌。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-full-word.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    read = _production_table_reader().read_region_with_diagnostics(frame, "right_play")

    assert read.value is not None
    assert read.value.cards("right_play") == ()


def test_action_reader_uses_the_same_literal_word_evidence() -> None:
    """动作区在普通卡面几何缺失时，也只能复用完整 JOKER 字样确认出牌可见。"""
    frame = cv2.imread(
        str(_JOKER_ROOT / "big-joker-full-word.jpg"),
        cv2.IMREAD_COLOR,
    )
    assert frame is not None

    result = build_live_observation_reader(_MANIFEST)._action_result_reader.read(frame)

    assert result.states["left_play"].value == "play"
    assert result.states["right_play"].value == "pass"
