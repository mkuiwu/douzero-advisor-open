package com.mkuiwu.douzero.runtime.infrastructure;

import ch.qos.logback.classic.Level;
import com.mkuiwu.douzero.runtime.application.model.ActionScore;
import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.testsupport.LogCapture;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertTrue;

class LoggingGameRuntimeObserverTest {
    /** 验证正式出牌成功建议会展示动作、价值、领先值和模型诊断元数据。 */
    @Test
    void recommendationLogContainsActionScoresAndModelMetadata() {
        LoggingGameRuntimeObserver observer = new LoggingGameRuntimeObserver();
        PlayAction action = new PlayAction.Play(
                new CardSet(List.of(new Card(CardRank.THREE))));
        AdviceResult result = new AdviceResult.Recommendation(
                "resnet2", action, 0.8, 0.2,
                List.of(new ActionScore(action, 0.8)),
                Map.of("modelVersion", "m1", "latencyMs", "3"));

        try (LogCapture logs = LogCapture.capture(LoggingGameRuntimeObserver.class)) {
            observer.onPlayAdvice(snapshot(), result);

            assertTrue(logs.contains(Level.INFO, "正式出牌只读建议 dealId=d1"));
            assertTrue(logs.contains(Level.INFO, "action=Play"));
            assertTrue(logs.contains(Level.INFO, "actionValue=0.8 actionMargin=0.2"));
            assertTrue(logs.contains(Level.INFO, "modelVersion=m1"));
            assertTrue(logs.contains(Level.DEBUG, "正式出牌只读建议候选明细"));
        }
    }

    /** 验证正式出牌不可用结果会以 WARN 明确展示稳定失败分类和诊断说明。 */
    @Test
    void unavailableLogContainsFailureAndDiagnosticDetail() {
        LoggingGameRuntimeObserver observer = new LoggingGameRuntimeObserver();
        AdviceResult result = new AdviceResult.Unavailable(
                "resnet2", AdviceFailure.MODEL_TIMEOUT, "模型响应超过截止时间");

        try (LogCapture logs = LogCapture.capture(LoggingGameRuntimeObserver.class)) {
            observer.onPlayAdvice(snapshot(), result);

            assertTrue(logs.contains(Level.WARN, "正式出牌只读建议不可用 dealId=d1"));
            assertTrue(logs.contains(Level.WARN, "failure=MODEL_TIMEOUT"));
            assertTrue(logs.contains(Level.WARN, "detail=模型响应超过截止时间"));
        }
    }

    private GameSnapshot snapshot() {
        return new GameSnapshot(
                "d1", GamePhase.PLAYING, Seat.LANDLORD,
                new CardSet(List.of(new Card(CardRank.THREE))),
                new CardSet(List.of(new Card(CardRank.FOUR), new Card(CardRank.FIVE),
                        new Card(CardRank.SIX))),
                List.of());
    }
}
