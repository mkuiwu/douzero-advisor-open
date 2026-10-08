package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;

import java.util.Objects;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledFuture;

/**
 * 一局运行资源的唯一容器；负责幂等取消识别任务、决策任务、超时及两个业务上下文。
 */
public final class GameSession implements AutoCloseable {
    /** 当前牌局标识，一局内不可变化。 */
    private final String dealId;

    /** 当前牌局代次，用于隔离迟到回调。 */
    private final long generation;

    /** 叫地主、抢地主和加倍提示的 Java 事实上下文。 */
    private final PreplayContext preplayContext;

    /** 正式牌局闭合后建立的唯一出牌状态；PREPLAY 中为空。 */
    private GameContext gameContext;

    /** 当前阶段前台识别任务；PREPLAY 为提示任务，PLAYING 为本方回合任务。 */
    private RecognitionJob<?> currentStateJob;

    /** 当前前台识别任务的 Java 侧硬超时。 */
    private ScheduledFuture<?> currentStateTimeout;

    /** 与局前提示并行执行的完整牌局初始化任务。 */
    private RecognitionJob<?> dealJob;

    /** 完整牌局初始化任务的 Java 侧硬超时。 */
    private ScheduledFuture<?> dealTimeout;

    /** 从 PREPLAY 持续到结算的整局结算旁路任务。 */
    private RecognitionJob<?> settlementJob;

    /** 结算旁路任务的 Java 侧硬超时。 */
    private ScheduledFuture<?> settlementTimeout;

    /** 当前同步模型调用在决策线程池中的 Future；局前和正式出牌共用一个槽位。 */
    private Future<?> decisionFuture;

    /** 当前模型调用身份，用于拒绝阶段切换后的迟到结果。 */
    private GameTaskIdentity decisionIdentity;

    /** 当前模型调用的 Java 侧硬超时。 */
    private ScheduledFuture<?> decisionTimeout;

    /** 当前局是否已经统一收口。 */
    private boolean closed;

    /** 局前建议支路是否已停止；停止后 Deal 和 Settlement 仍可继续。 */
    private boolean preplayAdviceClosed;

    /** 创建只含局前上下文、尚未锁定正式牌局事实的会话。 */
    public GameSession(String dealId, long generation) {
        if (dealId == null || dealId.isBlank()) {
            throw new IllegalArgumentException("会话牌局标识不能为空");
        }
        if (generation <= 0) {
            throw new IllegalArgumentException("会话牌局代次必须为正数");
        }
        this.dealId = dealId;
        this.generation = generation;
        this.preplayContext = new PreplayContext(dealId, generation);
    }

    /** 返回当前牌局标识。 */
    public String dealId() {
        return dealId;
    }

    /** 返回当前牌局代次。 */
    public long generation() {
        return generation;
    }

    /** 返回局前提示上下文。 */
    public PreplayContext preplayContext() {
        return preplayContext;
    }

    /** 返回正式出牌上下文；仅在 PLAYING 阶段可用。 */
    public synchronized GameContext gameContext() {
        if (gameContext == null) {
            throw new IllegalStateException("正式牌局上下文尚未建立");
        }
        return gameContext;
    }

    /** 安装正式牌局上下文并关闭局前事实入口。 */
    public synchronized void installGameContext(GameContext context) {
        requireOpen();
        if (gameContext != null) {
            throw new IllegalStateException("正式牌局上下文不能重复安装");
        }
        gameContext = Objects.requireNonNull(context, "正式牌局上下文不能为空");
        preplayContext.close();
        preplayAdviceClosed = true;
    }

    /** 返回本局局前建议支路是否已经停止。 */
    public synchronized boolean isPreplayAdviceClosed() {
        return preplayAdviceClosed;
    }

    /**
     * 幂等停止局前建议支路；识别失败时不关闭并行 Deal 和 Settlement。
     */
    public synchronized void closePreplayAdviceBranch() {
        if (preplayAdviceClosed) {
            return;
        }
        preplayAdviceClosed = true;
        cancelCurrentStateJob();
        cancelDecision();
        preplayContext.close();
    }

    /** 安装当前前台识别任务；超时可为空，表示该任务的业务截止由远端能力自行完成。 */
    public synchronized void installCurrentStateJob(
            RecognitionJob<?> job,
            ScheduledFuture<?> timeout
    ) {
        requireOpen();
        cancelCurrentStateJob();
        currentStateJob = Objects.requireNonNull(job, "当前状态任务不能为空");
        currentStateTimeout = timeout;
    }

    /** 返回请求是否仍是当前前台识别任务。 */
    public synchronized boolean isCurrentStateJob(String requestId) {
        return !closed && currentStateJob != null
                && currentStateJob.requestId().equals(requestId);
    }

    /** 当前任务正常完成时只清理持有关系和超时，不向已完成任务发送取消。 */
    public synchronized boolean completeCurrentStateJob(String requestId) {
        if (!isCurrentStateJob(requestId)) {
            return false;
        }
        cancelFuture(currentStateTimeout);
        currentStateTimeout = null;
        currentStateJob = null;
        return true;
    }

    /** 取消当前前台识别支路。 */
    public synchronized void cancelCurrentStateJob() {
        cancelJob(currentStateJob);
        cancelFuture(currentStateTimeout);
        currentStateJob = null;
        currentStateTimeout = null;
    }

    /** 安装完整牌局初始化任务及其硬超时。 */
    public synchronized void installDealJob(RecognitionJob<?> job, ScheduledFuture<?> timeout) {
        requireOpen();
        cancelDealJob();
        dealJob = Objects.requireNonNull(job, "牌局初始化任务不能为空");
        dealTimeout = Objects.requireNonNull(timeout, "牌局初始化超时不能为空");
    }

    /** 返回请求是否仍是当前牌局初始化任务。 */
    public synchronized boolean isDealJob(String requestId) {
        return !closed && dealJob != null && dealJob.requestId().equals(requestId);
    }

    /** 清理已正常完成的牌局初始化任务。 */
    public synchronized boolean completeDealJob(String requestId) {
        if (!isDealJob(requestId)) {
            return false;
        }
        cancelFuture(dealTimeout);
        dealTimeout = null;
        dealJob = null;
        return true;
    }

    /** 取消牌局初始化支路。 */
    public synchronized void cancelDealJob() {
        cancelJob(dealJob);
        cancelFuture(dealTimeout);
        dealJob = null;
        dealTimeout = null;
    }

    /** 安装整局结算旁路任务及其硬超时。 */
    public synchronized void installSettlementJob(
            RecognitionJob<?> job,
            ScheduledFuture<?> timeout
    ) {
        requireOpen();
        cancelSettlementJob();
        settlementJob = Objects.requireNonNull(job, "结算旁路任务不能为空");
        settlementTimeout = Objects.requireNonNull(timeout, "结算旁路超时不能为空");
    }

    /** 返回请求是否仍是当前结算旁路任务。 */
    public synchronized boolean isSettlementJob(String requestId) {
        return !closed && settlementJob != null
                && settlementJob.requestId().equals(requestId);
    }

    /** 清理已正常完成的结算旁路任务。 */
    public synchronized boolean completeSettlementJob(String requestId) {
        if (!isSettlementJob(requestId)) {
            return false;
        }
        cancelFuture(settlementTimeout);
        settlementTimeout = null;
        settlementJob = null;
        return true;
    }

    /** 取消结算旁路支路。 */
    public synchronized void cancelSettlementJob() {
        cancelJob(settlementJob);
        cancelFuture(settlementTimeout);
        settlementJob = null;
        settlementTimeout = null;
    }

    /** 安装当前模型任务及其身份和硬超时。 */
    public synchronized void installDecision(
            GameTaskIdentity identity,
            Future<?> future,
            ScheduledFuture<?> timeout
    ) {
        requireOpen();
        cancelDecision();
        decisionIdentity = Objects.requireNonNull(identity, "模型任务身份不能为空");
        decisionFuture = Objects.requireNonNull(future, "模型任务 Future 不能为空");
        decisionTimeout = Objects.requireNonNull(timeout, "模型任务超时不能为空");
    }

    /** 返回身份是否仍属于当前模型调用。 */
    public synchronized boolean isDecision(GameTaskIdentity identity) {
        return !closed && decisionIdentity != null && decisionIdentity.equals(identity);
    }

    /** 清理已经正常完成的模型任务。 */
    public synchronized boolean completeDecision(GameTaskIdentity identity) {
        if (!isDecision(identity)) {
            return false;
        }
        cancelFuture(decisionTimeout);
        decisionTimeout = null;
        decisionFuture = null;
        decisionIdentity = null;
        return true;
    }

    /** 取消当前模型调用；运行中的同步实现即使忽略中断，迟到结果也会因身份失效被拒绝。 */
    public synchronized void cancelDecision() {
        cancelFuture(decisionFuture);
        cancelFuture(decisionTimeout);
        decisionFuture = null;
        decisionIdentity = null;
        decisionTimeout = null;
    }

    /** 返回会话是否已经关闭。 */
    public synchronized boolean isClosed() {
        return closed;
    }

    /** 幂等取消所有局内任务和上下文。 */
    @Override
    public synchronized void close() {
        if (closed) {
            return;
        }
        closed = true;
        preplayAdviceClosed = true;
        cancelCurrentStateJob();
        cancelDealJob();
        cancelSettlementJob();
        cancelDecision();
        preplayContext.close();
        if (gameContext != null) {
            gameContext.close();
        }
    }

    private void requireOpen() {
        if (closed) {
            throw new IllegalStateException("牌局会话已经关闭");
        }
    }

    private static void cancelJob(RecognitionJob<?> job) {
        if (job != null) {
            try {
                job.cancel();
            } catch (RuntimeException ignored) {
                // 单个基础设施取消失败不能阻止同局其余任务和上下文继续收口。
            }
        }
    }

    private static void cancelFuture(Future<?> future) {
        if (future != null) {
            try {
                future.cancel(true);
            } catch (RuntimeException ignored) {
                // 决策或超时实现的取消异常不能泄漏到会话关闭路径。
            }
        }
    }
}
