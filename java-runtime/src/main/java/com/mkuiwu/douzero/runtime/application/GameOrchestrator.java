package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.FinishReason;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailureCode;
import com.mkuiwu.douzero.runtime.application.port.CardPlayExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.DecisionPort;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayButtonExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.application.port.RuntimeIdentitySource;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Objects;
import java.util.concurrent.Callable;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.FutureTask;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

/**
 * Java 唯一牌局编排器；识别和模型完成回调全部汇入注入的单线程事件执行器。
 *
 * <p>本类只调度完整业务能力，不编排截图、Reader 或稳定逻辑；局前正向按钮点击由
 * 独立执行端口按用户授权触发。PREPLAY 中局前提示、完整牌局初始化和结算旁路并行；
 * 完整牌局事实一旦闭合就取消整个局前支路。</p>
 */
public final class GameOrchestrator {
    private static final Logger LOGGER = LoggerFactory.getLogger(GameOrchestrator.class);

    /** 整局结算旁路识别能力。 */
    private final SettlementWatchPort settlementPort;

    /** 只读阶段和建议展示端口。 */
    private final GameRuntimeObserver observer;

    /** 生成 Java 权威牌局和任务身份的来源。 */
    private final RuntimeIdentitySource identitySource;

    /** 为新局创建独立会话的工厂。 */
    private final GameSessionFactory sessionFactory;

    /** 串行执行所有状态读取和修改的事件执行器；注入方必须保证单线程。 */
    private final Executor eventExecutor;

    /** 隔离同步 Python 模型调用的决策执行器。 */
    private final ExecutorService decisionExecutor;

    /** 只负责触发 Java 侧硬超时、从不直接修改业务状态的调度器。 */
    private final ScheduledExecutorService timeoutScheduler;

    /** 所有识别和决策能力的 Java 侧截止时间配置。 */
    private final Deadlines deadlines;

    /** 可选的本方出牌执行端口；为 null 时保持只读建议模式。 */
    private final CardPlayExecutionPort executionPort;

    /** 单次出牌执行任务的 Java 侧硬截止时间，单位为毫秒；0 表示未启用执行链。 */
    private final long executionExecuteMs;

    /** 是否自动点击出牌提交按钮；false 时只选牌供用户手工确认。 */
    private final boolean executionAutoSubmit;

    /** 是否允许正式出牌执行；局前执行 Worker 启动时仍必须保持该能力关闭。 */
    private final boolean executionCardPlayEnabled;

    /** 是否允许正式模型明确 PASS 后自动点击；普通出牌仍受 executionCardPlayEnabled 控制。 */
    private final boolean executionAutoPassEnabled;

    /** 当前可观察阶段；实际修改只发生在事件执行器。 */
    private volatile GamePhase phase = GamePhase.WAIT_NEW_GAME;

    /** 编排器是否已启动且允许继续签发任务。 */
    private volatile boolean running;

    /** 当前唯一牌局会话；WAIT_NEW_GAME 中为空。 */
    private GameSession session;

    /** 管理无局态的新局识别、超时与重试。 */
    private final WaitNewGameCoordinator waitNewGameCoordinator;

    /** 管理局前提示、局前建议和完整牌局初始化三条并行支路。 */
    private final PreplayCoordinator preplayCoordinator;

    /** 管理正式出牌阶段的本方回合识别、历史对账和只读决策。 */
    private final PlayingCoordinator playingCoordinator;

    /**
     * 创建不包含框架依赖的业务编排器；默认只读建议模式，不自动出牌。
     *
     * @param eventExecutor 必须串行执行回调的单线程执行器
     * @param decisionExecutor 专门承载同步模型调用的执行器
     * @param timeoutScheduler Java 侧硬超时调度器
     */
    public GameOrchestrator(
            NewGameRecognitionPort newGamePort,
            PreplayRecognitionPort preplayRecognitionPort,
            DealRecognitionPort dealPort,
            LocalTurnRecognitionPort localTurnPort,
            TurnEndRecognitionPort turnEndPort,
            SettlementWatchPort settlementPort,
            PreplayDecisionPort preplayDecisionPort,
            DecisionPort decisionPort,
            GameRuntimeObserver observer,
            RuntimeIdentitySource identitySource,
            GameSessionFactory sessionFactory,
            Executor eventExecutor,
            ExecutorService decisionExecutor,
            ScheduledExecutorService timeoutScheduler,
            Deadlines deadlines
    ) {
        this(newGamePort, preplayRecognitionPort, dealPort, localTurnPort, turnEndPort,
                settlementPort, preplayDecisionPort, decisionPort, observer, identitySource,
                sessionFactory, eventExecutor, decisionExecutor, timeoutScheduler, deadlines,
                null, 0, false, false, null, 0);
    }

    /**
     * 创建带可选自动出牌执行端口的业务编排器。
     *
     * @param executionPort 本方出牌执行端口；为 null 时保持只读建议模式
     * @param executionExecuteMs 单次出牌执行硬截止时间，单位为毫秒；0 表示未启用
     * @param executionAutoSubmit 是否自动点击出牌提交按钮；false 时只选牌供用户手工确认
     */
    public GameOrchestrator(
            NewGameRecognitionPort newGamePort,
            PreplayRecognitionPort preplayRecognitionPort,
            DealRecognitionPort dealPort,
            LocalTurnRecognitionPort localTurnPort,
            TurnEndRecognitionPort turnEndPort,
            SettlementWatchPort settlementPort,
            PreplayDecisionPort preplayDecisionPort,
            DecisionPort decisionPort,
            GameRuntimeObserver observer,
            RuntimeIdentitySource identitySource,
            GameSessionFactory sessionFactory,
            Executor eventExecutor,
            ExecutorService decisionExecutor,
            ScheduledExecutorService timeoutScheduler,
            Deadlines deadlines,
            CardPlayExecutionPort executionPort,
            long executionExecuteMs,
            boolean executionAutoSubmit
    ) {
        this(newGamePort, preplayRecognitionPort, dealPort, localTurnPort, turnEndPort,
                settlementPort, preplayDecisionPort, decisionPort, observer, identitySource,
                sessionFactory, eventExecutor, decisionExecutor, timeoutScheduler, deadlines,
                executionPort, executionExecuteMs, executionAutoSubmit,
                executionPort != null, false, null, 0);
    }

    /** 创建同时支持正式出牌和局前正向按钮点击的业务编排器。 */
    public GameOrchestrator(
            NewGameRecognitionPort newGamePort,
            PreplayRecognitionPort preplayRecognitionPort,
            DealRecognitionPort dealPort,
            LocalTurnRecognitionPort localTurnPort,
            TurnEndRecognitionPort turnEndPort,
            SettlementWatchPort settlementPort,
            PreplayDecisionPort preplayDecisionPort,
            DecisionPort decisionPort,
            GameRuntimeObserver observer,
            RuntimeIdentitySource identitySource,
            GameSessionFactory sessionFactory,
            Executor eventExecutor,
            ExecutorService decisionExecutor,
            ScheduledExecutorService timeoutScheduler,
            Deadlines deadlines,
            CardPlayExecutionPort executionPort,
            long executionExecuteMs,
            boolean executionAutoSubmit,
            boolean executionCardPlayEnabled,
            PreplayButtonExecutionPort preplayExecutionPort,
            long preplayExecutionMs
    ) {
        this(newGamePort, preplayRecognitionPort, dealPort, localTurnPort, turnEndPort,
                settlementPort, preplayDecisionPort, decisionPort, observer, identitySource,
                sessionFactory, eventExecutor, decisionExecutor, timeoutScheduler, deadlines,
                executionPort, executionExecuteMs, executionAutoSubmit, executionCardPlayEnabled,
                false, preplayExecutionPort, preplayExecutionMs);
    }

    /** 创建同时支持正式出牌、固定自动不出和局前正向按钮点击的业务编排器。 */
    public GameOrchestrator(
            NewGameRecognitionPort newGamePort,
            PreplayRecognitionPort preplayRecognitionPort,
            DealRecognitionPort dealPort,
            LocalTurnRecognitionPort localTurnPort,
            TurnEndRecognitionPort turnEndPort,
            SettlementWatchPort settlementPort,
            PreplayDecisionPort preplayDecisionPort,
            DecisionPort decisionPort,
            GameRuntimeObserver observer,
            RuntimeIdentitySource identitySource,
            GameSessionFactory sessionFactory,
            Executor eventExecutor,
            ExecutorService decisionExecutor,
            ScheduledExecutorService timeoutScheduler,
            Deadlines deadlines,
            CardPlayExecutionPort executionPort,
            long executionExecuteMs,
            boolean executionAutoSubmit,
            boolean executionCardPlayEnabled,
            boolean executionAutoPassEnabled,
            PreplayButtonExecutionPort preplayExecutionPort,
            long preplayExecutionMs
    ) {
        this.settlementPort = Objects.requireNonNull(settlementPort, "结算端口不能为空");
        this.waitNewGameCoordinator = new WaitNewGameCoordinator(
                Objects.requireNonNull(newGamePort, "新局识别端口不能为空"));
        this.preplayCoordinator = new PreplayCoordinator(
                Objects.requireNonNull(preplayRecognitionPort, "局前识别端口不能为空"),
                Objects.requireNonNull(dealPort, "牌局初始化端口不能为空"),
                Objects.requireNonNull(preplayDecisionPort, "局前决策端口不能为空"),
                preplayExecutionPort, preplayExecutionMs);
        this.playingCoordinator = new PlayingCoordinator(
                Objects.requireNonNull(localTurnPort, "本方回合端口不能为空"),
                Objects.requireNonNull(turnEndPort, "回合结束端口不能为空"),
                Objects.requireNonNull(decisionPort, "正式决策端口不能为空"));
        this.observer = Objects.requireNonNull(observer, "运行时观察器不能为空");
        this.identitySource = Objects.requireNonNull(identitySource, "身份来源不能为空");
        this.sessionFactory = Objects.requireNonNull(sessionFactory, "会话工厂不能为空");
        this.eventExecutor = Objects.requireNonNull(eventExecutor, "事件执行器不能为空");
        this.decisionExecutor = Objects.requireNonNull(decisionExecutor, "决策执行器不能为空");
        this.timeoutScheduler = Objects.requireNonNull(timeoutScheduler, "超时调度器不能为空");
        this.deadlines = Objects.requireNonNull(deadlines, "截止时间配置不能为空");
        this.executionPort = executionPort;
        this.executionExecuteMs = executionExecuteMs;
        this.executionAutoSubmit = executionAutoSubmit;
        this.executionCardPlayEnabled = executionCardPlayEnabled;
        this.executionAutoPassEnabled = executionAutoPassEnabled;
        if ((executionPort == null) != (executionExecuteMs == 0)) {
            throw new IllegalArgumentException(
                    "执行端口和截止时间必须同时配置或同时为空");
        }
        if ((preplayExecutionPort == null) != (preplayExecutionMs == 0)) {
            throw new IllegalArgumentException(
                    "局前执行端口和截止时间必须同时配置或同时为空");
        }
    }

    /** 幂等启动运行时并进入 WAIT_NEW_GAME；实际启动动作投递到事件执行器。 */
    public void start() {
        try {
            eventExecutor.execute(() -> {
                if (running) {
                    return;
                }
                running = true;
                waitNewGameCoordinator.enter();
            });
        } catch (RuntimeException error) {
            LOGGER.error("牌局编排器启动事件入队失败", error);
            throw error;
        }
    }

    /** 幂等停止运行时，取消当前任务且不再自动等待下一局。 */
    public void stop() {
        stopAsync();
    }

    /**
     * 异步停止运行时，并在事件线程完成任务取消和上下文关闭后结束 CompletionStage。
     *
     * @return 可供 Spring 销毁阶段等待的停止完成信号
     */
    public CompletionStage<Void> stopAsync() {
        CompletableFuture<Void> stopped = new CompletableFuture<>();
        try {
            eventExecutor.execute(() -> {
                try {
                    if (!running) {
                        stopped.complete(null);
                        return;
                    }
                    running = false;
                    waitNewGameCoordinator.cancel();
                    if (session != null) {
                        session.close();
                        session = null;
                    }
                    setPhase(GamePhase.WAIT_NEW_GAME, null);
                    stopped.complete(null);
                } catch (RuntimeException error) {
                    LOGGER.error("停止牌局编排器失败 phase={}", phase, error);
                    stopped.completeExceptionally(error);
                }
            });
        } catch (RuntimeException error) {
            LOGGER.error("牌局编排器停止事件入队失败 phase={}", phase, error);
            stopped.completeExceptionally(error);
        }
        return stopped;
    }

    /** 返回最近一次由事件线程发布的阶段，主要供诊断和只读状态展示。 */
    public GamePhase phase() {
        return phase;
    }

    /** 返回编排器是否仍允许签发新的业务任务。 */
    public boolean isRunning() {
        return running;
    }

    private void startSettlementWatch(GameSession expectedSession) {
        GameTaskIdentity identity = taskIdentity(expectedSession);
        SettlementWatchPort.Request request = new SettlementWatchPort.Request(
                identity, deadlines.settlementMs());
        RecognitionJob<SettlementWatchPort.Result> job;
        try {
            job = settlementPort.watch(request);
        } catch (RuntimeException error) {
            LOGGER.error("提交结算监控失败 requestId={} dealId={} generation={}",
                    identity.requestId(), identity.dealId(), identity.generation(), error);
            finishIfCurrent(expectedSession, FinishReason.RECOGNITION_FAILED);
            return;
        }
        if (!identity.requestId().equals(job.requestId())) {
            LOGGER.error("结算监控任务身份不匹配 expectedRequestId={} actualRequestId={} "
                            + "dealId={} generation={}",
                    identity.requestId(), job.requestId(), identity.dealId(),
                    identity.generation());
            cancelMismatchedJob(job, "结算监控", identity);
            finishIfCurrent(expectedSession, FinishReason.RECOGNITION_FAILED);
            return;
        }
        ScheduledFuture<?> timeout = scheduleTimeout(
                () -> onSettlementTimeout(expectedSession, identity), deadlines.settlementMs());
        expectedSession.installSettlementJob(job, timeout);
        funnel(job, (result, error) -> onSettlementCompleted(
                expectedSession, identity, result, error));
    }

    private void onSettlementCompleted(
            GameSession expectedSession,
            GameTaskIdentity identity,
            SettlementWatchPort.Result result,
            Throwable error
    ) {
        if (!isCurrentSession(expectedSession)) {
            logStaleCallback("结算监控", identity);
            return;
        }
        if (!expectedSession.isSettlementJob(identity.requestId())) {
            logStaleCallback("结算监控", identity);
            return;
        }
        if (result != null && !matches(identity, result)) {
            logMismatchedResult("结算监控", identity, result);
            return;
        }
        expectedSession.completeSettlementJob(identity.requestId());
        if (error != null) {
            LOGGER.error("结算监控异常完成 requestId={} dealId={} generation={}",
                    identity.requestId(), identity.dealId(), identity.generation(), error);
            finishCurrentGame(FinishReason.RECOGNITION_FAILED);
        } else if (result instanceof SettlementWatchPort.Detected) {
            setPhase(GamePhase.SETTLEMENT, expectedSession.dealId());
            finishCurrentGame(FinishReason.SETTLEMENT_DETECTED);
        } else if (result instanceof SettlementWatchPort.Failed failed) {
            boolean retryable = isRetryableSettlementFailure(failed.failure().code());
            logRecognitionFailure("结算监控", identity, failed.failure(), retryable);
            if (retryable) {
                startSettlementWatch(expectedSession);
            } else {
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
            }
        } else {
            LOGGER.error("结算监控返回空或未知结果 requestId={} dealId={} generation={}",
                    identity.requestId(), identity.dealId(), identity.generation());
            finishCurrentGame(FinishReason.RECOGNITION_FAILED);
        }
    }

    private void onSettlementTimeout(GameSession expectedSession, GameTaskIdentity identity) {
        if (isCurrentSession(expectedSession)
                && expectedSession.isSettlementJob(identity.requestId())) {
            LOGGER.warn("结算监控达到 Java 截止时间，重新监听 requestId={} dealId={} "
                            + "generation={} deadlineMs={}",
                    identity.requestId(), identity.dealId(), identity.generation(),
                    deadlines.settlementMs());
            expectedSession.cancelSettlementJob();
            startSettlementWatch(expectedSession);
        }
    }

    private <T> void submitDecision(
            GameSession expectedSession,
            GameTaskIdentity identity,
            long deadlineMs,
            Callable<T> call,
            BiConsumer<T, Throwable> completion
    ) {
        FutureTask<T> task = new FutureTask<>(call) {
            @Override
            protected void done() {
                T result = null;
                Throwable error = null;
                try {
                    result = get();
                } catch (CancellationException cancelled) {
                    error = cancelled;
                } catch (ExecutionException failed) {
                    error = failed.getCause();
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    error = interrupted;
                }
                T finalResult = result;
                Throwable finalError = error;
                try {
                    eventExecutor.execute(() -> completion.accept(finalResult, finalError));
                } catch (RuntimeException rejected) {
                    LOGGER.error("模型完成事件入队失败 requestId={} dealId={} generation={}",
                            identity.requestId(), identity.dealId(), identity.generation(),
                            rejected);
                }
            }
        };
        ScheduledFuture<?> timeout = scheduleTimeout(
                () -> onDecisionTimeout(expectedSession, identity), deadlineMs);
        expectedSession.installDecision(identity, task, timeout);
        try {
            decisionExecutor.execute(task);
        } catch (RuntimeException rejected) {
            completion.accept(null, rejected);
        }
    }

    private void onDecisionTimeout(GameSession expectedSession, GameTaskIdentity identity) {
        if (!isCurrentSession(expectedSession) || !expectedSession.isDecision(identity)) {
            return;
        }
        LOGGER.warn("模型决策达到 Java 截止时间 requestId={} dealId={} generation={} phase={}",
                identity.requestId(), identity.dealId(), identity.generation(), phase);
        expectedSession.cancelDecision();
        if (phase == GamePhase.PLAYING) {
            playingCoordinator.requestLocalTurn(expectedSession);
        }
    }

    private void finishIfCurrent(GameSession expectedSession, FinishReason reason) {
        if (isCurrentSession(expectedSession)) {
            finishCurrentGame(reason);
        }
    }

    private void finishCurrentGame(FinishReason reason) {
        if (session == null) {
            return;
        }
        String dealId = session.dealId();
        session.close();
        session = null;
        notifyObserver(() -> observer.onGameFinished(dealId, reason));
        if (running) {
            waitNewGameCoordinator.enter();
        } else {
            setPhase(GamePhase.WAIT_NEW_GAME, null);
        }
    }

    private boolean isCurrentSession(GameSession expectedSession) {
        return running && session == expectedSession && !expectedSession.isClosed();
    }

    private GameTaskIdentity taskIdentity(GameSession expectedSession) {
        return new GameTaskIdentity(
                identitySource.nextRequestId(),
                expectedSession.dealId(),
                expectedSession.generation()
        );
    }

    private void setPhase(GamePhase next, String dealId) {
        phase = next;
        notifyObserver(() -> observer.onPhaseChanged(next, dealId));
    }

    /**
     * 将识别任务在任意工作线程产生的完成信号，统一导流到事件执行器处理。
     *
     * <p>识别端口可能在 JSONL 读线程、超时线程或测试线程完成任务；业务回调必须进入
     * {@code eventExecutor} 串行执行，才能与阶段切换、任务取消和身份校验保持同一顺序，
     * 避免并发修改编排器状态。这里不解释成功或失败，只原样转交结果与异常。</p>
     *
     * @param job 异步识别任务
     * @param completion 进入事件线程后执行的业务完成回调
     * @param <R> 该识别端口的结果类型
     */
    private <R> void funnel(
            RecognitionJob<R> job,
            BiConsumer<R, Throwable> completion
    ) {
        job.completion().whenComplete((result, error) -> {
            try {
                eventExecutor.execute(() -> completion.accept(result, error));
            } catch (RuntimeException rejected) {
                LOGGER.error("识别完成事件入队失败 requestId={}", job.requestId(), rejected);
            }
        });
    }

    private ScheduledFuture<?> scheduleTimeout(Runnable callback, long delayMs) {
        try {
            return timeoutScheduler.schedule(() -> {
                try {
                    eventExecutor.execute(callback);
                } catch (RuntimeException rejected) {
                    LOGGER.error("超时事件入队失败 delayMs={}", delayMs, rejected);
                }
            }, delayMs, TimeUnit.MILLISECONDS);
        } catch (RuntimeException rejected) {
            LOGGER.error("调度 Java 截止任务失败 delayMs={}", delayMs, rejected);
            throw rejected;
        }
    }

    /** 管理 WAIT_NEW_GAME 阶段唯一的新局识别任务及其恢复。 */
    private final class WaitNewGameCoordinator {
        /** 仅供 WAIT_NEW_GAME 阶段使用的新局边界识别能力。 */
        private final NewGameRecognitionPort newGamePort;

        /** 当前新局识别任务；该阶段尚未创建 GameSession。 */
        private RecognitionJob<NewGameRecognitionPort.Result> job;

        /** 当前新局任务的 Java 侧硬超时或提交失败后的重试唤醒。 */
        private ScheduledFuture<?> timeout;

        private WaitNewGameCoordinator(NewGameRecognitionPort newGamePort) {
            this.newGamePort = newGamePort;
        }

        private void enter() {
            if (!running) {
                return;
            }
            if (session != null) {
                session.close();
                session = null;
            }
            cancel();
            setPhase(GamePhase.WAIT_NEW_GAME, null);
            NewGameRecognitionPort.Request request = new NewGameRecognitionPort.Request(
                    identitySource.nextRequestId(), deadlines.newGameMs());
            RecognitionJob<NewGameRecognitionPort.Result> submitted;
            try {
                submitted = newGamePort.waitForNewGame(request);
            } catch (RuntimeException error) {
                LOGGER.error("提交新局识别失败 requestId={} retryDelayMs={}",
                        request.requestId(), deadlines.newGameMs(), error);
                scheduleRetry();
                return;
            }
            if (!request.requestId().equals(submitted.requestId())) {
                LOGGER.error("新局识别任务身份不匹配 expectedRequestId={} actualRequestId={} "
                                + "retryDelayMs={}",
                        request.requestId(), submitted.requestId(), deadlines.newGameMs());
                try {
                    submitted.cancel();
                } catch (RuntimeException cancelError) {
                    LOGGER.warn("取消身份不匹配的新局识别任务失败 requestId={}",
                            submitted.requestId(), cancelError);
                }
                scheduleRetry();
                return;
            }
            job = submitted;
            timeout = scheduleTimeout(
                    () -> onTimeout(request.requestId()), deadlines.newGameMs());
            funnel(submitted, (result, error) -> onCompleted(request, result, error));
        }

        private void onCompleted(
                NewGameRecognitionPort.Request request,
                NewGameRecognitionPort.Result result,
                Throwable error
        ) {
            if (!running || phase != GamePhase.WAIT_NEW_GAME
                    || job == null
                    || !job.requestId().equals(request.requestId())) {
                LOGGER.debug("忽略过期新局识别回调 requestId={} phase={} running={}",
                        request.requestId(), phase, running);
                return;
            }
            cancelFuture(timeout);
            timeout = null;
            job = null;
            if (error != null) {
                LOGGER.error("新局识别异常完成 requestId={}", request.requestId(), error);
                enter();
                return;
            }
            if (result instanceof NewGameRecognitionPort.Failed failed) {
                logNewGameFailure(request.requestId(), failed.failure());
                enter();
                return;
            }
            if (!(result instanceof NewGameRecognitionPort.Detected detected)) {
                LOGGER.error("新局识别返回空或未知结果 requestId={}", request.requestId());
                enter();
                return;
            }
            if (!request.requestId().equals(detected.requestId())) {
                LOGGER.error("新局识别结果身份不匹配 expectedRequestId={} actualRequestId={}",
                        request.requestId(), detected.requestId());
                enter();
                return;
            }
            preplayCoordinator.enter();
        }

        private void onTimeout(String requestId) {
            if (!running || job == null || !job.requestId().equals(requestId)) {
                return;
            }
            LOGGER.warn("新局识别达到 Java 截止时间 requestId={} deadlineMs={}",
                    requestId, deadlines.newGameMs());
            cancel();
            enter();
        }

        private void cancel() {
            if (job != null) {
                try {
                    job.cancel();
                } catch (RuntimeException error) {
                    LOGGER.warn("取消新局识别任务失败 requestId={}，继续清理运行状态",
                            job.requestId(), error);
                }
            }
            cancelFuture(timeout);
            job = null;
            timeout = null;
        }

        /**
         * 新局任务提交失败或返回任务身份不一致时，延迟重新进入新局等待流程。
         *
         * <p>这里只安排一次重试唤醒，不提前创建请求；真正重试时由 {@link #enter()}
         * 生成新的 requestId，避免复用失败任务的身份。回调执行前再次核对运行状态、
         * 业务阶段和当前任务，防止旧的定时回调重复提交识别。</p>
         */
        private void scheduleRetry() {
            // 同一字段只保存 WAIT_NEW_GAME 阶段唯一的定时任务，避免并存多个重试唤醒。
            cancelFuture(timeout);
            timeout = scheduleTimeout(() -> {
                if (running && phase == GamePhase.WAIT_NEW_GAME && job == null) {
                    // 先清空唤醒任务，再由统一入口安装全新识别任务及其硬超时。
                    timeout = null;
                    enter();
                }
            }, deadlines.newGameMs());
        }
    }

    /** 管理 PREPLAY 阶段的发牌识别、局前提示循环和可选正向按钮执行。 */
    private final class PreplayCoordinator {
        /** 仅供 PREPLAY 阶段使用的局前提示识别能力。 */
        private final PreplayRecognitionPort preplayRecognitionPort;

        /** 仅供 PREPLAY 阶段使用的完整牌局初始化能力。 */
        private final DealRecognitionPort dealPort;

        /** 仅供 PREPLAY 阶段使用的局前模型能力。 */
        private final PreplayDecisionPort preplayDecisionPort;

        /** 仅在用户明确开启时执行叫地主、抢地主和加倍正向按钮。 */
        private final PreplayButtonExecutionPort preplayExecutionPort;

        /** 单次局前按钮执行截止时间。 */
        private final long preplayExecutionMs;

        private PreplayCoordinator(
                PreplayRecognitionPort preplayRecognitionPort,
                DealRecognitionPort dealPort,
                PreplayDecisionPort preplayDecisionPort,
                PreplayButtonExecutionPort preplayExecutionPort,
                long preplayExecutionMs
        ) {
            this.preplayRecognitionPort = preplayRecognitionPort;
            this.dealPort = dealPort;
            this.preplayDecisionPort = preplayDecisionPort;
            this.preplayExecutionPort = preplayExecutionPort;
            this.preplayExecutionMs = preplayExecutionMs;
        }

        private void enter() {
            String dealId = identitySource.nextDealId();
            long generation = identitySource.nextGeneration();
            GameSession createdSession;
            try {
                createdSession = sessionFactory.create(dealId, generation);
            } catch (RuntimeException error) {
                LOGGER.error("创建牌局会话失败 dealId={} generation={}",
                        dealId, generation, error);
                throw error;
            }
            session = createdSession;
            setPhase(GamePhase.PREPLAY, dealId);
            startSettlementWatch(createdSession);
            if (!isCurrentSession(createdSession)) {
                return;
            }
            startDealRecognition(createdSession);
            if (!isCurrentSession(createdSession)) {
                return;
            }
            requestPrompt(createdSession);
        }

        private void startDealRecognition(GameSession expectedSession) {
            GameTaskIdentity identity = taskIdentity(expectedSession);
            DealRecognitionPort.Request request = new DealRecognitionPort.Request(
                    identity, deadlines.dealMs());
            RecognitionJob<DealRecognitionPort.Result> job;
            try {
                job = dealPort.recognizeDeal(request);
            } catch (RuntimeException error) {
                LOGGER.error("提交牌局初始化识别失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                finishIfCurrent(expectedSession, FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!identity.requestId().equals(job.requestId())) {
                LOGGER.error("牌局初始化任务身份不匹配 expectedRequestId={} actualRequestId={} "
                                + "dealId={} generation={}",
                        identity.requestId(), job.requestId(), identity.dealId(),
                        identity.generation());
                cancelMismatchedJob(job, "牌局初始化", identity);
                finishIfCurrent(expectedSession, FinishReason.RECOGNITION_FAILED);
                return;
            }
            ScheduledFuture<?> timeout = scheduleTimeout(
                    () -> onDealTimeout(expectedSession, identity), deadlines.dealMs());
            expectedSession.installDealJob(job, timeout);
            funnel(job, (result, error) ->
                    onDealCompleted(expectedSession, identity, result, error));
        }

        private void onDealCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                DealRecognitionPort.Result result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PREPLAY
                    || !expectedSession.isDealJob(identity.requestId())) {
                logStaleCallback("牌局初始化", identity);
                return;
            }
            if (result != null && !matches(identity, result)) {
                logMismatchedResult("牌局初始化", identity, result);
                return;
            }
            expectedSession.completeDealJob(identity.requestId());
            if (error != null) {
                LOGGER.error("牌局初始化识别异常完成 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (result instanceof DealRecognitionPort.Failed failed) {
                logRecognitionFailure("牌局初始化", identity, failed.failure(), false);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!(result instanceof DealRecognitionPort.Recognized recognized)) {
                LOGGER.error("牌局初始化识别返回空或未知结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            playingCoordinator.enter(expectedSession, recognized);
        }

        private void onDealTimeout(GameSession expectedSession, GameTaskIdentity identity) {
            if (isCurrentSession(expectedSession)
                    && phase == GamePhase.PREPLAY
                    && expectedSession.isDealJob(identity.requestId())) {
                LOGGER.warn("牌局初始化达到 Java 截止时间 requestId={} dealId={} generation={} "
                                + "deadlineMs={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        deadlines.dealMs());
                finishCurrentGame(FinishReason.STATE_TIMEOUT);
            }
        }

        private void requestPrompt(GameSession expectedSession) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PREPLAY
                    || expectedSession.isPreplayAdviceClosed()) {
                return;
            }
            PreplayRecognitionPort.Request request;
            try {
                request = expectedSession.preplayContext().preparePrompt(
                        identitySource.nextRequestId(), deadlines.preplayRecognitionMs());
            } catch (RuntimeException error) {
                LOGGER.error("创建局前提示请求失败 dealId={} generation={}",
                        expectedSession.dealId(), expectedSession.generation(), error);
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            RecognitionJob<PreplayRecognitionPort.Result> job;
            try {
                job = preplayRecognitionPort.waitForPrompt(request);
            } catch (RuntimeException error) {
                LOGGER.warn("提交局前提示识别失败 requestId={} dealId={} generation={}",
                        request.identity().requestId(), request.identity().dealId(),
                        request.identity().generation(), error);
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            if (!request.identity().requestId().equals(job.requestId())) {
                LOGGER.error("局前提示任务身份不匹配 expectedRequestId={} actualRequestId={} "
                                + "dealId={} generation={}",
                        request.identity().requestId(), job.requestId(),
                        request.identity().dealId(), request.identity().generation());
                cancelMismatchedJob(job, "局前提示", request.identity());
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            ScheduledFuture<?> timeout = scheduleTimeout(
                    () -> onPromptTimeout(expectedSession, request.identity()),
                    deadlines.preplayRecognitionMs());
            expectedSession.installCurrentStateJob(job, timeout);
            funnel(job, (result, error) -> onPromptCompleted(
                    expectedSession, request.identity(), result, error));
        }

        private void onPromptCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                PreplayRecognitionPort.Result result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PREPLAY
                    || !expectedSession.isCurrentStateJob(identity.requestId())) {
                logStaleCallback("局前提示", identity);
                return;
            }
            if (result != null && !matches(identity, result)) {
                logMismatchedResult("局前提示", identity, result);
                return;
            }
            expectedSession.completeCurrentStateJob(identity.requestId());
            if (error != null) {
                LOGGER.warn("局前提示识别异常完成 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            if (result instanceof PreplayRecognitionPort.Failed failed) {
                logRecognitionFailure("局前提示", identity, failed.failure(), true);
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            if (!(result instanceof PreplayRecognitionPort.Ready ready)) {
                LOGGER.error("局前提示识别返回空或未知结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            PreplaySnapshot snapshot;
            try {
                snapshot = expectedSession.preplayContext().observe(ready);
            } catch (RuntimeException invalid) {
                LOGGER.error("应用局前提示事实失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), invalid);
                expectedSession.closePreplayAdviceBranch();
                return;
            }
            // 叫地主和抢地主后都可能立刻出现下一张本方局前提示，先挂严格边沿任务，
            // 避免模型调用期间漏掉短促的抢地主或加倍阶段；正式发牌确认后会取消该任务。
            if (snapshot.stage() == PreplayStage.CALL_LANDLORD
                    || snapshot.stage() == PreplayStage.ROB_LANDLORD) {
                requestPrompt(expectedSession);
            }
            if (!expectedSession.isPreplayAdviceClosed()) {
                submitDecision(expectedSession, snapshot);
            }
        }

        private void onPromptTimeout(
                GameSession expectedSession,
                GameTaskIdentity identity
        ) {
            if (!isCurrentSession(expectedSession)
                    || phase != GamePhase.PREPLAY
                    || !expectedSession.isCurrentStateJob(identity.requestId())) {
                return;
            }
            LOGGER.warn("局前提示识别达到 Java 截止时间 requestId={} dealId={} generation={} "
                            + "deadlineMs={}，关闭本局局前建议分支",
                    identity.requestId(), identity.dealId(), identity.generation(),
                    deadlines.preplayRecognitionMs());
            expectedSession.closePreplayAdviceBranch();
        }

        private void submitDecision(GameSession expectedSession, PreplaySnapshot snapshot) {
            GameTaskIdentity identity = taskIdentity(expectedSession);
            PreplayQuery query = new PreplayQuery(
                    identity, deadlines.preplayDecisionMs(), snapshot);
            GameOrchestrator.this.submitDecision(
                    expectedSession,
                    identity,
                    deadlines.preplayDecisionMs(),
                    () -> preplayDecisionPort.decide(query),
                    (result, error) -> onDecisionCompleted(
                            expectedSession, identity, snapshot, result, error)
            );
        }

        private void onDecisionCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                PreplaySnapshot snapshot,
                PreplayResult result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession)
                    || phase != GamePhase.PREPLAY
                    || !expectedSession.isDecision(identity)) {
                logStaleCallback("局前决策", identity);
                return;
            }
            expectedSession.completeDecision(identity);
            if (error != null) {
                LOGGER.warn("局前模型调用失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                return;
            }
            if (result == null) {
                LOGGER.error("局前模型返回空结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                return;
            }
            if (!identity.equals(result.identity())) {
                LOGGER.error("局前模型结果身份不匹配 expectedIdentity={} actualIdentity={}",
                        identity, result.identity());
                return;
            }
            if (result instanceof PreplayResult.Unavailable unavailable) {
                LOGGER.warn("局前模型无可用建议 requestId={} dealId={} generation={} modelId={} "
                                + "failure={} detail={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        unavailable.modelId(), unavailable.failure(), unavailable.detail());
                return;
            }
            if (result instanceof PreplayResult.Recommendation recommendation
                    && recommendation.action().stage() == snapshot.stage()
                    && snapshot.availableActions().contains(recommendation.action())) {
                notifyObserver(() -> observer.onPreplayAdvice(snapshot, recommendation));
                tryAutoPreplay(expectedSession, snapshot, recommendation);
            } else {
                LOGGER.error("局前模型建议不符合当前快照 requestId={} dealId={} generation={} "
                                + "snapshotStage={} resultType={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        snapshot.stage(), result.getClass().getSimpleName());
            }
            // 负向建议只发布给展示层，不创建执行请求；叫地主或抢地主后的下一提示已在识别 Ready 后立即挂起。
        }

        private void tryAutoPreplay(
                GameSession expectedSession,
                PreplaySnapshot snapshot,
                PreplayResult.Recommendation recommendation
        ) {
            PreplayAction action = recommendation.action();
            if (preplayExecutionPort == null
                    || action == PreplayAction.NO_CALL
                    || action == PreplayAction.NO_ROB
                    || action == PreplayAction.NO_DOUBLE) {
                if (action == PreplayAction.NO_CALL
                        || action == PreplayAction.NO_ROB
                        || action == PreplayAction.NO_DOUBLE) {
                    LOGGER.info("局前建议为被动动作，不执行点击 requestId={} dealId={} generation={} action={}",
                            recommendation.identity().requestId(), expectedSession.dealId(),
                            expectedSession.generation(), action);
                }
                return;
            }
            GameTaskIdentity identity = taskIdentity(expectedSession);
            PreplayButtonExecutionPort.ExecutionRequest request;
            try {
                request = new PreplayButtonExecutionPort.ExecutionRequest(
                        identity, snapshot.stage(), action, preplayExecutionMs);
            } catch (RuntimeException invalid) {
                LOGGER.error("创建局前按钮执行请求失败 requestId={} dealId={} generation={} action={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action, invalid);
                return;
            }
            RecognitionJob<PreplayButtonExecutionPort.ExecutionResult> job;
            try {
                job = preplayExecutionPort.execute(request);
            } catch (RuntimeException error) {
                LOGGER.warn("提交局前按钮执行失败 requestId={} dealId={} generation={} action={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action, error);
                return;
            }
            if (!identity.requestId().equals(job.requestId())) {
                LOGGER.error("局前按钮执行任务身份不匹配 expectedRequestId={} actualRequestId={}",
                        identity.requestId(), job.requestId());
                job.cancel();
                return;
            }
            funnel(job, (result, error) -> onPreplayExecutionCompleted(
                    expectedSession, identity, action, result, error));
        }

        private void onPreplayExecutionCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                PreplayAction action,
                PreplayButtonExecutionPort.ExecutionResult result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PREPLAY) {
                logStaleCallback("局前按钮执行", identity);
                return;
            }
            if (error != null) {
                LOGGER.warn("局前按钮执行异常完成 requestId={} dealId={} generation={} action={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action, error);
            } else if (result instanceof PreplayButtonExecutionPort.Executed executed) {
                LOGGER.info("局前按钮已确认 requestId={} dealId={} generation={} action={} verifiedAt={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action,
                        executed.verifiedAt());
            } else if (result instanceof PreplayButtonExecutionPort.ExecutionRejected rejected) {
                LOGGER.warn("局前按钮执行被拒绝 requestId={} dealId={} generation={} action={} failure={} detail={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action,
                        rejected.failure(), rejected.detail());
            } else if (result instanceof PreplayButtonExecutionPort.ExecutionUncertain uncertain) {
                LOGGER.warn("局前按钮执行不确定 requestId={} dealId={} generation={} action={} detail={}",
                        identity.requestId(), identity.dealId(), identity.generation(), action,
                        uncertain.detail());
            }
        }
    }

    /** 管理 PLAYING 阶段的回合识别、权威历史对账和正式只读建议。 */
    private final class PlayingCoordinator {
        /** 仅供 PLAYING 阶段使用的本方回合识别能力。 */
        private final LocalTurnRecognitionPort localTurnPort;

        /** 仅供 PLAYING 阶段使用的当前本方回合结束探测能力。 */
        private final TurnEndRecognitionPort turnEndPort;

        /** 仅供 PLAYING 阶段使用的正式出牌只读模型能力。 */
        private final DecisionPort decisionPort;

        private PlayingCoordinator(
                LocalTurnRecognitionPort localTurnPort,
                TurnEndRecognitionPort turnEndPort,
                DecisionPort decisionPort
        ) {
            this.localTurnPort = localTurnPort;
            this.turnEndPort = turnEndPort;
            this.decisionPort = decisionPort;
        }

        private void enter(
                GameSession expectedSession,
                DealRecognitionPort.Recognized recognized
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PREPLAY) {
                LOGGER.debug("忽略过期 PLAYING 阶段入口 dealId={} generation={} phase={}",
                        expectedSession.dealId(), expectedSession.generation(), phase);
                return;
            }
            expectedSession.cancelCurrentStateJob();
            expectedSession.cancelDecision();
            try {
                expectedSession.installGameContext(GameContext.from(recognized));
            } catch (RuntimeException error) {
                LOGGER.error("安装正式牌局上下文失败 requestId={} dealId={} generation={}",
                        recognized.identity().requestId(), recognized.identity().dealId(),
                        recognized.identity().generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            setPhase(GamePhase.PLAYING, expectedSession.dealId());
            requestLocalTurn(expectedSession);
        }

        private void requestLocalTurn(GameSession expectedSession) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PLAYING) {
                return;
            }
            LocalTurnRecognitionPort.Request request;
            try {
                request = expectedSession.gameContext().prepareLocalTurn(
                        identitySource.nextRequestId(), deadlines.localTurnMs());
            } catch (RuntimeException error) {
                LOGGER.error("创建本方回合识别请求失败 dealId={} generation={}",
                        expectedSession.dealId(), expectedSession.generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            RecognitionJob<LocalTurnRecognitionPort.Result> job;
            try {
                job = localTurnPort.waitUntilReady(request);
            } catch (RuntimeException error) {
                LOGGER.error("提交本方回合识别失败 requestId={} dealId={} generation={}",
                        request.identity().requestId(), request.identity().dealId(),
                        request.identity().generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!request.identity().requestId().equals(job.requestId())) {
                LOGGER.error("本方回合任务身份不匹配 expectedRequestId={} actualRequestId={} "
                                + "dealId={} generation={}",
                        request.identity().requestId(), job.requestId(),
                        request.identity().dealId(), request.identity().generation());
                cancelMismatchedJob(job, "本方回合", request.identity());
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            // 本方回合任务的 deadlineMs 已随请求交给 Python CV；Java 只持有任务身份，
            // 由 Python 返回完整成功或结构化失败，不能用同一业务截止抢先取消其生命周期。
            expectedSession.installCurrentStateJob(job, null);
            funnel(job, (result, error) -> onLocalTurnCompleted(
                    expectedSession, request.identity(), result, error));
        }

        private void onLocalTurnCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                LocalTurnRecognitionPort.Result result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PLAYING
                    || !expectedSession.isCurrentStateJob(identity.requestId())) {
                logStaleCallback("本方回合", identity);
                return;
            }
            if (result != null && !matches(identity, result)) {
                logMismatchedResult("本方回合", identity, result);
                return;
            }
            expectedSession.completeCurrentStateJob(identity.requestId());
            if (error != null) {
                LOGGER.error("本方回合识别异常完成 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (result instanceof LocalTurnRecognitionPort.Failed failed) {
                logRecognitionFailure("本方回合", identity, failed.failure(), false);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!(result instanceof LocalTurnRecognitionPort.Ready ready)) {
                LOGGER.error("本方回合识别返回空或未知结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            GameSnapshot snapshot;
            try {
                snapshot = expectedSession.gameContext().reconcile(ready).snapshot();
            } catch (RuntimeException invalid) {
                LOGGER.error("本方回合事实对账失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), invalid);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            notifyObserver(() -> observer.onPlayTurnStarted(snapshot));
            submitDecision(expectedSession, snapshot);
        }

        private void submitDecision(GameSession expectedSession, GameSnapshot snapshot) {
            GameTaskIdentity identity = taskIdentity(expectedSession);
            AdviceQuery query = new AdviceQuery(
                    identity.requestId(), identity.dealId(),
                    deadlines.playDecisionMs(), snapshot);
            GameOrchestrator.this.submitDecision(
                    expectedSession,
                    identity,
                    deadlines.playDecisionMs(),
                    () -> decisionPort.decide(query),
                    (result, error) -> onDecisionCompleted(
                            expectedSession, identity, snapshot, result, error)
            );
        }

        private void onDecisionCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                GameSnapshot snapshot,
                AdviceResult result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession)
                    || phase != GamePhase.PLAYING
                    || !expectedSession.isDecision(identity)) {
                logStaleCallback("正式出牌决策", identity);
                return;
            }
            expectedSession.completeDecision(identity);
            if (error != null) {
                LOGGER.warn("正式出牌模型调用失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
            } else if (result == null) {
                LOGGER.error("正式出牌模型返回空结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
            } else {
                if (result instanceof AdviceResult.Unavailable unavailable) {
                    LOGGER.warn("正式出牌模型无可用建议 requestId={} dealId={} generation={} "
                                    + "modelId={} failure={} detail={}",
                            identity.requestId(), identity.dealId(), identity.generation(),
                            unavailable.modelId(), unavailable.failure(), unavailable.detail());
                }
                notifyObserver(() -> observer.onPlayAdvice(snapshot, result));
            }
            // 建议已交付后，若启用自动出牌且有可用建议，先执行选牌+提交，再调度 TURN_END。
            // 自动出牌失败不中止牌局，只记日志后继续 TURN_END 检测，允许用户手工接管。
            if (executionPort != null && result instanceof AdviceResult.Recommendation recommendation
                    && shouldExecuteRecommendation(recommendation.action())) {
                tryAutoPlay(expectedSession, snapshot, recommendation);
            } else {
                requestTurnEnd(expectedSession);
            }
        }

        /**
         * 尝试自动出牌：单次 execute 调用完成选牌+（条件）提交。
         * autoSubmit=true 时 Python 自动点击提交；false 时只验证选牌，等待用户手工提交。
         * 无论成败都继续 TURN_END 检测，允许用户手工接管。
         */
        private void tryAutoPlay(
                GameSession expectedSession,
                GameSnapshot snapshot,
                AdviceResult.Recommendation recommendation
        ) {
            PlayAction action = recommendation.action();
            GameTaskIdentity identity = taskIdentity(expectedSession);
            CardPlayExecutionPort.ActionType actionType;
            CardSet recommendedCards;
            if (action instanceof PlayAction.Pass) {
                actionType = CardPlayExecutionPort.ActionType.PASS;
                recommendedCards = CardSet.empty();
            } else if (action instanceof PlayAction.Play playAction) {
                actionType = CardPlayExecutionPort.ActionType.PLAY;
                recommendedCards = playAction.cards();
            } else {
                LOGGER.error("自动出牌遇到未知动作类型 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                requestTurnEnd(expectedSession);
                return;
            }

            CardPlayExecutionPort.ExecutionRequest request =
                    new CardPlayExecutionPort.ExecutionRequest(
                            identity,
                            snapshot.localSeat(),
                            snapshot.hand(),
                            recommendedCards,
                            actionType,
                            actionType == CardPlayExecutionPort.ActionType.PASS
                                    ? executionAutoPassEnabled
                                    : executionAutoSubmit,
                            executionExecuteMs
                    );
            RecognitionJob<CardPlayExecutionPort.ExecutionResult> job;
            try {
                job = executionPort.execute(request);
            } catch (RuntimeException error) {
                LOGGER.error("自动出牌提交失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                requestTurnEnd(expectedSession);
                return;
            }
            if (!identity.requestId().equals(job.requestId())) {
                LOGGER.error("自动出牌任务身份不匹配 expectedRequestId={} actualRequestId={}",
                        identity.requestId(), job.requestId());
                job.cancel();
                requestTurnEnd(expectedSession);
                return;
            }
            funnel(job, (result, error) -> onExecuteCompleted(
                    expectedSession, identity, result, error));
        }

        /** 只有显式开启的普通选牌或固定自动不出，才能把模型建议变成执行任务。 */
        private boolean shouldExecuteRecommendation(PlayAction action) {
            return (action instanceof PlayAction.Pass && executionAutoPassEnabled)
                    || (action instanceof PlayAction.Play && executionCardPlayEnabled);
        }

        /** 出牌执行完成回调：记录结果后继续 TURN_END 检测。 */
        private void onExecuteCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                CardPlayExecutionPort.ExecutionResult result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PLAYING) {
                logStaleCallback("自动出牌", identity);
                return;
            }
            if (error != null) {
                LOGGER.warn("自动出牌异常完成 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
            } else if (result instanceof CardPlayExecutionPort.Executed executed) {
                LOGGER.info("自动出牌已确认 requestId={} dealId={} generation={} autoSubmit={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        executed.autoSubmitEcho());
            } else if (result instanceof CardPlayExecutionPort.ExecutionRejected rejected) {
                LOGGER.warn("自动出牌被拒绝 requestId={} dealId={} generation={} failure={} detail={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        rejected.failure(), rejected.detail());
            } else if (result instanceof CardPlayExecutionPort.ExecutionUncertain uncertain) {
                // fail-closed：不确定时必须重新识别，不能假设成功。
                LOGGER.warn("自动出牌不确定 requestId={} dealId={} generation={} detail={}",
                        identity.requestId(), identity.dealId(), identity.generation(),
                        uncertain.detail());
            } else {
                LOGGER.error("自动出牌返回未知结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
            }
            // 无论执行结果如何，都继续 TURN_END 检测：成功时确认回合离开，失败时让用户手工接管。
            requestTurnEnd(expectedSession);
        }

        private void requestTurnEnd(GameSession expectedSession) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PLAYING) {
                return;
            }
            TurnEndRecognitionPort.Request request;
            try {
                request = expectedSession.gameContext().prepareTurnEnd(
                        identitySource.nextRequestId(), deadlines.turnEndMs());
            } catch (RuntimeException error) {
                LOGGER.error("创建本方回合结束探测失败 dealId={} generation={}",
                        expectedSession.dealId(), expectedSession.generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            RecognitionJob<TurnEndRecognitionPort.Result> job;
            try {
                job = turnEndPort.waitUntilEnded(request);
            } catch (RuntimeException error) {
                LOGGER.error("提交本方回合结束探测失败 requestId={} dealId={} generation={}",
                        request.identity().requestId(), request.identity().dealId(),
                        request.identity().generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!request.identity().requestId().equals(job.requestId())) {
                LOGGER.error("回合结束任务身份不匹配 expectedRequestId={} actualRequestId={} "
                                + "dealId={} generation={}",
                        request.identity().requestId(), job.requestId(),
                        request.identity().dealId(), request.identity().generation());
                cancelMismatchedJob(job, "回合结束", request.identity());
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            expectedSession.installCurrentStateJob(job, null);
            funnel(job, (result, error) -> onTurnEndCompleted(
                    expectedSession, request.identity(), result, error));
        }

        private void onTurnEndCompleted(
                GameSession expectedSession,
                GameTaskIdentity identity,
                TurnEndRecognitionPort.Result result,
                Throwable error
        ) {
            if (!isCurrentSession(expectedSession) || phase != GamePhase.PLAYING
                    || !expectedSession.isCurrentStateJob(identity.requestId())) {
                logStaleCallback("回合结束", identity);
                return;
            }
            if (result != null && !matches(identity, result)) {
                logMismatchedResult("回合结束", identity, result);
                return;
            }
            expectedSession.completeCurrentStateJob(identity.requestId());
            if (error != null) {
                LOGGER.error("本方回合结束探测异常完成 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), error);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (result instanceof TurnEndRecognitionPort.Failed failed) {
                logRecognitionFailure("本方回合结束", identity, failed.failure(), false);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            if (!(result instanceof TurnEndRecognitionPort.Ended ended)) {
                LOGGER.error("回合结束探测返回空或未知结果 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation());
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            try {
                expectedSession.gameContext().completeTurnEnd(ended);
            } catch (RuntimeException invalid) {
                LOGGER.error("回合结束事实对账失败 requestId={} dealId={} generation={}",
                        identity.requestId(), identity.dealId(), identity.generation(), invalid);
                finishCurrentGame(FinishReason.RECOGNITION_FAILED);
                return;
            }
            requestLocalTurn(expectedSession);
        }
    }

    private static boolean isRetryableSettlementFailure(
            RecognitionFailureCode code
    ) {
        return code == RecognitionFailureCode.DEADLINE_EXCEEDED
                || code == RecognitionFailureCode.OBSERVATION_UNCERTAIN;
    }

    private static boolean matches(GameTaskIdentity expected, Object result) {
        return expected.equals(resultIdentity(result));
    }

    private static void logNewGameFailure(
            String requestId,
            RecognitionFailure failure
    ) {
        if (isWarningRecognitionFailure(failure.code())) {
            LOGGER.warn("新局识别失败 requestId={} code={} detail={}",
                    requestId, failure.code(), failure.detail());
        } else {
            LOGGER.error("新局识别失败 requestId={} code={} detail={}",
                    requestId, failure.code(), failure.detail());
        }
    }

    private static void logRecognitionFailure(
            String task,
            GameTaskIdentity identity,
            RecognitionFailure failure,
            boolean retrying
    ) {
        String action = retrying ? "重试或等待" : "关闭分支或牌局";
        if (isWarningRecognitionFailure(failure.code())) {
            LOGGER.warn("{}识别失败 requestId={} dealId={} generation={} code={} detail={} action={}",
                    task, identity.requestId(), identity.dealId(), identity.generation(),
                    failure.code(), failure.detail(), action);
        } else {
            LOGGER.error("{}识别失败 requestId={} dealId={} generation={} code={} detail={} action={}",
                    task, identity.requestId(), identity.dealId(), identity.generation(),
                    failure.code(), failure.detail(), action);
        }
    }

    private static boolean isWarningRecognitionFailure(RecognitionFailureCode code) {
        return code == RecognitionFailureCode.DEADLINE_EXCEEDED
                || code == RecognitionFailureCode.OBSERVATION_UNCERTAIN
                || code == RecognitionFailureCode.TASK_CANCELLED;
    }

    private static void logStaleCallback(String task, GameTaskIdentity identity) {
        LOGGER.debug("忽略过期{}回调 requestId={} dealId={} generation={}",
                task, identity.requestId(), identity.dealId(), identity.generation());
    }

    private static void logMismatchedResult(
            String task,
            GameTaskIdentity expected,
            Object result
    ) {
        LOGGER.error("{}结果身份不匹配 expectedIdentity={} actualIdentity={} resultType={}",
                task, expected, resultIdentity(result), result.getClass().getSimpleName());
    }

    private static GameTaskIdentity resultIdentity(Object result) {
        if (result instanceof PreplayRecognitionPort.Result preplay) {
            return preplay.identity();
        }
        if (result instanceof DealRecognitionPort.Result deal) {
            return deal.identity();
        }
        if (result instanceof LocalTurnRecognitionPort.Result turn) {
            return turn.identity();
        }
        if (result instanceof TurnEndRecognitionPort.Result turnEnd) {
            return turnEnd.identity();
        }
        if (result instanceof SettlementWatchPort.Result settlement) {
            return settlement.identity();
        }
        return null;
    }

    private static void cancelMismatchedJob(
            RecognitionJob<?> job,
            String task,
            GameTaskIdentity identity
    ) {
        try {
            job.cancel();
        } catch (RuntimeException error) {
            LOGGER.warn("取消身份不匹配的{}任务失败 requestId={} dealId={} generation={}",
                    task, identity.requestId(), identity.dealId(), identity.generation(), error);
        }
    }

    private static void cancelFuture(java.util.concurrent.Future<?> future) {
        if (future != null) {
            try {
                future.cancel(true);
            } catch (RuntimeException error) {
                LOGGER.warn("取消运行时定时任务失败 futureType={}，继续清除持有关系",
                        future.getClass().getName(), error);
            }
        }
    }

    private static void notifyObserver(Runnable notification) {
        try {
            notification.run();
        } catch (RuntimeException error) {
            LOGGER.warn("运行时观察器回调失败，已隔离展示层异常", error);
        }
    }

    /**
     * Java 对各完整业务能力施加的硬截止时间。
     *
     * @param newGameMs 等待新局边界的最长时间，单位为毫秒
     * @param preplayRecognitionMs 单次局前提示识别的最长时间，单位为毫秒
     * @param dealMs 完整牌局初始化事实闭合的最长时间，单位为毫秒
     * @param settlementMs 整局结算旁路任务的最长存活时间，单位为毫秒
     * @param localTurnMs 等待下一次本方稳定回合快照的最长时间，单位为毫秒；由 Python CV 解释并返回最终成功或失败
     * @param turnEndMs 建议后等待当前本方回合结束的最长时间，单位为毫秒；由 Python CV 依据基线确认
     * @param preplayDecisionMs 单次局前模型调用的最长时间，单位为毫秒
     * @param playDecisionMs 单次正式出牌模型调用的最长时间，单位为毫秒
     */
    public record Deadlines(
            long newGameMs,
            long preplayRecognitionMs,
            long dealMs,
            long settlementMs,
            long localTurnMs,
            long turnEndMs,
            long preplayDecisionMs,
            long playDecisionMs
    ) {
        public Deadlines {
            if (newGameMs <= 0 || preplayRecognitionMs <= 0 || dealMs <= 0
                    || settlementMs <= 0 || localTurnMs <= 0 || turnEndMs <= 0
                    || preplayDecisionMs <= 0 || playDecisionMs <= 0) {
                throw new IllegalArgumentException("所有业务截止时间都必须为正数");
            }
        }
    }
}
