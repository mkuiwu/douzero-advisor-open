"""Fail-closed automation for the visible pre-play buttons."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Literal


PreplayAction = Literal[
    "call",
    "no_call",
    "rob",
    "no_rob",
    "double",
    "super_double",
    "no_double",
]
_PREPLAY_ACTIONS = frozenset(
    {
        "call",
        "no_call",
        "rob",
        "no_rob",
        "double",
        "super_double",
        "no_double",
    }
)
_NON_CLICK_ACTIONS = frozenset({"no_call", "no_rob", "no_double"})


@dataclass(frozen=True, slots=True)
class PreplayButtonOutcome:
    status: Literal["clicked", "stopped"]
    action: str
    reason: str


class PreplayButtonExecutor:
    """只在新鲜且连续稳定的证据下点击模型推荐的局前按钮。

    缺少目标按钮时不会用邻近坐标猜测；只等待目标标签，超时后交给人工接管。
    唯一的显式回退是 ``super_double``：如果未显示超级加倍按钮，则点击同组稳定的
    ``double``。``no_call``、``no_rob`` 和 ``no_double`` 永远只等待，不点击任何按钮。
    点击后必须在 ``post_click_timeout_seconds`` 内看到目标消失或离开局前阶段。
    """

    # 识别到按钮只是候选证据；执行器还要等待连续稳定帧并在点击后观察离开。

    def __init__(
        self,
        *,
        click_client: Callable[[tuple[int, int]], object],
        required_confirmations: int = 3,
        timeout_seconds: float = 15.0,
        post_click_timeout_seconds: float = 1.5,
        minimum_margin: float = 0.02,
        maximum_distance: float = 0.34,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if required_confirmations < 1:
            raise ValueError("required_confirmations must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if post_click_timeout_seconds <= 0:
            raise ValueError("post_click_timeout_seconds must be positive")
        if minimum_margin < 0:
            raise ValueError("minimum_margin must be non-negative")
        if maximum_distance <= 0:
            raise ValueError("maximum_distance must be positive")
        self._click_client = click_client
        self._required_confirmations = int(required_confirmations)
        self._timeout_seconds = float(timeout_seconds)
        self._post_click_timeout_seconds = float(post_click_timeout_seconds)
        self._minimum_margin = float(minimum_margin)
        self._maximum_distance = float(maximum_distance)
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._pending_action: str | None = None
        self._pending_since: float | None = None
        self._candidate_key: tuple[object, ...] | None = None
        self._candidate_count = 0
        self._clicked_action: str | None = None
        self._clicked_at: float | None = None

    def observe(self, lifecycle_read, advice=None) -> PreplayButtonOutcome | None:
        """Consume one pre-play read and optionally a newly scored advice."""
        now = self._clock()
        texts = tuple(getattr(lifecycle_read, "texts", ()))
        lifecycle_stage = getattr(lifecycle_read, "stage", None)

        if self._clicked_action is not None:
            if self._clicked_action not in texts or lifecycle_stage == "playing":
                outcome = PreplayButtonOutcome(
                    "clicked", self._clicked_action, "transition_confirmed"
                )
                self.reset()
                return outcome
            if self._clicked_at is not None and now - self._clicked_at >= self._post_click_timeout_seconds:
                outcome = PreplayButtonOutcome(
                    "stopped", self._clicked_action, "post_click_not_confirmed"
                )
                self.reset()
                return outcome
            return None

        action = getattr(advice, "action", None) if advice is not None else None
        if action in _PREPLAY_ACTIONS:
            if action != self._pending_action:
                self._pending_action = action
                self._pending_since = now
                self._candidate_key = None
                self._candidate_count = 0

        if self._pending_action is None:
            return None

        if lifecycle_stage == "playing":
            outcome = PreplayButtonOutcome(
                "stopped", self._pending_action, "preplay_stage_changed"
            )
            self.reset()
            return outcome

        family = _preplay_family(texts)
        if family is not None and family != _preplay_family((self._pending_action,)):
            outcome = PreplayButtonOutcome(
                "stopped", self._pending_action, "preplay_stage_changed"
            )
            self.reset()
            return outcome

        if self._pending_since is not None and now - self._pending_since >= self._timeout_seconds:
            reason = (
                "no_double_wait_timeout"
                if self._pending_action == "no_double"
                else "target_button_timeout"
            )
            outcome = PreplayButtonOutcome(
                "stopped", self._pending_action, reason
            )
            self.reset()
            return outcome

        # 负向建议不产生点击；保持观察以便阶段变化和倒计时自然收口。
        if self._pending_action in _NON_CLICK_ACTIONS:
            return None

        effective_action = self._pending_action
        fallback_from_super_double = (
            self._pending_action == "super_double"
            and "super_double" not in texts
            and "double" in texts
        )
        if fallback_from_super_double:
            effective_action = "double"

        button = next(
            (button for button in getattr(lifecycle_read, "buttons", ()) if button.text == effective_action),
            None,
        )
        if button is None:
            self._candidate_key = None
            self._candidate_count = 0
            return None
        distance = float(getattr(button, "distance", 1.0))
        # Match LifecycleButtonReader's narrow live-frame allowance for the
        # wide `super_double` glyph.  Do not relax the other labels.
        maximum_distance = (
            max(self._maximum_distance, 0.38)
            if effective_action == "super_double"
            else self._maximum_distance
        )
        minimum_margin = (
            max(self._minimum_margin, 0.025)
            if effective_action == "super_double"
            else self._minimum_margin
        )
        if distance > maximum_distance or float(getattr(button, "margin", 0.0)) < minimum_margin:
            self._candidate_key = None
            self._candidate_count = 0
            return None

        box = button.box
        key = (
            button.text,
            box.left,
            box.top,
            box.right,
            box.bottom,
            box.palette,
        )
        if key == self._candidate_key:
            self._candidate_count += 1
        else:
            self._candidate_key = key
            self._candidate_count = 1
        if self._candidate_count < self._required_confirmations:
            return None

        try:
            self._click_client(box.center)
        except Exception as error:
            outcome = PreplayButtonOutcome(
                "stopped", self._pending_action, f"click_failed:{type(error).__name__}:{error}"
            )
            self.reset()
            return outcome
        self._clicked_action = effective_action
        self._clicked_at = now
        self._pending_action = None
        self._pending_since = None
        self._candidate_key = None
        self._candidate_count = 0
        reason = (
            "button_invoked_fallback_super_double_to_double"
            if fallback_from_super_double
            else "button_invoked"
        )
        return PreplayButtonOutcome("clicked", self._clicked_action, reason)


def _preplay_family(texts: tuple[str, ...]) -> str | None:
    if any(text in {"call", "no_call"} for text in texts):
        return "call"
    if any(text in {"rob", "no_rob"} for text in texts):
        return "rob"
    if any(text in {"double", "super_double", "no_double"} for text in texts):
        return "double"
    return None
