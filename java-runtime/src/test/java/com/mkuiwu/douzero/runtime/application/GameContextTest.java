package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PlayRecord;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ProtocolAdapter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GameContextTest {
    /** 验证首轮直接以 Deal 建立的历史游标决定待识别座位，不重复读取地主首手。 */
    @Test
    void firstAdviceOnlyRequiresActionsMissingFromBootstrapHistory() {
        GameContext landlord = context(Seat.LANDLORD, 1);
        LocalTurnRecognitionPort.Request landlordRequest = landlord.prepareLocalTurn("turn-l", 1_500);
        assertEquals(Set.of(), landlordRequest.requiredActors());
        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                landlordRequest.turnEntryMode());
        landlord.reconcile(ready(landlordRequest, handFor(Seat.LANDLORD), Map.of()));
        assertEquals(0, landlord.snapshot().history().size());

        GameContext landlordDown = context(Seat.LANDLORD_DOWN, 2);
        LocalTurnRecognitionPort.Request downRequest = landlordDown.prepareLocalTurn("turn-d", 1_500);
        assertEquals(Set.of(), downRequest.requiredActors());
        landlordDown.reconcile(ready(downRequest, handFor(Seat.LANDLORD_DOWN), Map.of()));
        assertEquals(1, landlordDown.snapshot().history().size());

        GameContext landlordUp = context(Seat.LANDLORD_UP, 3);
        LocalTurnRecognitionPort.Request upRequest = landlordUp.prepareLocalTurn("turn-u", 1_500);
        assertEquals(Set.of(Seat.LANDLORD_DOWN), upRequest.requiredActors());
        landlordUp.reconcile(ready(upRequest, handFor(Seat.LANDLORD_UP), Map.of(
                Seat.LANDLORD_DOWN, new PlayAction.Pass()
        )));
        assertEquals(2, landlordUp.snapshot().history().size());
        assertEquals(Seat.LANDLORD_DOWN,
                landlordUp.snapshot().history().get(1).player().seat());
        assertInstanceOf(PlayAction.Pass.class,
                landlordUp.snapshot().history().get(1).action());
        assertEquals(cards(CardRank.EIGHT, CardRank.EIGHT),
                landlordUp.snapshot().lastMove());
    }

    /** 验证后续完整轮次按语义座位顺序追加本方及两名对手动作，不受物理读取顺序影响。 */
    @Test
    void laterSnapshotAddsLocalAndTwoOpponentsInExpectedSeatOrder() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 4);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("turn-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));

        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("turn-2", 1_500);
        assertEquals(Set.of(Seat.LANDLORD_DOWN, Seat.LANDLORD_UP), second.requiredActors());
        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                second.turnEntryMode());
        CardSet currentHand = withoutOne(initialHand, CardRank.THREE);
        Map<Seat, PlayAction> reversePhysicalOrder = new LinkedHashMap<>();
        reversePhysicalOrder.put(Seat.LANDLORD_UP, play(CardRank.FOUR));
        reversePhysicalOrder.put(Seat.LANDLORD_DOWN, new PlayAction.Pass());

        context.reconcile(ready(second, currentHand, reversePhysicalOrder));

        List<PlayRecord> history = context.snapshot().history();
        assertEquals(3, history.size());
        assertEquals(List.of(Seat.LANDLORD, Seat.LANDLORD_DOWN, Seat.LANDLORD_UP),
                history.stream().map(record -> record.player().seat()).toList());
        assertInstanceOf(PlayAction.Play.class, history.get(0).action());
        assertInstanceOf(PlayAction.Pass.class, history.get(1).action());
        assertEquals(Seat.LANDLORD, context.snapshot().currentSeat());
    }

    /** 验证首次为地主上家生成建议时，只读取尚未入史的地主下家动作。 */
    @Test
    void firstLandlordUpAdviceDoesNotRequireAlreadyRecordedLandlordOpening() {
        GameContext context = context(Seat.LANDLORD_UP, 5);
        LocalTurnRecognitionPort.Request request = context.prepareLocalTurn("turn-stale-region", 1_500);
        assertEquals(Set.of(Seat.LANDLORD_DOWN), request.requiredActors());

        context.reconcile(ready(request, handFor(Seat.LANDLORD_UP), Map.of(
                Seat.LANDLORD_DOWN, play(CardRank.SIX)
        )));

        List<PlayRecord> history = context.snapshot().history();
        assertEquals(2, history.size());
        assertEquals(cards(CardRank.EIGHT, CardRank.EIGHT),
                ((PlayAction.Play) history.get(0).action()).cards());
        assertEquals(cards(CardRank.SIX), ((PlayAction.Play) history.get(1).action()).cards());

        ResNet2Request wire = new ResNet2ProtocolAdapter(new DouZeroCardCodec()).encode(
                new com.mkuiwu.douzero.runtime.application.model.AdviceQuery(
                        "bootstrap-history", "deal-5", 1_500, context.snapshot()));
        assertEquals(List.of(List.of(8, 8), List.of(6)), wire.actionHistory());
    }

    /** 验证同一 Ready 重复回调幂等，农民建局携带的地主首手不会重复入历史。 */
    @Test
    void duplicateReadyIsIdempotentAndDoesNotDuplicateLandlordOpening() {
        GameContext context = context(Seat.LANDLORD_DOWN, 6);
        LocalTurnRecognitionPort.Request request = context.prepareLocalTurn("turn-duplicate", 1_500);
        LocalTurnRecognitionPort.Ready ready = ready(
                request, handFor(Seat.LANDLORD_DOWN), firstRoundActions(Seat.LANDLORD_DOWN));

        assertTrue(context.reconcile(ready).applied());
        assertFalse(context.reconcile(ready).applied());

        assertEquals(1, context.snapshot().history().size());
        assertEquals(Seat.LANDLORD, context.snapshot().history().get(0).player().seat());
    }

    /** 验证旧 generation 的回合结果被拒绝且不会修改权威手牌或历史。 */
    @Test
    void staleGenerationIsRejectedWithoutMutation() {
        GameContext context = context(Seat.LANDLORD, 7);
        LocalTurnRecognitionPort.Request request = context.prepareLocalTurn("turn-stale", 1_500);
        LocalTurnRecognitionPort.Ready stale = new LocalTurnRecognitionPort.Ready(
                new GameTaskIdentity(request.identity().requestId(), request.identity().dealId(), 8),
                handFor(Seat.LANDLORD),
                Map.of(),
                OBSERVED_AT
        );

        GameContext.ReconciliationException error = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(stale));

        assertEquals(GameContext.FailureReason.STALE_IDENTITY, error.reason());
        assertEquals(0, context.snapshot().history().size());
    }

    /** 验证已被新请求替代的旧 requestId 结果被拒绝且该身份不能再次签发。 */
    @Test
    void supersededRequestIsRejectedWithoutMutation() {
        GameContext context = context(Seat.LANDLORD, 12);
        LocalTurnRecognitionPort.Request oldRequest = context.prepareLocalTurn("turn-old", 1_500);
        context.prepareLocalTurn("turn-current", 1_500);

        GameContext.ReconciliationException error = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(oldRequest, handFor(Seat.LANDLORD), Map.of())));

        assertEquals(GameContext.FailureReason.STALE_REQUEST, error.reason());
        assertEquals(0, context.snapshot().history().size());
        assertThrows(IllegalArgumentException.class,
                () -> context.prepareLocalTurn("turn-old", 1_500));
    }

    /** 验证失败请求的 requestId 不可重发，但同一在途结果修正完整后仍可重新对账。 */
    @Test
    void failedRequestIdCannotBeReissuedButItsActiveResultCanBeRetried() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 13);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("failed-first", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request failed = context.prepareLocalTurn("failed-next", 1_500);

        assertThrows(GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(
                        failed,
                        withoutOne(initialHand, CardRank.THREE),
                        Map.of(Seat.LANDLORD_DOWN, new PlayAction.Pass())
                )));
        assertThrows(IllegalArgumentException.class,
                () -> context.prepareLocalTurn("failed-next", 1_500));

        assertTrue(context.reconcile(ready(
                failed,
                withoutOne(initialHand, CardRank.THREE),
                opponentPasses()
        )).applied());
    }

    /** 验证无效截止时间不会占用 requestId，也不会替换当前仍有效的回合请求。 */
    @Test
    void invalidDeadlineDoesNotIssueRequestIdOrReplaceActiveRequest() {
        GameContext context = context(Seat.LANDLORD, 17);
        LocalTurnRecognitionPort.Request active = context.prepareLocalTurn("still-active", 1_500);

        assertThrows(IllegalArgumentException.class,
                () -> context.prepareLocalTurn("retry-deadline", 0));

        assertTrue(context.reconcile(ready(
                active, handFor(Seat.LANDLORD), Map.of())).applied());
        LocalTurnRecognitionPort.Request retried = context.prepareLocalTurn(
                "retry-deadline", 1_500);
        assertEquals("retry-deadline", retried.identity().requestId());
    }

    /** 验证缺少必需对手动作时整轮原子失败，不提交已经计算出的候选变更。 */
    @Test
    void missingRequiredOpponentFailsAfterCandidateWorkWithoutPartialCommit() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 8);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("turn-ok", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("turn-missing", 1_500);

        GameContext.ReconciliationException error = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(
                        second,
                        withoutOne(initialHand, CardRank.THREE),
                        Map.of(Seat.LANDLORD_DOWN, new PlayAction.Pass())
                )));

        assertEquals(GameContext.FailureReason.MISSING_REQUIRED_ACTION, error.reason());
        assertEquals(0, context.snapshot().history().size());
        assertEquals(initialHand, context.snapshot().hand());
    }

    /** 验证连续轮次可以分别打出同点数牌，多重集手牌差不会误判为重复回调。 */
    @Test
    void sameRankCanBeRemovedAcrossConsecutiveRounds() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 9);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("same-rank-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));

        CardSet afterFirstThree = withoutOne(initialHand, CardRank.THREE);
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("same-rank-2", 1_500);
        context.reconcile(ready(second, afterFirstThree, opponentPasses()));

        CardSet afterSecondThree = withoutOne(afterFirstThree, CardRank.THREE);
        LocalTurnRecognitionPort.Request third = context.prepareLocalTurn("same-rank-3", 1_500);
        context.reconcile(ready(third, afterSecondThree, opponentPasses()));

        assertEquals(cards(CardRank.THREE),
                ((PlayAction.Play) context.snapshot().history().get(0).action()).cards());
        assertEquals(cards(CardRank.THREE),
                ((PlayAction.Play) context.snapshot().history().get(3).action()).cards());
        assertEquals(6, context.snapshot().history().size());
        assertTrue(context.snapshot().lastMove().isEmpty());
    }

    /** 验证一次领出后连续两家不出会清空 lastMove，下一轮成为新的牌墩。 */
    @Test
    void twoTrailingPassesClearLastMoveForTheNewTrick() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 11);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("trick-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("trick-2", 1_500);

        context.reconcile(ready(
                second,
                withoutOne(initialHand, CardRank.THREE),
                opponentPasses()
        ));

        assertTrue(context.snapshot().lastMove().isEmpty());
    }

    /** 验证当前领出者在没有活动领牌时不能不出，防止产生非法历史。 */
    @Test
    void localLeaderCannotPassWithoutAnActiveLead() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 14);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("lead-pass-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("lead-pass-2", 1_500);

        GameContext.ReconciliationException error = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(second, initialHand, opponentPasses())));

        assertEquals(GameContext.FailureReason.PASS_WITHOUT_ACTIVE_LEAD, error.reason());
        assertEquals(0, context.snapshot().history().size());
    }

    /** 验证两次不出已结束牌墩后，后续对手不能继续提交无领牌的不出。 */
    @Test
    void opponentCannotKeepPassingAfterTwoPassesClearTheTrick() {
        CardSet initialHand = handFor(Seat.LANDLORD_UP);
        GameContext context = context(Seat.LANDLORD_UP, 15);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("opponent-pass-1", 1_500);
        context.reconcile(ready(first, initialHand, firstRoundActions(Seat.LANDLORD_UP)));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("opponent-pass-2", 1_500);

        GameContext.ReconciliationException error = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(
                        second,
                        initialHand,
                        Map.of(
                                Seat.LANDLORD, new PlayAction.Pass(),
                                Seat.LANDLORD_DOWN, play(CardRank.FOUR)
                        )
                )));

        assertEquals(GameContext.FailureReason.PASS_WITHOUT_ACTIVE_LEAD, error.reason());
        assertEquals(2, context.snapshot().history().size());
        assertEquals(initialHand, context.snapshot().hand());
    }

    /** 验证上下文关闭幂等，关闭后准备和对账都失败且不改变最终快照。 */
    @Test
    void closeIsIdempotentAndRejectsPrepareAndReconcileWithoutMutation() {
        GameContext context = context(Seat.LANDLORD, 16);
        LocalTurnRecognitionPort.Request request = context.prepareLocalTurn("close-turn", 1_500);
        CardSet hand = context.snapshot().hand();
        context.close();
        context.close();

        GameContext.ReconciliationException prepareError = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.prepareLocalTurn("after-close", 1_500));
        GameContext.ReconciliationException reconcileError = assertThrows(
                GameContext.ReconciliationException.class,
                () -> context.reconcile(ready(request, hand, Map.of())));

        assertEquals(GameContext.FailureReason.CONTEXT_CLOSED, prepareError.reason());
        assertEquals(GameContext.FailureReason.CONTEXT_CLOSED, reconcileError.reason());
        assertTrue(context.isClosed());
        assertEquals(GamePhase.PLAYING, context.snapshot().phase());
        assertEquals(0, context.snapshot().history().size());
        assertEquals(hand, context.snapshot().hand());
    }

    /** 验证无论本方是哪一座位，后续完整一轮都恰好追加三条动作并回到本方。 */
    @ParameterizedTest(name = "本方座位={0} 时完整后续轮次追加三条动作")
    @EnumSource(Seat.class)
    void laterCompleteRoundAddsExactlyThreeActionsForEveryLocalSeat(Seat localSeat) {
        CardSet initialHand = handFor(localSeat);
        GameContext context = context(localSeat, 20 + localSeat.ordinal());
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn(
                "role-first-" + localSeat, 1_500);
        context.reconcile(ready(first, initialHand, firstRoundActions(localSeat)));
        int before = context.snapshot().history().size();

        LocalTurnRecognitionPort.Request later = context.prepareLocalTurn(
                "role-later-" + localSeat, 1_500);
        CardSet currentHand = withoutOne(initialHand, CardRank.THREE);
        Map<Seat, PlayAction> actions = Map.of(
                localSeat.next(), new PlayAction.Pass(),
                localSeat.next().next(), new PlayAction.Pass()
        );
        context.reconcile(ready(later, currentHand, actions));

        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                later.turnEntryMode());
        assertEquals(3, context.snapshot().history().size() - before);
        assertEquals(localSeat, context.snapshot().currentSeat());
    }

    /** 验证后续完整轮次的同一 Ready 重复到达时不会重复追加三条动作。 */
    @Test
    void duplicateLaterCompleteRoundReadyIsIdempotent() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 30);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("round-idem-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("round-idem-2", 1_500);
        LocalTurnRecognitionPort.Ready ready = ready(
                second, withoutOne(initialHand, CardRank.THREE), opponentPasses());

        assertTrue(context.reconcile(ready).applied());
        assertFalse(context.reconcile(ready).applied());
        assertEquals(3, context.snapshot().history().size());
    }

    /** 验证跨越不同 UI 回合边沿时，即使语义快照相同也应视为两个合法轮次。 */
    @Test
    void identicalSemanticSnapshotCanBeConsumedAcrossDistinctTurnEdges() {
        CardSet hand = handFor(Seat.LANDLORD_UP);
        GameContext context = context(Seat.LANDLORD_UP, 31);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("same-view-1", 1_500);
        context.reconcile(ready(first, hand, Map.of(
                Seat.LANDLORD, play(CardRank.EIGHT, CardRank.EIGHT),
                Seat.LANDLORD_DOWN, play(CardRank.SIX))));

        Map<Seat, PlayAction> repeatedActions = Map.of(
                Seat.LANDLORD, play(CardRank.ACE),
                Seat.LANDLORD_DOWN, new PlayAction.Pass()
        );
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("same-view-2", 1_500);
        context.reconcile(ready(second, hand, repeatedActions));
        LocalTurnRecognitionPort.Request third = context.prepareLocalTurn("same-view-3", 1_500);
        context.reconcile(ready(third, hand, repeatedActions));

        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                second.turnEntryMode());
        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                third.turnEntryMode());
        assertEquals(8, context.snapshot().history().size());
    }

    /** 验证对账后的权威历史按原顺序和显式 Pass 精确编码给正式模型。 */
    @Test
    void reconciledHistoryIsEncodedExactlyForTheModel() {
        CardSet initialHand = handFor(Seat.LANDLORD);
        GameContext context = context(Seat.LANDLORD, 10);
        LocalTurnRecognitionPort.Request first = context.prepareLocalTurn("model-turn-1", 1_500);
        context.reconcile(ready(first, initialHand, Map.of()));
        LocalTurnRecognitionPort.Request second = context.prepareLocalTurn("model-turn-2", 1_500);
        context.reconcile(ready(
                second,
                withoutOne(initialHand, CardRank.THREE),
                Map.of(
                        Seat.LANDLORD_DOWN, new PlayAction.Pass(),
                        Seat.LANDLORD_UP, play(CardRank.FOUR)
                )
        ));

        ResNet2Request wire = new ResNet2ProtocolAdapter(new DouZeroCardCodec()).encode(
                new com.mkuiwu.douzero.runtime.application.model.AdviceQuery(
                        "model-request", "deal-10", 1_500, context.snapshot()));

        assertEquals(List.of(List.of(3), List.of(), List.of(4)), wire.actionHistory());
    }

    private static final Instant OBSERVED_AT = Instant.parse("2026-08-26T04:00:00Z");

    private GameContext context(Seat localSeat, long generation) {
        return GameContext.from(new DealRecognitionPort.Recognized(
                new GameTaskIdentity("deal-task-" + generation, "deal-" + generation, generation),
                localSeat,
                handFor(localSeat),
                cards(CardRank.FIVE, CardRank.SIX, CardRank.SEVEN),
                localSeat == Seat.LANDLORD
                        ? CardSet.empty()
                        : cards(CardRank.EIGHT, CardRank.EIGHT),
                OBSERVED_AT
        ));
    }

    private LocalTurnRecognitionPort.Ready ready(
            LocalTurnRecognitionPort.Request request,
            CardSet currentHand,
            Map<Seat, PlayAction> actions
    ) {
        return new LocalTurnRecognitionPort.Ready(
                request.identity(), currentHand, actions, OBSERVED_AT);
    }

    private CardSet handFor(Seat seat) {
        int size = seat == Seat.LANDLORD ? 20 : 17;
        List<Card> cards = new ArrayList<>(size);
        cards.add(new Card(CardRank.THREE));
        cards.add(new Card(CardRank.THREE));
        CardRank[] ranks = CardRank.values();
        for (int index = cards.size(); index < size; index++) {
            cards.add(new Card(ranks[(index - 1) % ranks.length]));
        }
        return new CardSet(cards);
    }

    private CardSet withoutOne(CardSet source, CardRank rank) {
        List<Card> cards = new ArrayList<>(source.cards());
        int index = 0;
        while (cards.get(index).rank() != rank) {
            index++;
        }
        cards.remove(index);
        return new CardSet(cards);
    }

    private Map<Seat, PlayAction> firstRoundActions(Seat localSeat) {
        return switch (localSeat) {
            case LANDLORD -> Map.of();
            case LANDLORD_DOWN -> Map.of();
            case LANDLORD_UP -> Map.of(Seat.LANDLORD_DOWN, new PlayAction.Pass());
        };
    }

    private Map<Seat, PlayAction> opponentPasses() {
        return Map.of(
                Seat.LANDLORD_DOWN, new PlayAction.Pass(),
                Seat.LANDLORD_UP, new PlayAction.Pass()
        );
    }

    private PlayAction.Play play(CardRank... ranks) {
        return new PlayAction.Play(cards(ranks));
    }

    private CardSet cards(CardRank... ranks) {
        return new CardSet(java.util.Arrays.stream(ranks).map(Card::new).toList());
    }
}
