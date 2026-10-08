package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.ExecutionFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.Seat;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * {@link CardPlayExecutionPort} DTO 不变量测试（单一 execute 接口）。
 *
 * <p>验证执行请求的边界校验：子集关系、必填字段、截止时间正数、
 * PLAY/PASS 与 recommendedCards 的匹配约束。</p>
 */
class CardPlayExecutionPortTest {

    private static final GameTaskIdentity IDENTITY =
            new GameTaskIdentity("req-001", "deal-001", 3);
    private static final CardSet HAND = new CardSet(List.of(
            new Card(CardRank.THREE),
            new Card(CardRank.THREE),
            new Card(CardRank.FIVE),
            new Card(CardRank.SMALL_JOKER),
            new Card(CardRank.BIG_JOKER)
    ));

    @Test
    void playRequestAcceptsValidSubset() {
        // 场景：PLAY 请求的建议牌是权威手牌的有效子集。预期：构造成功。
        CardSet recommended = new CardSet(List.of(new Card(CardRank.FIVE)));
        assertDoesNotThrow(() -> new CardPlayExecutionPort.ExecutionRequest(
                IDENTITY, Seat.LANDLORD, HAND, recommended,
                CardPlayExecutionPort.ActionType.PLAY, true, 10000));
    }

    @Test
    void playRequestRejectsEmptyRecommended() {
        // 场景：PLAY 请求的建议牌为空。预期：抛出 IllegalArgumentException。
        CardSet recommended = CardSet.empty();
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRequest(
                        IDENTITY, Seat.LANDLORD, HAND, recommended,
                        CardPlayExecutionPort.ActionType.PLAY, true, 10000));
    }

    @Test
    void playRequestRejectsNonSubset() {
        // 场景：PLAY 请求的建议牌包含权威手牌以外的牌。预期：拒绝越权选牌。
        CardSet recommended = new CardSet(List.of(new Card(CardRank.NINE)));
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRequest(
                        IDENTITY, Seat.LANDLORD, HAND, recommended,
                        CardPlayExecutionPort.ActionType.PLAY, true, 10000));
    }

    @Test
    void passRequestRejectsNonEmptyRecommended() {
        // 场景：PASS 请求携带非空建议牌。预期：抛出 IllegalArgumentException。
        CardSet recommended = new CardSet(List.of(new Card(CardRank.FIVE)));
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRequest(
                        IDENTITY, Seat.LANDLORD, HAND, recommended,
                        CardPlayExecutionPort.ActionType.PASS, true, 10000));
    }

    @Test
    void passRequestAcceptsEmptyRecommended() {
        // 场景：PASS 请求的建议牌为空。预期：构造成功。
        assertDoesNotThrow(() -> new CardPlayExecutionPort.ExecutionRequest(
                IDENTITY, Seat.LANDLORD, HAND, CardSet.empty(),
                CardPlayExecutionPort.ActionType.PASS, false, 10000));
    }

    @Test
    void requestRejectsEmptyHand() {
        // 场景：权威手牌为空。预期：抛出 IllegalArgumentException。
        CardSet recommended = new CardSet(List.of(new Card(CardRank.FIVE)));
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRequest(
                        IDENTITY, Seat.LANDLORD, CardSet.empty(), recommended,
                        CardPlayExecutionPort.ActionType.PLAY, true, 10000));
    }

    @Test
    void requestRejectsNonPositiveDeadline() {
        // 场景：截止时间非正数。预期：抛出 IllegalArgumentException。
        CardSet recommended = new CardSet(List.of(new Card(CardRank.FIVE)));
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRequest(
                        IDENTITY, Seat.LANDLORD, HAND, recommended,
                        CardPlayExecutionPort.ActionType.PLAY, true, 0));
    }

    @Test
    void executedRequiresInstant() {
        // 场景：执行确认结果的 verifiedAt 为空。预期：抛出 NullPointerException。
        assertThrows(NullPointerException.class,
                () -> new CardPlayExecutionPort.Executed(IDENTITY, true, null));
    }

    @Test
    void executedEchoesAutoSubmit() {
        // 场景：构造执行确认结果。预期：autoSubmitEcho 与构造时一致。
        Instant now = Instant.now();
        var executed = new CardPlayExecutionPort.Executed(IDENTITY, false, now);
        assertEquals(IDENTITY, executed.identity());
        assertEquals(false, executed.autoSubmitEcho());
        assertEquals(now, executed.verifiedAt());
    }

    @Test
    void rejectedRequiresNonBlankDetail() {
        // 场景：执行拒绝结果的 detail 为空。预期：抛出 IllegalArgumentException。
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionRejected(
                        IDENTITY, ExecutionFailure.BUTTON_NOT_FOUND, "   "));
    }

    @Test
    void uncertainRequiresNonBlankDetail() {
        // 场景：执行不确定结果的 detail 为空。预期：fail-closed 必须有诊断说明。
        assertThrows(IllegalArgumentException.class,
                () -> new CardPlayExecutionPort.ExecutionUncertain(IDENTITY, " "));
    }

    @Test
    void resultsCarryIdentity() {
        // 场景：构造各类结果并查询 identity。预期：identity 与构造时一致。
        Instant now = Instant.now();
        var executed = new CardPlayExecutionPort.Executed(IDENTITY, true, now);
        var rejected = new CardPlayExecutionPort.ExecutionRejected(
                IDENTITY, ExecutionFailure.HAND_MISMATCH, "hand differs");
        var uncertain = new CardPlayExecutionPort.ExecutionUncertain(IDENTITY, "no change");

        assertEquals(IDENTITY, executed.identity());
        assertEquals(IDENTITY, rejected.identity());
        assertEquals(IDENTITY, uncertain.identity());
    }
}
