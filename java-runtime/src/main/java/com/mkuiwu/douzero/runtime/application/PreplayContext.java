package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;

import java.util.HashSet;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 单局局前提示上下文；只记录 Java 亲眼确认过的提示阶段，不记录模型建议为用户行为。
 */
public final class PreplayContext {
    /** 当前牌局标识，一局内不可变化。 */
    private final String dealId;

    /** 当前牌局代次，用于拒绝上一局迟到提示。 */
    private final long generation;

    /** 局内已经签发的提示任务标识，失败或取消后同样不得复用。 */
    private final Set<String> issuedRequestIds = new HashSet<>();

    /** 已成功应用的提示任务及其快照，保证重复 Ready 不重复推进提示事实。 */
    private final Map<String, PreplaySnapshot> appliedSnapshots = new HashMap<>();

    /** 当前唯一有效的局前提示任务标识。 */
    private String activeRequestId;

    /** 是否已经成功观察过至少一个本方局前提示。 */
    private boolean hasObservedPrompt;

    /** 本局是否真实观察过本方叫地主提示。 */
    private boolean callPromptSeen;

    /** 本局是否真实观察过本方抢地主提示。 */
    private boolean robPromptSeen;

    /** 最近一次成功确认的局前业务快照。 */
    private PreplaySnapshot latestSnapshot;

    /** 上下文是否已经因正式牌局闭合或牌局结束而关闭。 */
    private boolean closed;

    /** 创建尚未观察任何提示的局前上下文。 */
    public PreplayContext(String dealId, long generation) {
        if (dealId == null || dealId.isBlank()) {
            throw new IllegalArgumentException("局前上下文牌局标识不能为空");
        }
        if (generation <= 0) {
            throw new IllegalArgumentException("局前上下文代次必须为正数");
        }
        this.dealId = dealId;
        this.generation = generation;
    }

    /**
     * 签发当前唯一有效的提示识别任务。
     *
     * @param requestId 单次提示任务标识，局内不得复用
     * @param deadlineMs Python CV 交付完整提示的最长时间，单位为毫秒
     * @return 只包含业务身份和提示边沿要求的请求
     */
    public synchronized PreplayRecognitionPort.Request preparePrompt(
            String requestId,
            long deadlineMs
    ) {
        requireOpen();
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("局前提示任务标识不能为空");
        }
        if (issuedRequestIds.contains(requestId)) {
            throw new IllegalArgumentException("局前提示任务标识不能复用");
        }
        PreplayRecognitionPort.Request request = new PreplayRecognitionPort.Request(
                new GameTaskIdentity(requestId, dealId, generation),
                hasObservedPrompt
                        ? PreplayRecognitionPort.EntryMode.REQUIRE_CHANGE_OR_EXIT_THEN_NEW
                        : PreplayRecognitionPort.EntryMode.ACCEPT_CURRENT_STABLE_PROMPT,
                deadlineMs
        );
        issuedRequestIds.add(requestId);
        activeRequestId = requestId;
        return request;
    }

    /**
     * 接收当前提示结果并生成模型快照；建议结果不会再次写回本上下文。
     *
     * @param ready Python CV 交付的稳定局前提示
     * @return 已包含历史提示事实的不可变局前快照
     */
    public synchronized PreplaySnapshot observe(PreplayRecognitionPort.Ready ready) {
        requireOpen();
        Objects.requireNonNull(ready, "局前提示结果不能为空");
        GameTaskIdentity identity = ready.identity();
        if (!dealId.equals(identity.dealId()) || generation != identity.generation()) {
            throw new IllegalStateException("局前提示结果不属于当前牌局代次");
        }
        PreplaySnapshot applied = appliedSnapshots.get(identity.requestId());
        if (applied != null) {
            return applied;
        }
        if (!identity.requestId().equals(activeRequestId)) {
            throw new IllegalStateException("局前提示结果不是当前有效任务");
        }
        callPromptSeen |= ready.stage() == PreplayStage.CALL_LANDLORD;
        robPromptSeen |= ready.stage() == PreplayStage.ROB_LANDLORD;
        hasObservedPrompt = true;
        activeRequestId = null;
        latestSnapshot = new PreplaySnapshot(
                dealId,
                generation,
                ready.stage(),
                ready.hand(),
                ready.bottomCards(),
                ready.availableActions(),
                callPromptSeen,
                robPromptSeen
        );
        appliedSnapshots.put(identity.requestId(), latestSnapshot);
        return latestSnapshot;
    }

    /** 当前识别失败后清除任务占用，但保留已经观察到的阶段事实供后续重试。 */
    public synchronized void rejectPrompt(String requestId) {
        if (!closed && Objects.equals(activeRequestId, requestId)) {
            activeRequestId = null;
        }
    }

    /** 返回最近一次稳定提示快照；尚未观察到提示时返回 null。 */
    public synchronized PreplaySnapshot latestSnapshot() {
        return latestSnapshot;
    }

    /** 幂等关闭局前上下文；关闭后不再接受或签发提示任务。 */
    public synchronized void close() {
        if (closed) {
            return;
        }
        closed = true;
        activeRequestId = null;
    }

    /** 返回局前上下文是否已经关闭。 */
    public synchronized boolean isClosed() {
        return closed;
    }

    private void requireOpen() {
        if (closed) {
            throw new IllegalStateException("局前上下文已经关闭");
        }
    }
}
