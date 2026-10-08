package com.mkuiwu.douzero.runtime.domain;

import java.util.Objects;

/**
 * 一张牌的不可变领域值对象；重复牌通过多个 Card 实例表达。
 *
 * @param rank 牌面点数，不包含花色；斗地主的合法点数由 {@link CardRank} 限定
 */
public record Card(CardRank rank) {
    public Card {
        Objects.requireNonNull(rank, "牌面不能为空");
    }

    @Override
    public String toString() {
        return rank.symbol();
    }
}
