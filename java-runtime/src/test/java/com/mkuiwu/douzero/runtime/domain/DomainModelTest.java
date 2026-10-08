package com.mkuiwu.douzero.runtime.domain;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DomainModelTest {
    /** 验证领域牌点只暴露项目语义符号，不泄漏模型整数编码。 */
    @Test
    void cardRankUsesOnlyProjectSemanticSymbol() {
        assertEquals(CardRank.BIG_JOKER, CardRank.fromSymbol("D"));
    }

    /** 验证玩家角色完全由语义座位推导，地主与上下家关系保持一致。 */
    @Test
    void playerDerivesRoleFromSeat() {
        assertEquals(Role.LANDLORD, new Player(Seat.LANDLORD).role());
        assertEquals(Role.FARMER, new Player(Seat.LANDLORD_DOWN).role());
    }

    /** 验证出牌动作会防御性复制牌集，并拒绝用空牌伪装出牌。 */
    @Test
    void playRecordCopiesCardsAndValidatesPass() {
        CardSet cards = new CardSet(List.of(new Card(CardRank.THREE)));
        assertThrows(IllegalArgumentException.class,
                () -> new PlayAction.Play(CardSet.empty()));
        PlayRecord record = new PlayRecord(
                new Player(Seat.LANDLORD_DOWN),
                new PlayAction.Play(cards));
        assertEquals(cards, ((PlayAction.Play) record.action()).cards());
    }

    /** 验证手牌差按多重集计数，重复点数的牌只扣除实际出现的数量。 */
    @Test
    void cardSetDifferenceUsesMultisetCountsForRepeatedRanks() {
        CardSet previous = new CardSet(List.of(
                new Card(CardRank.THREE),
                new Card(CardRank.THREE),
                new Card(CardRank.FOUR)
        ));
        CardSet current = new CardSet(List.of(
                new Card(CardRank.THREE),
                new Card(CardRank.FOUR)
        ));

        assertEquals(new CardSet(List.of(new Card(CardRank.THREE))),
                previous.removedTo(current));
        assertThrows(IllegalArgumentException.class,
                () -> current.removedTo(previous));
    }

    /** 验证当前行动方和牌墩状态只从有序历史派生，不依赖额外可漂移字段。 */
    @Test
    void gameSnapshotDerivesTurnAndTrickStateFromHistoryOnly() {
        CardSet lead = new CardSet(List.of(new Card(CardRank.THREE)));
        List<PlayRecord> clearedTrick = List.of(
                new PlayRecord(new Player(Seat.LANDLORD), new PlayAction.Play(lead)),
                new PlayRecord(new Player(Seat.LANDLORD_DOWN), new PlayAction.Pass()),
                new PlayRecord(new Player(Seat.LANDLORD_UP), new PlayAction.Pass())
        );
        GameSnapshot snapshot = new GameSnapshot(
                "deal-derived",
                GamePhase.PLAYING,
                Seat.LANDLORD,
                lead,
                CardSet.empty(),
                clearedTrick
        );

        assertEquals(Seat.LANDLORD, snapshot.currentSeat());
        assertTrue(snapshot.lastMove().isEmpty());
        assertEquals(List.of("dealId", "phase", "localSeat", "hand", "bottomCards", "history"),
                java.util.Arrays.stream(GameSnapshot.class.getRecordComponents())
                        .map(component -> component.getName())
                        .toList());
        assertThrows(IllegalArgumentException.class, () -> new GameSnapshot(
                "deal-invalid-pass",
                GamePhase.PLAYING,
                Seat.LANDLORD,
                lead,
                CardSet.empty(),
                List.of(new PlayRecord(new Player(Seat.LANDLORD), new PlayAction.Pass()))
        ));
    }
}
