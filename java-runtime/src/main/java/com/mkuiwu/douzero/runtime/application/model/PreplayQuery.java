package com.mkuiwu.douzero.runtime.application.model;

import java.util.Objects;

/**
 * Java 提交给局前模型的语义决策请求。
 *
 * @param identity 当前局前模型任务身份；每次提示必须使用新的 requestId
 * @param deadlineMs 局前模型允许的最长耗时，单位为毫秒
 * @param snapshot 已通过阶段和可用动作校验的局前快照
 */
public record PreplayQuery(
        GameTaskIdentity identity,
        long deadlineMs,
        PreplaySnapshot snapshot
) {
    public PreplayQuery {
        Objects.requireNonNull(identity, "局前决策身份不能为空");
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("局前决策截止时间必须为正数");
        }
        Objects.requireNonNull(snapshot, "局前决策快照不能为空");
        if (!identity.dealId().equals(snapshot.dealId())
                || identity.generation() != snapshot.generation()) {
            throw new IllegalArgumentException("局前决策身份与快照不一致");
        }
    }
}
