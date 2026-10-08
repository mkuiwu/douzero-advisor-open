package com.mkuiwu.douzero.runtime.application.model;

import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import java.util.Objects;

/**
 * 控制层提交给模型端口的语义化建议请求。
 *
 * @param requestId 单次建议请求标识，用于拒绝迟到或串线响应
 * @param dealId 当前锁定牌局标识，用于隔离跨局响应
 * @param deadlineMs 本次建议允许的最长耗时，单位为毫秒
 * @param snapshot 已经通过状态完整性校验的牌局快照
 */
public record AdviceQuery(
        String requestId,
        String dealId,
        long deadlineMs,
        GameSnapshot snapshot
) {
    public AdviceQuery {
        requireText(requestId, "请求标识不能为空");
        requireText(dealId, "牌局标识不能为空");
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("建议截止时间必须为正数");
        }
        Objects.requireNonNull(snapshot, "牌局快照不能为空");
        if (!dealId.equals(snapshot.dealId())) {
            throw new IllegalArgumentException("建议请求与牌局快照身份不一致");
        }
        if (snapshot.phase() != GamePhase.PLAYING
                || snapshot.currentSeat() != snapshot.localSeat()) {
            throw new IllegalArgumentException("只有本方正式出牌回合才能请求建议");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
