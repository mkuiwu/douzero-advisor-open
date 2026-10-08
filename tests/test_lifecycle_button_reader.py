from __future__ import annotations

import cv2
import numpy as np
from douzero_advisor.vision.action_button_reader import ButtonBox
from douzero_advisor.vision import lifecycle_button_reader as lifecycle_module
from douzero_advisor.vision.lifecycle_button_reader import LifecycleButtonReader


TABLE = (175, 132, 105)
ORANGE = (80, 165, 252)
BLUE = (249, 135, 111)


def _frame(reader: LifecycleButtonReader, *items: tuple[str, str, tuple[int, int, int, int]]) -> np.ndarray:
    frame = np.empty((819, 1455, 3), dtype=np.uint8)
    frame[:] = TABLE
    for label, palette, (left, top, right, bottom) in items:
        color = ORANGE if palette == "orange" else BLUE
        cv2.rectangle(frame, (left, top), (right, bottom), color, thickness=-1)
        glyph = reader._templates[label]
        width = min(glyph.shape[1], right - left - 12)
        glyph = cv2.resize(glyph[:, :width], (width, min(32, bottom - top - 12)), interpolation=cv2.INTER_NEAREST)
        y = top + ((bottom - top + 1) - glyph.shape[0]) // 2
        x = left + ((right - left + 1) - glyph.shape[1]) // 2
        crop = frame[y : y + glyph.shape[0], x : x + glyph.shape[1]]
        crop[glyph.astype(bool)] = (255, 255, 255)
    return frame


def test_cannot_beat_is_not_voluntary_pass() -> None:
    """要不起单按钮与可选不出行必须保留不同的正式出牌语义。"""
    reader = LifecycleButtonReader()
    cannot = reader.read(_frame(reader, ("cannot_beat", "blue", (710, 494, 849, 554))))
    voluntary = reader.read(
        _frame(
            reader,
            ("play", "orange", (436, 493, 575, 554)),
            ("pass", "blue", (710, 494, 849, 554)),
            ("hint", "blue", (878, 495, 1022, 555)),
        )
    )

    assert cannot.stage == "playing"
    assert cannot.texts == ("cannot_beat",)
    assert voluntary.stage == "playing"
    assert voluntary.texts == ("play", "pass", "hint")


def test_lifecycle_layouts_are_text_driven() -> None:
    """叫地主和不叫的成组文本，而非按钮坐标，决定局前阶段。"""
    reader = LifecycleButtonReader()
    assert reader.read(_frame(reader, ("reveal", "orange", (658, 497, 797, 559)))).stage == "preplay"
    assert reader.read(
        _frame(
            reader,
            ("call", "orange", (513, 493, 652, 554)),
            ("no_call", "blue", (803, 495, 942, 554)),
        )
    ).stage == "preplay"


def test_partial_preplay_layout_stays_in_preplay_path() -> None:
    """局前动画暂时遮住邻钮时，已确认的加倍仍留在局前监听链。"""
    reader = LifecycleButtonReader()
    result = reader.read(_frame(reader, ("double", "orange", (395, 498, 534, 559))))
    assert result.stage == "preplay"
    assert result.texts == ("double",)
    assert result.reason == "partial_preplay_layout"
    assert reader.read(
        _frame(
            reader,
            ("double", "orange", (395, 498, 534, 559)),
            ("super_double", "orange", (565, 496, 793, 559)),
            ("no_double", "blue", (918, 498, 1063, 560)),
        )
    ).stage == "preplay"


def test_unreadable_neighbor_does_not_drop_confirmed_double_button() -> None:
    """加倍旁的蓝钮不可读时，不能抹掉已直接识别的加倍事实。"""
    reader = LifecycleButtonReader()
    frame = _frame(reader, ("double", "orange", (395, 498, 534, 559)))
    # The blue control is present, but its glyph is hidden by an animation.
    # Candidate geometry must not make the already readable ``double`` label
    # disappear from the pre-play path.
    cv2.rectangle(frame, (918, 498), (1063, 560), BLUE, thickness=-1)

    result = reader.read(frame)

    assert result.stage == "preplay"
    assert result.texts == ("double",)
    assert result.reason == "partial_preplay_layout"


def test_current_no_double_margin_uses_narrow_label_specific_floor(monkeypatch) -> None:
    """无加倍上下文的通用蓝钮匹配仍执行既有的不加倍分差保护。"""
    reader = LifecycleButtonReader()
    template_names = {id(template): name for name, template in reader._templates.items()}
    distances = {
        "no_double": 0.15,
        "no_call": 0.18,
        "no_rob": 0.30,
        "pass": 0.31,
        "hint": 0.32,
        "cannot_beat": 0.33,
        "in_play_reveal": 0.34,
    }
    monkeypatch.setattr(lifecycle_module, "_button_text_mask", lambda *_args: object())
    monkeypatch.setattr(
        lifecycle_module,
        "_template_distance",
        lambda _mask, template: distances[template_names[id(template)]],
    )

    button = reader._classify(
        np.zeros((819, 1455, 3), dtype=np.uint8),
        ButtonBox(921, 500, 1059, 558, "blue"),
    )

    assert button is not None
    assert button.text == "no_double"
    assert button.margin == 0.03


def test_confirmed_double_reads_its_paired_blue_button_only_as_no_double(monkeypatch) -> None:
    """当前行已直接读到加倍时，蓝色配对按钮不与不叫等其他阶段标签竞争。"""
    reader = LifecycleButtonReader()
    template_names = {id(template): name for name, template in reader._templates.items()}
    distances = {
        "reveal": 0.30,
        "call": 0.30,
        "rob": 0.30,
        "double": 0.10,
        "super_double": 0.30,
        "play": 0.30,
        "no_call": 0.1923,
        "no_rob": 0.30,
        "no_double": 0.1729,
        "pass": 0.30,
        "hint": 0.30,
        "cannot_beat": 0.30,
        "in_play_reveal": 0.30,
    }
    monkeypatch.setattr(lifecycle_module, "_button_text_mask", lambda *_args: object())
    monkeypatch.setattr(
        lifecycle_module,
        "_template_distance",
        lambda _mask, template: distances[template_names[id(template)]],
    )
    frame = _frame(
        reader,
        ("double", "orange", (521, 498, 659, 558)),
        ("no_double", "blue", (795, 499, 933, 558)),
    )

    result = reader.read(frame)

    assert result.stage == "preplay"
    assert result.texts == ("double", "no_double")


def test_without_confirmed_double_the_blue_button_keeps_general_competition(monkeypatch) -> None:
    """未读到当前行加倍时，蓝钮不得被上下文规则强制映射为不加倍。"""
    reader = LifecycleButtonReader()
    template_names = {id(template): name for name, template in reader._templates.items()}
    distances = {
        "no_call": 0.1923,
        "no_rob": 0.30,
        "no_double": 0.1729,
        "pass": 0.30,
        "hint": 0.30,
        "cannot_beat": 0.30,
        "in_play_reveal": 0.30,
    }
    monkeypatch.setattr(lifecycle_module, "_button_text_mask", lambda *_args: object())
    monkeypatch.setattr(
        lifecycle_module,
        "_template_distance",
        lambda _mask, template: distances[template_names[id(template)]],
    )

    button = reader._classify(
        np.zeros((819, 1455, 3), dtype=np.uint8),
        ButtonBox(795, 499, 933, 558, "blue"),
    )

    assert button is None


def test_landlord_opening_reveal_option_is_still_playing() -> None:
    """正式出牌首回合的明牌按钮不能被识别为局前明牌。"""
    reader = LifecycleButtonReader()
    result = reader.read(
        _frame(
            reader,
            ("play", "orange", (436, 493, 575, 554)),
            ("in_play_reveal", "blue", (710, 494, 849, 554)),
            ("hint", "blue", (878, 495, 1022, 555)),
        )
    )

    assert result.stage == "playing"
    assert result.texts == ("play", "in_play_reveal", "hint")
