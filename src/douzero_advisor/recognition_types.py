"""CV 与历史恢复共享的轻量语义类型；导入本模块不会加载状态机或模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActionResultState(str, Enum):
    """固定动作结果区的视觉状态，不包含牌局轮次或历史语义。"""

    WAIT = "wait"  # 尚未出现可消费的出牌或不出证据。
    CLEAR = "clear"  # 仅供旧 shadow 链表示结果区已清空。
    PASS = "pass"  # 结果区稳定显示“不出”。
    PLAY = "play"  # 结果区稳定显示已出牌。
    AMBIGUOUS = "ambiguous"  # 同帧证据冲突，不能形成动作事实。
    UNREADABLE = "unreadable"  # 当前画面无法可靠判定结果区状态。


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    """同一牌局动作恢复使用的不可变视觉检查点，不持有 tracker 或模型对象。"""

    generation: int  # Capture 会话代际；切换窗口或捕获恢复后递增。
    sequence: int  # 当前代际内从 1 开始递增的稳定观察序号。
    evidence_hashes: tuple[str, ...]  # 已消费视觉证据的唯一摘要集合。
    my_hand: tuple[int, ...]  # 本方剩余手牌的 DouZero 环境整数，仅供旧恢复链使用。
    tracker_counts: tuple[int, ...]  # D 到 3 共十五个公开记牌器计数。

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if any(not isinstance(value, str) or not value for value in self.evidence_hashes):
            raise ValueError("evidence_hashes must contain non-empty strings")
        if len(set(self.evidence_hashes)) != len(self.evidence_hashes):
            raise ValueError("evidence_hashes must be unique")
        if any(type(card) is not int for card in self.my_hand):
            raise ValueError("my_hand must contain integer environment cards")
        capacities = (1, 1) + (4,) * 13
        if len(self.tracker_counts) != len(capacities) or any(
            type(value) is not int or not 0 <= value <= capacity
            for value, capacity in zip(self.tracker_counts, capacities, strict=True)
        ):
            raise ValueError("tracker_counts must contain 15 canonical counts")
