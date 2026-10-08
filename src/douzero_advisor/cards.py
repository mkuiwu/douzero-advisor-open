"""斗地主牌面在 UI、识别器与 DouZero 环境之间的唯一编码约定。

项目内部始终使用 ``RANK_TO_ENV`` 的整数值参与合法性校验和模型输入；界面、
日志以及 OCR 则使用短牌面字符。不要在业务模块中自行定义 ``2`` 或两张王的数值，
否则容易造成合法动作和牌数统计使用两套规则。
"""

from enum import Enum


class Rank(str, Enum):
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"
    ACE = "A"
    TWO = "2"
    SMALL_JOKER = "X"
    BIG_JOKER = "D"


# DouZero 将 2 和两张王放在普通 A 之后的稀疏值，保留该约定可直接复用上游模型。
RANK_TO_ENV = {str(value): value for value in range(3, 10)} | {
    "10": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
    "2": 17, "X": 20, "D": 30,
}
ENV_TO_RANK = {value: key for key, value in RANK_TO_ENV.items()}
# 此元组同时是记牌器的容量真值；顺序不代表出牌大小，只便于稳定序列化。
FULL_DECK = tuple([value for value in range(3, 15) for _ in range(4)] + [17] * 4 + [20, 30])


def to_env_cards(cards: list[str]) -> list[int]:
    """把可见牌面转换为模型和状态机使用的环境编码。"""
    return [RANK_TO_ENV[card] for card in cards]


def from_env_cards(cards: list[int]) -> list[str]:
    """把环境编码转换为 UI/JSON 中展示的短牌面。"""
    return [ENV_TO_RANK[card] for card in cards]
