package com.mkuiwu.douzero.runtime.infrastructure.model.douzero;

import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;

import java.util.List;
import java.util.Objects;

/** DouZero 稀疏整数编码与项目牌面语义之间的唯一 Java 映射点。 */
public final class DouZeroCardCodec {
    /** 把领域牌集合编码为 DouZero 模型使用的整数列表。 */
    public List<Integer> encode(CardSet cardSet) {
        return cardSet.cards().stream().map(card -> encodeRank(card.rank())).toList();
    }

    /** 把 DouZero 整数列表解码为领域牌集合，未知编码直接拒绝。 */
    public CardSet decode(List<Integer> codes) {
        if (codes == null) {
            throw new IllegalArgumentException("DouZero 牌面编码不能为空");
        }
        return new CardSet(codes.stream().map(code -> new Card(decodeRank(code))).toList());
    }

    /** 把领域动作编码为模型动作；只有在该边界内空列表才表示不出。 */
    public List<Integer> encodeAction(PlayAction action) {
        Objects.requireNonNull(action, "领域动作不能为空");
        if (action instanceof PlayAction.Pass) {
            return List.of();
        }
        return encode(((PlayAction.Play) action).cards());
    }

    /** 把模型动作立即还原为显式领域动作，避免空列表进入内部状态机。 */
    public PlayAction decodeAction(List<Integer> codes) {
        CardSet cards = decode(codes);
        return cards.isEmpty() ? new PlayAction.Pass() : new PlayAction.Play(cards);
    }

    private int encodeRank(CardRank rank) {
        return switch (rank) {
            case THREE -> 3;
            case FOUR -> 4;
            case FIVE -> 5;
            case SIX -> 6;
            case SEVEN -> 7;
            case EIGHT -> 8;
            case NINE -> 9;
            case TEN -> 10;
            case JACK -> 11;
            case QUEEN -> 12;
            case KING -> 13;
            case ACE -> 14;
            case TWO -> 17;
            case SMALL_JOKER -> 20;
            case BIG_JOKER -> 30;
        };
    }

    private CardRank decodeRank(int code) {
        return switch (code) {
            case 3 -> CardRank.THREE;
            case 4 -> CardRank.FOUR;
            case 5 -> CardRank.FIVE;
            case 6 -> CardRank.SIX;
            case 7 -> CardRank.SEVEN;
            case 8 -> CardRank.EIGHT;
            case 9 -> CardRank.NINE;
            case 10 -> CardRank.TEN;
            case 11 -> CardRank.JACK;
            case 12 -> CardRank.QUEEN;
            case 13 -> CardRank.KING;
            case 14 -> CardRank.ACE;
            case 17 -> CardRank.TWO;
            case 20 -> CardRank.SMALL_JOKER;
            case 30 -> CardRank.BIG_JOKER;
            default -> throw new IllegalArgumentException("未知 DouZero 牌面编码: " + code);
        };
    }
}
