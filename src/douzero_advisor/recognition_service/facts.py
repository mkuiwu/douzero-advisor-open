"""一张捕获帧的懒加载事实视图。"""

from __future__ import annotations

from functools import cached_property
from typing import Any


class FrameFacts:
    """同帧所有任务共享的 Reader 缓存。

    ``generation`` 是 Python capture 会话代数，只用于清空稳定候选，不上送
    Java；``observed_at`` 是该帧被 Worker 消费的 UTC 业务时间。
    """

    def __init__(
        self,
        frame: Any,
        *,
        generation: int,
        observed_at: str,
        observation_reader: Any,
        lifecycle_reader: Any,
        settlement_reader: Any,
    ) -> None:
        self.frame = frame
        self.generation = generation
        self.observed_at = observed_at
        self._pixels = getattr(frame, "pixels", frame)
        self._observation_reader = observation_reader
        self._lifecycle_reader = lifecycle_reader
        self._settlement_reader = settlement_reader

    @cached_property
    def lifecycle(self) -> Any:
        return self._lifecycle_reader.read(self._pixels)

    @cached_property
    def settlement(self) -> Any:
        return self._settlement_reader.read(self._pixels)

    @cached_property
    def hand_result(self) -> Any:
        return self._observation_reader.read_my_hand_with_diagnostics(self._pixels)

    @cached_property
    def bottom_result(self) -> Any:
        return self._observation_reader.read_bottom_cards_with_diagnostics(self._pixels)

    @cached_property
    def turn_ready(self) -> bool | None:
        return self._observation_reader.is_my_turn_ready(self._pixels)

    @cached_property
    def action_buttons_present(self) -> bool | None:
        """本方出牌按钮是否在画面上。

        生产路径读 ``local_action_buttons_present``；测试假对象若没有该方法，
        退回 ``turn_ready``，保持旧单测帧数据可用。
        """
        probe = getattr(self._observation_reader, "local_action_buttons_present", None)
        if callable(probe):
            present = probe(self._pixels)
            if present is None:
                return None
            return bool(present)
        return self.turn_ready

    @cached_property
    def self_turn(self) -> Any:
        return self._observation_reader.read_self_turn_snapshot(self._pixels)

    @property
    def hand(self) -> tuple[str, ...] | None:
        value = getattr(self.hand_result, "value", None)
        cards = getattr(value, "cards", None)
        return tuple(cards) if cards else None

    @property
    def bottom_cards(self) -> tuple[str, ...] | None:
        value = getattr(self.bottom_result, "value", None)
        cards = getattr(value, "cards", None)
        return tuple(cards) if cards else None
