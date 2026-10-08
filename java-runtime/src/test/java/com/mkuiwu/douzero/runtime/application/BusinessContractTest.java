package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailureCode;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class BusinessContractTest {
    /** 验证等待新局时尚未创建牌局，新局请求和结果只能携带 requestId。 */
    @Test
    void newGameTaskOwnsNoDealIdentity() {
        NewGameRecognitionPort.Request request =
                new NewGameRecognitionPort.Request("new-game-1", 5_000);
        NewGameRecognitionPort.Detected result = new NewGameRecognitionPort.Detected(
                request.requestId(),
                Instant.parse("2026-08-26T03:00:00Z")
        );

        assertEquals("new-game-1", result.requestId());
    }

    /** 验证本方为农民时，建局结果必须携带地主首手牌，缺失时拒绝建立正式牌局。 */
    @Test
    void farmerDealCarriesLandlordOpeningPlay() {
        GameTaskIdentity identity = new GameTaskIdentity("deal-task-1", "deal-1", 3);
        DealRecognitionPort.Recognized result = new DealRecognitionPort.Recognized(
                identity,
                Seat.LANDLORD_DOWN,
                cards(17),
                cards(3),
                cards(2),
                Instant.parse("2026-08-26T03:00:01Z")
        );

        assertEquals(2, result.landlordOpeningPlay().size());
        assertThrows(IllegalArgumentException.class, () -> new DealRecognitionPort.Recognized(
                identity,
                Seat.LANDLORD_DOWN,
                cards(17),
                cards(3),
                CardSet.empty(),
                Instant.parse("2026-08-26T03:00:01Z")
        ));
    }

    /** 验证本方为地主时以 20 张手牌直接建局，尚未发生地主首手，因此首手牌必须为空。 */
    @Test
    void landlordDealStartsBeforeLandlordOpeningPlay() {
        DealRecognitionPort.Recognized result = new DealRecognitionPort.Recognized(
                new GameTaskIdentity("deal-task-2", "deal-2", 4),
                Seat.LANDLORD,
                cards(20),
                cards(3),
                CardSet.empty(),
                Instant.parse("2026-08-26T03:00:02Z")
        );

        assertEquals(Seat.LANDLORD, result.localSeat());
    }

    /** 验证本方回合端口只返回当前手牌和按语义座位索引的动作，不携带上一手牌等历史状态。 */
    @Test
    void localTurnReturnsSemanticSnapshotWithoutPreviousHand() {
        GameTaskIdentity readyIdentity = new GameTaskIdentity("turn-ready-1", "deal-1", 3);
        LocalTurnRecognitionPort.Request request = new LocalTurnRecognitionPort.Request(
                readyIdentity,
                Seat.LANDLORD_UP,
                Set.of(Seat.LANDLORD_DOWN),
                LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                1_500
        );
        LocalTurnRecognitionPort.Ready ready = new LocalTurnRecognitionPort.Ready(
                readyIdentity,
                cards(17),
                Map.of(Seat.LANDLORD_DOWN, new PlayAction.Pass()),
                Instant.parse("2026-08-26T03:00:03Z")
        );

        assertEquals(Set.of(Seat.LANDLORD_DOWN), request.requiredActors());
        assertEquals(17, ready.currentHand().size());
        assertEquals(new PlayAction.Pass(), ready.actionsBySeat().get(Seat.LANDLORD_DOWN));
        assertEquals(
                List.of("identity", "localSeat", "requiredActors", "turnEntryMode", "deadlineMs"),
                java.util.Arrays.stream(LocalTurnRecognitionPort.Request.class.getRecordComponents())
                        .map(component -> component.getName())
                        .toList()
        );
        assertEquals(
                List.of("identity", "currentHand", "actionsBySeat", "observedAt"),
                java.util.Arrays.stream(LocalTurnRecognitionPort.Ready.class.getRecordComponents())
                        .map(component -> component.getName())
                        .toList()
        );
    }

    /** 验证结算成功和失败结果都保留当前牌局身份，便于 Java 拒绝旧局或旧代回调。 */
    @Test
    void settlementAndFailureKeepCurrentGameIdentity() {
        GameTaskIdentity identity = new GameTaskIdentity("settlement-1", "deal-1", 3);
        SettlementWatchPort.Detected detected = new SettlementWatchPort.Detected(
                identity,
                Instant.parse("2026-08-26T03:00:05Z")
        );
        SettlementWatchPort.Failed failed = new SettlementWatchPort.Failed(
                identity,
                new RecognitionFailure(
                        RecognitionFailureCode.OBSERVATION_UNCERTAIN,
                        "结算页在任务截止前没有稳定"
                )
        );

        assertEquals(3, detected.identity().generation());
        assertEquals(RecognitionFailureCode.OBSERVATION_UNCERTAIN,
                failed.failure().code());
    }

    private static CardSet cards(int size) {
        List<Card> cards = new ArrayList<>(size);
        CardRank[] ranks = CardRank.values();
        for (int index = 0; index < size; index++) {
            cards.add(new Card(ranks[index % ranks.length]));
        }
        return new CardSet(cards);
    }
}
