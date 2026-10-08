package com.mkuiwu.douzero.runtime.domain;

import java.util.Objects;

/** 明确表达一次出牌或不出，避免使用空牌集合承载“不出”的隐含语义。 */
public sealed interface PlayAction permits PlayAction.Pass, PlayAction.Play {
    /** 不出动作；领域层不关心外部模型如何编码该动作。 */
    record Pass() implements PlayAction {
    }

    /**
     * 实际出牌动作。
     *
     * @param cards 本次打出的非空牌集合
     */
    record Play(CardSet cards) implements PlayAction {
        public Play {
            Objects.requireNonNull(cards, "出牌集合不能为空");
            if (cards.isEmpty()) {
                throw new IllegalArgumentException("实际出牌动作不能使用空牌集合");
            }
        }
    }
}
