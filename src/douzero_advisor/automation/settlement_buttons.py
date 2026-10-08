"""Fail-closed automation for the settlement-page Change Table control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import random
import time
from typing import Literal


@dataclass(frozen=True, slots=True)
class SettlementButtonOutcome:
    status: Literal["clicked", "stopped"]
    action: str
    reason: str


CHANGE_TABLE_POINT = (1116, 68)


class SettlementChangeTableExecutor:
    """Click Change Table only after the settlement page is fully stable.

    The lower ``明牌开始×5``/``继续游戏`` pair is settlement evidence only.
    The action target is the fixed top-bar ``换桌`` control, never the lower
    ``继续游戏`` button.
    """

    # 结算页的“继续游戏”与“换桌”是不同生命周期；这里只允许前者证实页面、
    # 后者作为唯一动作目标，避免把底部按钮误当成换桌点击。

    def __init__(
        self,
        *,
        click_client: Callable[[tuple[int, int]], object],
        required_confirmations: int = 3,
        timeout_seconds: float = 15.0,
        post_click_timeout_seconds: float = 2.0,
        minimum_confidence: float = 0.75,
        minimum_click_delay_seconds: float = 1.5,
        maximum_click_delay_seconds: float = 2.0,
        change_table_point: tuple[int, int] = CHANGE_TABLE_POINT,
        random_uniform: Callable[[float, float], float] = random.uniform,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if required_confirmations < 1:
            raise ValueError("required_confirmations must be positive")
        if timeout_seconds <= 0 or post_click_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be between zero and one")
        if minimum_click_delay_seconds < 0:
            raise ValueError("minimum_click_delay_seconds must be non-negative")
        if maximum_click_delay_seconds < minimum_click_delay_seconds:
            raise ValueError(
                "maximum_click_delay_seconds must be at least minimum_click_delay_seconds"
            )
        if (
            len(change_table_point) != 2
            or any(type(value) is not int or value < 0 for value in change_table_point)
        ):
            raise ValueError("change_table_point must contain two non-negative integers")
        self._click_client = click_client
        self._required_confirmations = int(required_confirmations)
        self._timeout_seconds = float(timeout_seconds)
        self._post_click_timeout_seconds = float(post_click_timeout_seconds)
        self._minimum_confidence = float(minimum_confidence)
        self._minimum_click_delay_seconds = float(minimum_click_delay_seconds)
        self._maximum_click_delay_seconds = float(maximum_click_delay_seconds)
        self._change_table_point = change_table_point
        self._random_uniform = random_uniform
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._pending_since: float | None = None
        self._candidate_key: tuple[object, ...] | None = None
        self._candidate_count = 0
        self._scheduled_click_at: float | None = None
        self._clicked_at: float | None = None

    def observe(self, settlement_read) -> SettlementButtonOutcome | None:
        now = self._clock()
        stage = getattr(settlement_read, "stage", None)
        buttons = tuple(getattr(settlement_read, "buttons", ()))
        by_name = {getattr(button, "name", None): button for button in buttons}
        settlement_target = by_name.get("continue_game")

        if self._clicked_at is not None:
            if stage != "settlement" or settlement_target is None:
                outcome = SettlementButtonOutcome(
                    "clicked", "change_table", "transition_confirmed"
                )
                self.reset()
                return outcome
            if now - self._clicked_at >= self._post_click_timeout_seconds:
                outcome = SettlementButtonOutcome(
                    "stopped", "change_table", "post_click_not_confirmed"
                )
                self.reset()
                return outcome
            return None

        if stage != "settlement":
            if self._pending_since is not None and stage in {"preplay", "playing"}:
                outcome = SettlementButtonOutcome(
                    "stopped", "change_table", "settlement_stage_changed"
                )
                self.reset()
                return outcome
            return None

        if self._pending_since is None:
            self._pending_since = now
        if now - self._pending_since >= self._timeout_seconds:
            outcome = SettlementButtonOutcome(
                "stopped", "change_table", "settlement_page_timeout"
            )
            self.reset()
            return outcome

        # Require the complete pair.  A lone button during an animation is
        # not enough evidence to send a mouse click to the next deal.
        reveal = by_name.get("reveal_start")
        if reveal is None or settlement_target is None:
            self._candidate_key = None
            self._candidate_count = 0
            self._scheduled_click_at = None
            return None
        if (
            float(getattr(reveal, "confidence", 0.0)) < self._minimum_confidence
            or float(getattr(settlement_target, "confidence", 0.0)) < self._minimum_confidence
        ):
            self._candidate_key = None
            self._candidate_count = 0
            self._scheduled_click_at = None
            return None

        key = (
            tuple(
                (
                    getattr(button, "name", None),
                    button.box.left,
                    button.box.top,
                    button.box.right,
                    button.box.bottom,
                )
                for button in (reveal, settlement_target)
            ),
        )
        if key == self._candidate_key:
            self._candidate_count += 1
        else:
            self._candidate_key = key
            self._candidate_count = 1
            self._scheduled_click_at = None
        if self._candidate_count < self._required_confirmations:
            return None

        if self._scheduled_click_at is None:
            delay = float(
                self._random_uniform(
                    self._minimum_click_delay_seconds,
                    self._maximum_click_delay_seconds,
                )
            )
            self._scheduled_click_at = now + delay
            return None
        if now < self._scheduled_click_at:
            return None

        try:
            self._click_client(self._change_table_point)
        except Exception as error:
            outcome = SettlementButtonOutcome(
                "stopped",
                "change_table",
                f"click_failed:{type(error).__name__}:{error}",
            )
            self.reset()
            return outcome
        self._clicked_at = now
        self._candidate_key = None
        self._candidate_count = 0
        self._scheduled_click_at = None
        return SettlementButtonOutcome("clicked", "change_table", "button_invoked")


# Compatibility for callers that imported the old class.  Its behavior is
# intentionally updated: it now changes table rather than clicking Continue.
SettlementContinueExecutor = SettlementChangeTableExecutor
