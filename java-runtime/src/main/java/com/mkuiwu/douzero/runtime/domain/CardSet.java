package com.mkuiwu.douzero.runtime.domain;

import java.util.EnumMap;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 有序牌集合；用于表达手牌、底牌或一次出牌，不在这里推导牌型。
 *
 * @param cards 按上游识别或业务约定顺序保存的牌；构造时复制，允许为空但不允许为 null
 */
public record CardSet(List<Card> cards) {
    public CardSet {
        Objects.requireNonNull(cards, "牌集合不能为空");
        cards = List.copyOf(cards);
    }

    public static CardSet empty() {
        return new CardSet(List.of());
    }

    public int size() {
        return cards.size();
    }

    public boolean isEmpty() {
        return cards.isEmpty();
    }

    /**
     * 计算本牌集变成指定剩余牌集时移除的牌，重复点数按数量逐张扣减。
     *
     * @param remaining 变化后的剩余牌；必须是本牌集的多重子集
     * @return 按本牌集原顺序排列的移除牌
     */
    public CardSet removedTo(CardSet remaining) {
        Objects.requireNonNull(remaining, "剩余牌集合不能为空");
        Map<CardRank, Integer> remainingCounts = new EnumMap<>(CardRank.class);
        for (Card card : remaining.cards()) {
            remainingCounts.merge(card.rank(), 1, Integer::sum);
        }

        List<Card> removed = new ArrayList<>();
        for (Card card : cards) {
            int count = remainingCounts.getOrDefault(card.rank(), 0);
            if (count == 0) {
                removed.add(card);
            } else {
                remainingCounts.put(card.rank(), count - 1);
            }
        }
        if (remainingCounts.values().stream().anyMatch(count -> count != 0)) {
            throw new IllegalArgumentException("当前手牌不能包含上次确认手牌之外的牌");
        }
        return new CardSet(removed);
    }
}
