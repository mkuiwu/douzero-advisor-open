package com.mkuiwu.douzero.runtime.application;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.FinishReason;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailureCode;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.DecisionPort;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.application.port.RuntimeIdentitySource;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import com.mkuiwu.douzero.runtime.domain.Seat;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.Set;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Delayed;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BiConsumer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GameOrchestratorTest {
    /** 验证新局提交异常会记录请求身份、重试间隔和完整异常。 */
    @Test
    void logsNewGameSubmissionFailureWithRequestIdentity() {
        LogCapture logs = captureLogs();
        try {
            Harness harness = new Harness();
            harness.newGame.throwOnSubmit = true;

            harness.orchestrator.start();
            harness.events.runAll();

            assertTrue(logs.contains(Level.ERROR,
                    "提交新局识别失败 requestId=request-1 retryDelayMs=100"));
            assertTrue(logs.events().stream().anyMatch(event ->
                    event.getThrowableProxy() != null
                            && IllegalStateException.class.getName().equals(
                            event.getThrowableProxy().getClassName())));
        } finally {
            logs.close();
        }
    }

    /** 验证结构化识别失败日志包含牌局身份、失败分类、原因和恢复动作。 */
    @Test
    void logsRecognitionFailureWithCorrelationAndRecoveryAction() {
        LogCapture logs = captureLogs();
        try {
            Harness harness = new Harness();
            harness.startAndDetectNewGame();
            PreplayRecognitionPort.Request request = harness.preplay.requests.get(0);

            harness.preplay.jobs.get(0).emit(new PreplayRecognitionPort.Failed(
                    request.identity(), failure("局前提示未稳定")));
            harness.events.runAll();

            assertTrue(logs.contains(Level.WARN,
                    "局前提示识别失败 requestId=" + request.identity().requestId()
                            + " dealId=" + request.identity().dealId()
                            + " generation=" + request.identity().generation()
                            + " code=OBSERVATION_UNCERTAIN detail=局前提示未稳定"
                            + " action=重试或等待"));
        } finally {
            logs.close();
        }
    }

    /** 验证新局开始后会并行启动结算、发牌和局前识别，并在事件循环外执行局前决策。 */
    @Test
    void startsThreePreplayBranchesAndRunsDecisionOutsideEventLoop() {
        Harness harness = new Harness();

        harness.startAndDetectNewGame();

        assertEquals(GamePhase.PREPLAY, harness.orchestrator.phase());
        assertEquals(List.of("settlement", "deal", "preplay"), harness.submissions);
        assertEquals(1, harness.settlement.requests.size());
        assertEquals(1, harness.deal.requests.size());
        assertEquals(1, harness.preplay.requests.size());
        assertEquals(PreplayRecognitionPort.EntryMode.ACCEPT_CURRENT_STABLE_PROMPT,
                harness.preplay.requests.get(0).entryMode());

        PreplayRecognitionPort.Request prompt = harness.preplay.requests.get(0);
        harness.preplay.jobs.get(0).emit(promptReady(prompt, PreplayStage.CALL_LANDLORD));
        harness.events.runAll();

        assertEquals(0, harness.preplayDecisionCalls);
        assertEquals(1, harness.decisions.queued());
        harness.decisions.runNext();
        assertEquals(1, harness.preplayDecisionCalls);
        assertEquals(0, harness.observer.preplayAdvice.size());

        harness.events.runAll();
        assertEquals(1, harness.observer.preplayAdvice.size());
        assertTrue(harness.preplayQueries.get(0).snapshot().callPromptSeen());
        assertFalse(harness.preplayQueries.get(0).snapshot().robPromptSeen());
        assertEquals(2, harness.preplay.requests.size());
        assertEquals(PreplayRecognitionPort.EntryMode.REQUIRE_CHANGE_OR_EXIT_THEN_NEW,
                harness.preplay.requests.get(1).entryMode());
    }

    /** 验证加倍成功后不再订下一张局前提示，但仍发布加倍建议且发牌继续。 */
    @Test
    void doublePromptDoesNotRequestAnotherPreplayAndStillPublishesAdvice() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        PreplayRecognitionPort.Request prompt = harness.preplay.requests.get(0);
        harness.preplay.jobs.get(0).emit(promptReady(prompt, PreplayStage.DOUBLE));
        harness.events.runAll();

        assertEquals(1, harness.preplay.requests.size());
        assertEquals(1, harness.decisions.queued());
        harness.decisions.runNext();
        harness.events.runAll();

        assertEquals(1, harness.observer.preplayAdvice.size());
        assertEquals(1, harness.preplay.requests.size());
        assertEquals(GamePhase.PREPLAY, harness.orchestrator.phase());
        assertFalse(harness.deal.jobs.get(0).cancelled);
    }

    /** 验证抢地主后严格监听加倍，负向建议也会依次发布且不继续虚构后续提示。 */
    @Test
    void robPromptSchedulesEdgeBoundDoublePromptAndPublishesNegativeAdvice() {
        Harness harness = new Harness();
        harness.preplayAction = PreplayAction.NO_ROB;
        harness.startAndDetectNewGame();
        PreplayRecognitionPort.Request prompt = harness.preplay.requests.get(0);
        harness.preplay.jobs.get(0).emit(promptReady(prompt, PreplayStage.ROB_LANDLORD));
        harness.events.runAll();

        assertEquals(2, harness.preplay.requests.size());
        assertEquals(PreplayRecognitionPort.EntryMode.REQUIRE_CHANGE_OR_EXIT_THEN_NEW,
                harness.preplay.requests.get(1).entryMode());
        harness.decisions.runNext();
        harness.events.runAll();

        assertEquals(1, harness.observer.preplayAdvice.size());
        assertEquals(PreplayAction.NO_ROB,
                harness.observer.preplayAdvice.get(0).action());
        harness.preplayAction = PreplayAction.NO_DOUBLE;
        PreplayRecognitionPort.Request doublePrompt = harness.preplay.requests.get(1);
        harness.preplay.jobs.get(1).emit(promptReady(doublePrompt, PreplayStage.DOUBLE));
        harness.events.runAll();
        harness.decisions.runNext();
        harness.events.runAll();

        assertEquals(2, harness.observer.preplayAdvice.size());
        assertEquals(PreplayAction.NO_DOUBLE,
                harness.observer.preplayAdvice.get(1).action());
        assertEquals(2, harness.preplay.requests.size());
        assertEquals(GamePhase.PREPLAY, harness.orchestrator.phase());
    }

    /** 验证发牌完成后会取消局前分支，并进入正式对局阶段。 */
    @Test
    void dealReadyCancelsPreplayBranchAndEntersPlaying() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        PreplayRecognitionPort.Request first = harness.preplay.requests.get(0);
        harness.preplay.jobs.get(0).emit(promptReady(first, PreplayStage.CALL_LANDLORD));
        harness.events.runAll();
        FakeJob<PreplayRecognitionPort.Result> secondPrompt = harness.preplay.jobs.get(1);

        DealRecognitionPort.Request dealRequest = harness.deal.requests.get(0);
        harness.deal.jobs.get(0).emit(landlordDeal(dealRequest));
        harness.events.runAll();

        assertEquals(GamePhase.PLAYING, harness.orchestrator.phase());
        assertTrue(secondPrompt.cancelled);
        assertEquals(1, harness.localTurn.requests.size());
        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                harness.localTurn.requests.get(0).turnEntryMode());
        assertFalse(harness.settlement.jobs.get(0).cancelled);
        harness.decisions.runNext();
        harness.events.runAll();
        assertEquals(0, harness.preplayDecisionCalls);
        assertEquals(0, harness.observer.preplayAdvice.size());
    }

    /** 验证检测到结算页会抢占其他识别分支，并忽略它们之后到达的重复或过期回调。 */
    @Test
    void settlementPreemptsDealAndIgnoresItsLateAndDuplicateCallbacks() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        DealRecognitionPort.Request dealRequest = harness.deal.requests.get(0);
        SettlementWatchPort.Request settlementRequest = harness.settlement.requests.get(0);

        harness.settlement.jobs.get(0).emit(new SettlementWatchPort.Detected(
                settlementRequest.identity(), Instant.parse("2026-08-26T08:00:01Z")));
        harness.events.runAll();

        assertEquals(GamePhase.WAIT_NEW_GAME, harness.orchestrator.phase());
        assertTrue(harness.deal.jobs.get(0).cancelled);
        assertTrue(harness.preplay.jobs.get(0).cancelled);
        assertEquals(List.of(FinishReason.SETTLEMENT_DETECTED), harness.observer.finishReasons);
        assertEquals(2, harness.newGame.requests.size());

        harness.deal.jobs.get(0).emit(landlordDeal(dealRequest));
        harness.settlement.jobs.get(0).emit(new SettlementWatchPort.Detected(
                settlementRequest.identity(), Instant.parse("2026-08-26T08:00:02Z")));
        harness.events.runAll();

        assertEquals(GamePhase.WAIT_NEW_GAME, harness.orchestrator.phase());
        assertEquals(0, harness.localTurn.requests.size());
        assertEquals(1, harness.observer.finishReasons.size());
    }

    /** 验证旧的局前提示回调不会重复创建决策任务。 */
    @Test
    void duplicateOldPromptCallbackCannotCreateAnotherDecision() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        PreplayRecognitionPort.Request first = harness.preplay.requests.get(0);
        PreplayRecognitionPort.Ready ready = promptReady(first, PreplayStage.CALL_LANDLORD);
        harness.preplay.jobs.get(0).emit(ready);
        harness.events.runAll();
        harness.decisions.runNext();
        harness.events.runAll();
        assertEquals(2, harness.preplay.requests.size());

        harness.preplay.jobs.get(0).emit(ready);
        harness.events.runAll();

        assertEquals(1, harness.preplayDecisionCalls);
        assertEquals(0, harness.decisions.queued());
        assertEquals(2, harness.preplay.requests.size());
    }

    /** 验证局前识别失败只停止建议分支，模型失败也不会影响发牌识别。 */
    @Test
    void preplayRecognitionFailureStopsOnlyAdviceBranchAndModelFailureDoesNotStopDeal() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        PreplayRecognitionPort.Request request = harness.preplay.requests.get(0);

        harness.preplay.jobs.get(0).emit(new PreplayRecognitionPort.Failed(
                request.identity(), failure("局前提示未稳定")));
        harness.events.runAll();

        assertEquals(GamePhase.PREPLAY, harness.orchestrator.phase());
        assertFalse(harness.deal.jobs.get(0).cancelled);
        assertEquals(1, harness.preplay.requests.size());
        assertEquals(0, harness.decisions.queued());

        Harness modelFailure = new Harness();
        modelFailure.startAndDetectNewGame();
        modelFailure.preplayDecisionFails = true;
        PreplayRecognitionPort.Request prompt = modelFailure.preplay.requests.get(0);
        modelFailure.preplay.jobs.get(0).emit(promptReady(prompt, PreplayStage.ROB_LANDLORD));
        modelFailure.events.runAll();
        modelFailure.decisions.runNext();
        modelFailure.events.runAll();

        assertEquals(GamePhase.PREPLAY, modelFailure.orchestrator.phase());
        assertFalse(modelFailure.deal.jobs.get(0).cancelled);
        assertEquals(2, modelFailure.preplay.requests.size());
        assertEquals(0, modelFailure.observer.preplayAdvice.size());
    }

    /** 验证结算监控超时或不确定失败后会以新身份重启监控。 */
    @Test
    void settlementTimeoutAndUncertainFailureRestartWatcherWithNewIdentity() {
        Harness timeout = new Harness();
        timeout.startAndDetectNewGame();
        String firstTimeoutId = timeout.settlement.requests.get(0).identity().requestId();
        timeout.scheduler.fireDelay(900);
        timeout.events.runAll();

        assertEquals(2, timeout.settlement.requests.size());
        assertNotEquals(firstTimeoutId,
                timeout.settlement.requests.get(1).identity().requestId());
        assertFalse(timeout.deal.jobs.get(0).cancelled);

        Harness uncertain = new Harness();
        uncertain.startAndDetectNewGame();
        SettlementWatchPort.Request request = uncertain.settlement.requests.get(0);
        uncertain.settlement.jobs.get(0).emit(new SettlementWatchPort.Failed(
                request.identity(), failure("结算页暂未稳定")));
        uncertain.events.runAll();

        assertEquals(2, uncertain.settlement.requests.size());
        assertEquals(GamePhase.PREPLAY, uncertain.orchestrator.phase());
        assertFalse(uncertain.deal.jobs.get(0).cancelled);
    }

    /** 验证 Java 侧局前超时只取消局前建议，不影响发牌和结算监控。 */
    @Test
    void preplayJavaTimeoutStopsOnlyAdviceBranch() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();

        harness.scheduler.fireDelay(300);
        harness.events.runAll();

        assertEquals(GamePhase.PREPLAY, harness.orchestrator.phase());
        assertTrue(harness.preplay.jobs.get(0).cancelled);
        assertEquals(1, harness.preplay.requests.size());
        assertFalse(harness.deal.jobs.get(0).cancelled);
        assertFalse(harness.settlement.jobs.get(0).cancelled);
    }

    /** 验证严重结算失败会结束当前局，并在新局提交同步失败时安排重试。 */
    @Test
    void hardSettlementFailureClosesAndSynchronousNewGameFailureSchedulesRetry() {
        Harness settlementFailure = new Harness();
        settlementFailure.startAndDetectNewGame();
        SettlementWatchPort.Request request = settlementFailure.settlement.requests.get(0);
        settlementFailure.settlement.jobs.get(0).emit(new SettlementWatchPort.Failed(
                request.identity(),
                new RecognitionFailure(RecognitionFailureCode.CAPTURE_UNAVAILABLE, "捕获服务失效")
        ));
        settlementFailure.events.runAll();

        assertEquals(List.of(FinishReason.RECOGNITION_FAILED),
                settlementFailure.observer.finishReasons);
        assertEquals(GamePhase.WAIT_NEW_GAME, settlementFailure.orchestrator.phase());

        Harness newGameRetry = new Harness();
        newGameRetry.newGame.throwOnSubmit = true;
        newGameRetry.orchestrator.start();
        newGameRetry.events.runAll();
        assertEquals(0, newGameRetry.newGame.requests.size());
        newGameRetry.newGame.throwOnSubmit = false;
        newGameRetry.scheduler.fireDelay(100);
        newGameRetry.events.runAll();
        assertEquals(1, newGameRetry.newGame.requests.size());
    }

    /** 验证发牌或本方回合识别发生严重失败时，只关闭当前对局并等待新局。 */
    @Test
    void hardDealAndLocalFailuresCloseOnlyTheCurrentGame() {
        Harness dealFailure = new Harness();
        dealFailure.startAndDetectNewGame();
        DealRecognitionPort.Request dealRequest = dealFailure.deal.requests.get(0);
        dealFailure.deal.jobs.get(0).emit(new DealRecognitionPort.Failed(
                dealRequest.identity(), failure("正式出牌事实未闭合")));
        dealFailure.events.runAll();

        assertEquals(List.of(FinishReason.RECOGNITION_FAILED),
                dealFailure.observer.finishReasons);
        assertEquals(GamePhase.WAIT_NEW_GAME, dealFailure.orchestrator.phase());

        Harness localFailure = new Harness();
        localFailure.startAndDetectNewGame();
        DealRecognitionPort.Request recognizedRequest = localFailure.deal.requests.get(0);
        localFailure.deal.jobs.get(0).emit(landlordDeal(recognizedRequest));
        localFailure.events.runAll();
        LocalTurnRecognitionPort.Request turn = localFailure.localTurn.requests.get(0);
        localFailure.localTurn.jobs.get(0).emit(new LocalTurnRecognitionPort.Failed(
                turn.identity(), failure("本方回合未稳定")));
        localFailure.events.runAll();

        assertEquals(List.of(FinishReason.RECOGNITION_FAILED),
                localFailure.observer.finishReasons);
        assertEquals(GamePhase.WAIT_NEW_GAME, localFailure.orchestrator.phase());
    }

    /** 验证建议完成后 Java 只启动回合结束任务，结束确认前不会重复提交本方回合识别。 */
    @Test
    void localTurnNeverSchedulesJavaLifecycleTimeout() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        DealRecognitionPort.Request dealRequest = harness.deal.requests.get(0);
        harness.deal.jobs.get(0).emit(landlordDeal(dealRequest));
        harness.events.runAll();
        LocalTurnRecognitionPort.Request firstTurn = harness.localTurn.requests.get(0);
        assertFalse(harness.scheduler.hasActiveDelay(400));
        harness.localTurn.jobs.get(0).emit(new LocalTurnRecognitionPort.Ready(
                firstTurn.identity(), cards(20), Map.of(), Instant.parse("2026-08-26T08:00:03Z")));
        harness.events.runAll();
        harness.decisions.runNext();
        harness.events.runAll();

        assertEquals(1, harness.localTurn.requests.size());
        assertEquals(1, harness.turnEnd.requests.size());
        assertFalse(harness.scheduler.hasActiveDelay(400));

        TurnEndRecognitionPort.Request turnEnd = harness.turnEnd.requests.get(0);
        harness.turnEnd.jobs.get(0).emit(new TurnEndRecognitionPort.Ended(
                turnEnd.identity(), Instant.parse("2026-08-26T08:00:04Z")));
        harness.events.runAll();
        assertEquals(2, harness.localTurn.requests.size());
        assertEquals(LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                harness.localTurn.requests.get(1).turnEntryMode());

        assertEquals(GamePhase.PLAYING, harness.orchestrator.phase());
        assertTrue(harness.observer.finishReasons.isEmpty());
        assertFalse(harness.localTurn.jobs.get(1).cancelled);
    }

    /** 验证出牌模型失败会保留已对账历史，并先等待当前本方回合结束。 */
    @Test
    void playModelFailureKeepsReconciledHistoryAndWaitsForNextTurn() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        DealRecognitionPort.Request dealRequest = harness.deal.requests.get(0);
        harness.deal.jobs.get(0).emit(landlordDeal(dealRequest));
        harness.events.runAll();
        LocalTurnRecognitionPort.Request firstTurn = harness.localTurn.requests.get(0);
        harness.localTurn.jobs.get(0).emit(new LocalTurnRecognitionPort.Ready(
                firstTurn.identity(), cards(20), Map.of(), Instant.parse("2026-08-26T08:00:03Z")));
        harness.events.runAll();

        assertEquals(0, harness.playDecisionCalls);
        harness.playDecisionFails = true;
        harness.decisions.runNext();
        harness.events.runAll();

        assertEquals(1, harness.playDecisionCalls);
        assertEquals(GamePhase.PLAYING, harness.orchestrator.phase());
        assertEquals(1, harness.localTurn.requests.size());
        assertEquals(1, harness.turnEnd.requests.size());
        assertEquals(0, harness.observer.playResults.size());
    }

    /** 验证 Java 侧发牌超时和主动停止都会取消当前会话拥有的全部资源。 */
    @Test
    void javaDealTimeoutAndStopCancelAllOwnedResources() {
        Harness timeout = new Harness();
        timeout.startAndDetectNewGame();
        timeout.scheduler.fireDelay(200);
        timeout.events.runAll();

        assertEquals(List.of(FinishReason.STATE_TIMEOUT), timeout.observer.finishReasons);
        assertTrue(timeout.deal.jobs.get(0).cancelled);
        assertTrue(timeout.preplay.jobs.get(0).cancelled);
        assertTrue(timeout.settlement.jobs.get(0).cancelled);
        assertEquals(GamePhase.WAIT_NEW_GAME, timeout.orchestrator.phase());

        Harness stopped = new Harness();
        stopped.startAndDetectNewGame();
        stopped.orchestrator.stop();
        stopped.events.runAll();

        assertFalse(stopped.orchestrator.isRunning());
        assertTrue(stopped.deal.jobs.get(0).cancelled);
        assertTrue(stopped.preplay.jobs.get(0).cancelled);
        assertTrue(stopped.settlement.jobs.get(0).cancelled);
        assertEquals(1, stopped.newGame.requests.size());
    }

    /** 验证停止流程即使遇到识别或决策任务取消异常，也会继续完成清理。 */
    @Test
    void stopContinuesAfterRecognitionAndDecisionCancellationFailures() {
        Harness harness = new Harness();
        harness.startAndDetectNewGame();
        harness.preplay.jobs.get(0).throwOnCancel = true;
        ThrowingCancelFuture decision = new ThrowingCancelFuture();
        ManualScheduledFuture decisionTimeout = new ManualScheduledFuture(() -> { }, 500);
        harness.createdSession.installDecision(
                new GameTaskIdentity("decision", harness.createdSession.dealId(),
                        harness.createdSession.generation()),
                decision,
                decisionTimeout);

        CompletionStage<Void> stopped = harness.orchestrator.stopAsync();
        harness.events.runAll();

        assertDoesNotThrow(() -> stopped.toCompletableFuture().join());
        assertFalse(harness.orchestrator.isRunning());
        assertTrue(harness.preplay.jobs.get(0).cancelled);
        assertTrue(harness.deal.jobs.get(0).cancelled);
        assertTrue(harness.settlement.jobs.get(0).cancelled);
        assertTrue(decision.cancelAttempted);
        assertTrue(decisionTimeout.isCancelled());
        assertTrue(harness.createdSession.isClosed());
        assertTrue(harness.createdSession.preplayContext().isClosed());
    }

    /** 验证不同局前阶段只接受对应数量的手牌和合法动作集合。 */
    @Test
    void preplayContractsEnforceStageSpecificCardsAndActions() {
        GameTaskIdentity identity = new GameTaskIdentity("prompt", "deal", 1);

        assertThrows(IllegalArgumentException.class, () -> new PreplayRecognitionPort.Ready(
                identity,
                PreplayStage.CALL_LANDLORD,
                cards(20),
                CardSet.empty(),
                Set.of(PreplayAction.CALL),
                Instant.now()
        ));
        assertThrows(IllegalArgumentException.class, () -> new PreplayRecognitionPort.Ready(
                identity,
                PreplayStage.DOUBLE,
                cards(20),
                CardSet.empty(),
                Set.of(PreplayAction.DOUBLE),
                Instant.now()
        ));
        assertThrows(IllegalArgumentException.class, () -> new PreplayRecognitionPort.Ready(
                identity,
                PreplayStage.ROB_LANDLORD,
                cards(17),
                CardSet.empty(),
                Set.of(PreplayAction.NO_CALL),
                Instant.now()
        ));
    }

    /** 验证无效提示请求不会污染身份状态，重复的有效提示回调具备幂等性。 */
    @Test
    void preplayContextDoesNotPolluteIdentityOnInvalidRequestAndAppliesReadyIdempotently() {
        PreplayContext context = new PreplayContext("deal", 1);
        assertThrows(IllegalArgumentException.class,
                () -> context.preparePrompt("same-id", 0));
        PreplayRecognitionPort.Request request = context.preparePrompt("same-id", 100);
        PreplayRecognitionPort.Ready ready = promptReady(
                request, PreplayStage.CALL_LANDLORD);

        PreplaySnapshot first = context.observe(ready);
        PreplaySnapshot duplicate = context.observe(ready);

        assertEquals(first, duplicate);
        assertTrue(duplicate.callPromptSeen());
        assertFalse(duplicate.robPromptSeen());
    }

    private static PreplayRecognitionPort.Ready promptReady(
            PreplayRecognitionPort.Request request,
            PreplayStage stage
    ) {
        Set<PreplayAction> actions = switch (stage) {
            case CALL_LANDLORD -> Set.of(PreplayAction.CALL, PreplayAction.NO_CALL);
            case ROB_LANDLORD -> Set.of(PreplayAction.ROB, PreplayAction.NO_ROB);
            case DOUBLE -> Set.of(PreplayAction.DOUBLE, PreplayAction.NO_DOUBLE);
        };
        return new PreplayRecognitionPort.Ready(
                request.identity(), stage, cards(17), CardSet.empty(), actions,
                Instant.parse("2026-08-26T08:00:00Z"));
    }

    private static DealRecognitionPort.Recognized landlordDeal(
            DealRecognitionPort.Request request
    ) {
        return new DealRecognitionPort.Recognized(
                request.identity(), Seat.LANDLORD, cards(20), cards(3), CardSet.empty(),
                Instant.parse("2026-08-26T08:00:00Z"));
    }

    private static RecognitionFailure failure(String detail) {
        return new RecognitionFailure(RecognitionFailureCode.OBSERVATION_UNCERTAIN, detail);
    }

    private static CardSet cards(int size) {
        List<Card> cards = new ArrayList<>();
        CardRank[] ranks = CardRank.values();
        for (int index = 0; index < size; index++) {
            cards.add(new Card(ranks[index % ranks.length]));
        }
        return new CardSet(cards);
    }

    private static LogCapture captureLogs() {
        Logger logger = (Logger) LoggerFactory.getLogger(GameOrchestrator.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        return new LogCapture(logger, appender);
    }

    private record LogCapture(
            Logger logger,
            ListAppender<ILoggingEvent> appender
    ) implements AutoCloseable {
        private List<ILoggingEvent> events() {
            return appender.list;
        }

        private boolean contains(Level level, String message) {
            return events().stream().anyMatch(event -> event.getLevel() == level
                    && event.getFormattedMessage().contains(message));
        }

        @Override
        public void close() {
            logger.detachAppender(appender);
            appender.stop();
        }
    }

    private static final class Harness {
        private final ManualExecutor events = new ManualExecutor();
        private final ManualExecutorService decisions = new ManualExecutorService();
        private final ManualScheduler scheduler = new ManualScheduler();
        private final List<String> submissions = new ArrayList<>();
        private final FakeNewGamePort newGame = new FakeNewGamePort();
        private final FakePreplayPort preplay = new FakePreplayPort(submissions);
        private final FakeDealPort deal = new FakeDealPort(submissions);
        private final FakeLocalTurnPort localTurn = new FakeLocalTurnPort();
        private final FakeTurnEndPort turnEnd = new FakeTurnEndPort();
        private final FakeSettlementPort settlement = new FakeSettlementPort(submissions);
        private final Observer observer = new Observer();
        private final List<PreplayQuery> preplayQueries = new ArrayList<>();
        private int preplayDecisionCalls;
        private int playDecisionCalls;
        private boolean preplayDecisionFails;
        private boolean playDecisionFails;
        private PreplayAction preplayAction;
        private GameSession createdSession;
        private final GameOrchestrator orchestrator;

        private Harness() {
            PreplayDecisionPort preplayDecision = query -> {
                preplayDecisionCalls++;
                preplayQueries.add(query);
                if (preplayDecisionFails) {
                    throw new IllegalStateException("局前模型失败");
                }
                PreplayAction action = preplayAction != null
                        ? preplayAction
                        : query.snapshot().availableActions().iterator().next();
                return new PreplayResult.Recommendation(
                        query.identity(), "preplay-fake", action, 0.4, 0.2, Map.of());
            };
            DecisionPort playDecision = query -> {
                playDecisionCalls++;
                if (playDecisionFails) {
                    throw new IllegalStateException("正式模型失败");
                }
                return new AdviceResult.Unavailable(
                        "play-fake", AdviceFailure.NO_RECOMMENDATION, "测试等待");
            };
            orchestrator = new GameOrchestrator(
                    newGame,
                    preplay,
                    deal,
                    localTurn,
                    turnEnd,
                    settlement,
                    preplayDecision,
                    playDecision,
                    observer,
                    new SequentialIdentitySource(),
                    (dealId, generation) -> {
                        createdSession = new GameSession(dealId, generation);
                        return createdSession;
                    },
                    events,
                    decisions,
                    scheduler,
                    new GameOrchestrator.Deadlines(100, 300, 200, 900, 400, 450, 500, 600)
            );
        }

        private void startAndDetectNewGame() {
            orchestrator.start();
            events.runAll();
            assertEquals(1, newGame.requests.size());
            NewGameRecognitionPort.Request request = newGame.requests.get(0);
            newGame.jobs.get(0).emit(new NewGameRecognitionPort.Detected(
                    request.requestId(), Instant.parse("2026-08-26T07:59:59Z")));
            events.runAll();
        }
    }

    private static final class SequentialIdentitySource implements RuntimeIdentitySource {
        private int dealSequence;
        private int requestSequence;
        private long generation;

        @Override
        public String nextDealId() {
            return "deal-" + ++dealSequence;
        }

        @Override
        public String nextRequestId() {
            return "request-" + ++requestSequence;
        }

        @Override
        public long nextGeneration() {
            return ++generation;
        }
    }

    private static final class Observer implements GameRuntimeObserver {
        private final List<GamePhase> phases = new ArrayList<>();
        private final List<PreplayResult.Recommendation> preplayAdvice = new ArrayList<>();
        private final List<AdviceResult> playResults = new ArrayList<>();
        private final List<FinishReason> finishReasons = new ArrayList<>();

        @Override
        public void onPhaseChanged(GamePhase phase, String dealId) {
            phases.add(phase);
        }

        @Override
        public void onPreplayAdvice(
                PreplaySnapshot snapshot,
                PreplayResult.Recommendation recommendation
        ) {
            preplayAdvice.add(recommendation);
        }

        @Override
        public void onPlayAdvice(GameSnapshot snapshot, AdviceResult result) {
            playResults.add(result);
        }

        @Override
        public void onGameFinished(String dealId, FinishReason reason) {
            finishReasons.add(reason);
        }
    }

    private static final class FakeNewGamePort implements NewGameRecognitionPort {
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();
        private boolean throwOnSubmit;

        @Override
        public RecognitionJob<Result> waitForNewGame(Request request) {
            if (throwOnSubmit) {
                throw new IllegalStateException("测试新局端口不可用");
            }
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakePreplayPort implements PreplayRecognitionPort {
        private final List<String> submissions;
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();

        private FakePreplayPort(List<String> submissions) {
            this.submissions = submissions;
        }

        @Override
        public RecognitionJob<Result> waitForPrompt(Request request) {
            submissions.add("preplay");
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.identity().requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakeDealPort implements DealRecognitionPort {
        private final List<String> submissions;
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();

        private FakeDealPort(List<String> submissions) {
            this.submissions = submissions;
        }

        @Override
        public RecognitionJob<Result> recognizeDeal(Request request) {
            submissions.add("deal");
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.identity().requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakeLocalTurnPort implements LocalTurnRecognitionPort {
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();

        @Override
        public RecognitionJob<Result> waitUntilReady(Request request) {
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.identity().requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakeTurnEndPort implements TurnEndRecognitionPort {
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();

        @Override
        public RecognitionJob<Result> waitUntilEnded(Request request) {
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.identity().requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakeSettlementPort implements SettlementWatchPort {
        private final List<String> submissions;
        private final List<Request> requests = new ArrayList<>();
        private final List<FakeJob<Result>> jobs = new ArrayList<>();

        private FakeSettlementPort(List<String> submissions) {
            this.submissions = submissions;
        }

        @Override
        public RecognitionJob<Result> watch(Request request) {
            submissions.add("settlement");
            requests.add(request);
            FakeJob<Result> job = new FakeJob<>(request.identity().requestId());
            jobs.add(job);
            return job;
        }
    }

    private static final class FakeJob<R> implements RecognitionJob<R> {
        private final String requestId;
        private final ReplayableStage<R> completion = new ReplayableStage<>();
        private boolean cancelled;
        private boolean throwOnCancel;

        private FakeJob(String requestId) {
            this.requestId = requestId;
        }

        @Override
        public String requestId() {
            return requestId;
        }

        @Override
        public CompletionStage<R> completion() {
            return completion;
        }

        @Override
        public void cancel() {
            cancelled = true;
            if (throwOnCancel) {
                throw new IllegalStateException("测试识别任务取消失败");
            }
        }

        private void emit(R result) {
            completion.emit(result, null);
        }
    }

    private static final class ThrowingCancelFuture implements Future<Object> {
        private boolean cancelAttempted;

        @Override
        public boolean cancel(boolean mayInterruptIfRunning) {
            cancelAttempted = true;
            throw new IllegalStateException("测试决策任务取消失败");
        }

        @Override
        public boolean isCancelled() {
            return false;
        }

        @Override
        public boolean isDone() {
            return false;
        }

        @Override
        public Object get() {
            throw new UnsupportedOperationException("测试不读取决策结果");
        }

        @Override
        public Object get(long timeout, TimeUnit unit) {
            throw new UnsupportedOperationException("测试不读取决策结果");
        }
    }

    private static final class ReplayableStage<T> extends CompletableFuture<T> {
        private final List<BiConsumer<? super T, ? super Throwable>> callbacks =
                new ArrayList<>();

        @Override
        public CompletableFuture<T> whenComplete(
                BiConsumer<? super T, ? super Throwable> action
        ) {
            callbacks.add(action);
            return this;
        }

        private void emit(T result, Throwable error) {
            List.copyOf(callbacks).forEach(callback -> callback.accept(result, error));
        }
    }

    private static class ManualExecutor implements java.util.concurrent.Executor {
        protected final Queue<Runnable> tasks = new ArrayDeque<>();

        @Override
        public void execute(Runnable command) {
            tasks.add(command);
        }

        private void runAll() {
            while (!tasks.isEmpty()) {
                tasks.remove().run();
            }
        }
    }

    private static final class ManualExecutorService extends AbstractExecutorService {
        private final Queue<Runnable> tasks = new ArrayDeque<>();
        private boolean shutdown;

        @Override
        public void shutdown() {
            shutdown = true;
        }

        @Override
        public List<Runnable> shutdownNow() {
            shutdown = true;
            List<Runnable> pending = List.copyOf(tasks);
            tasks.clear();
            return pending;
        }

        @Override
        public boolean isShutdown() {
            return shutdown;
        }

        @Override
        public boolean isTerminated() {
            return shutdown && tasks.isEmpty();
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return isTerminated();
        }

        @Override
        public void execute(Runnable command) {
            if (shutdown) {
                throw new RejectedExecutionException("测试决策执行器已关闭");
            }
            tasks.add(command);
        }

        private int queued() {
            return tasks.size();
        }

        private void runNext() {
            tasks.remove().run();
        }
    }

    private static final class ManualScheduler extends AbstractExecutorService
            implements ScheduledExecutorService {
        private final List<ManualScheduledFuture> scheduled = new ArrayList<>();
        private boolean shutdown;

        @Override
        public ScheduledFuture<?> schedule(Runnable command, long delay, TimeUnit unit) {
            ManualScheduledFuture future = new ManualScheduledFuture(
                    command, unit.toMillis(delay));
            scheduled.add(future);
            return future;
        }

        @Override
        public <V> ScheduledFuture<V> schedule(Callable<V> callable, long delay, TimeUnit unit) {
            throw new UnsupportedOperationException("测试不需要 Callable 调度");
        }

        @Override
        public ScheduledFuture<?> scheduleAtFixedRate(
                Runnable command,
                long initialDelay,
                long period,
                TimeUnit unit
        ) {
            throw new UnsupportedOperationException("测试不需要固定频率调度");
        }

        @Override
        public ScheduledFuture<?> scheduleWithFixedDelay(
                Runnable command,
                long initialDelay,
                long delay,
                TimeUnit unit
        ) {
            throw new UnsupportedOperationException("测试不需要固定间隔调度");
        }

        @Override
        public void shutdown() {
            shutdown = true;
        }

        @Override
        public List<Runnable> shutdownNow() {
            shutdown = true;
            return List.of();
        }

        @Override
        public boolean isShutdown() {
            return shutdown;
        }

        @Override
        public boolean isTerminated() {
            return shutdown;
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return shutdown;
        }

        @Override
        public void execute(Runnable command) {
            command.run();
        }

        private boolean hasActiveDelay(long delayMs) {
            return scheduled.stream()
                    .anyMatch(item -> !item.isCancelled() && item.delayMs == delayMs);
        }

        private void fireDelay(long delayMs) {
            ManualScheduledFuture future = scheduled.stream()
                    .filter(item -> !item.isCancelled() && item.delayMs == delayMs)
                    .min(Comparator.comparingLong(item -> item.sequence))
                    .orElseThrow();
            future.run();
        }
    }

    private static final class ManualScheduledFuture implements ScheduledFuture<Object> {
        private static long nextSequence;
        private final Runnable command;
        private final long delayMs;
        private final long sequence = ++nextSequence;
        private boolean cancelled;
        private boolean done;

        private ManualScheduledFuture(Runnable command, long delayMs) {
            this.command = command;
            this.delayMs = delayMs;
        }

        private void run() {
            if (!cancelled) {
                done = true;
                command.run();
            }
        }

        @Override
        public long getDelay(TimeUnit unit) {
            return unit.convert(delayMs, TimeUnit.MILLISECONDS);
        }

        @Override
        public int compareTo(Delayed other) {
            return Long.compare(getDelay(TimeUnit.MILLISECONDS),
                    other.getDelay(TimeUnit.MILLISECONDS));
        }

        @Override
        public boolean cancel(boolean mayInterruptIfRunning) {
            cancelled = true;
            return true;
        }

        @Override
        public boolean isCancelled() {
            return cancelled;
        }

        @Override
        public boolean isDone() {
            return done || cancelled;
        }

        @Override
        public Object get() throws InterruptedException, ExecutionException {
            if (!done) {
                throw new IllegalStateException("测试超时任务尚未触发");
            }
            return null;
        }

        @Override
        public Object get(long timeout, TimeUnit unit)
                throws InterruptedException, ExecutionException, TimeoutException {
            return get();
        }
    }
}
