package com.mkuiwu.douzero.runtime.domain;

import java.util.Arrays;

/** 斗地主牌面点数；该领域类型不感知任何模型或识别实现的编码。 */
public enum CardRank {
    /** 点数 3。 */
    THREE("3"),
    /** 点数 4。 */
    FOUR("4"),
    /** 点数 5。 */
    FIVE("5"),
    /** 点数 6。 */
    SIX("6"),
    /** 点数 7。 */
    SEVEN("7"),
    /** 点数 8。 */
    EIGHT("8"),
    /** 点数 9。 */
    NINE("9"),
    /** 点数 10。 */
    TEN("10"),
    /** 点数 J。 */
    JACK("J"),
    /** 点数 Q。 */
    QUEEN("Q"),
    /** 点数 K。 */
    KING("K"),
    /** 点数 A。 */
    ACE("A"),
    /** 点数 2。 */
    TWO("2"),
    /** 小王。 */
    SMALL_JOKER("X"),
    /** 大王。 */
    BIG_JOKER("D");

    /** 领域日志、界面和识别适配器共同使用的稳定牌面符号。 */
    private final String symbol;

    CardRank(String symbol) {
        this.symbol = symbol;
    }

    public String symbol() {
        return symbol;
    }

    /** 按短牌面字符恢复牌面，未知字符直接拒绝。 */
    public static CardRank fromSymbol(String symbol) {
        return Arrays.stream(values())
                .filter(rank -> rank.symbol.equals(symbol))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("未知牌面: " + symbol));
    }
}
