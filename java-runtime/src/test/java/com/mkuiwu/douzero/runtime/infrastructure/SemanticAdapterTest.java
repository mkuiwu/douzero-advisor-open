package com.mkuiwu.douzero.runtime.infrastructure;

import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;

class SemanticAdapterTest {
    private final DouZeroCardCodec codec = new DouZeroCardCodec();

    /** 验证 DouZero 稀疏牌面编码只由基础设施适配器完整维护。 */
    @Test
    void douZeroCodecOwnsTheCompleteSparseMapping() {
        CardSet allRanks = new CardSet(Arrays.stream(CardRank.values()).map(Card::new).toList());

        assertEquals(
                List.of(3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 20, 30),
                codec.encode(allRanks));
        assertEquals(allRanks, codec.decode(codec.encode(allRanks)));
        assertThrows(IllegalArgumentException.class, () -> codec.decode(List.of(99)));
    }

    /** 验证模型线协议中的空数组在边界处转换为显式 Pass，不进入应用语义层。 */
    @Test
    void passEncodingCannotLeakPastTheAdapter() {
        assertEquals(List.of(), codec.encodeAction(new PlayAction.Pass()));
        assertInstanceOf(PlayAction.Pass.class, codec.decodeAction(List.of()));
    }

}
