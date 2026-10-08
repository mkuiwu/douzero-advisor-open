package com.mkuiwu.douzero.runtime.contract.model.douzero;

import java.util.List;
import java.util.Objects;

/**
 * Java 调用默认 DouZero 模型的完整线协议请求。
 *
 * @param contractVersion DouZero 请求协议版本，当前固定为 inference.v1
 * @param requestId 单次调用标识，用于关联响应、超时和重试
 * @param dealId 当前锁定牌局标识，用于隔离跨局响应
 * @param modelId 模型标识，固定为 original
 * @param deadlineMs 本次推理允许的最长耗时，单位为毫秒
 * @param position 本方相对座位，取 landlord、landlord_down 或 landlord_up
 * @param hand 本方当前手牌的 DouZero 整数编码
 * @param bottomCards 本局三张底牌的 DouZero 整数编码
 * @param actionHistory 从地主首轮开始按行动顺序排列的完整动作；空动作表示不出，Python 据此计算合法动作
 */
public record DouZeroRequest(
        String contractVersion,
        String requestId,
        String dealId,
        String modelId,
        long deadlineMs,
        String position,
        List<Integer> hand,
        List<Integer> bottomCards,
        List<List<Integer>> actionHistory
) {
    public DouZeroRequest {
        requireText(contractVersion, "协议版本不能为空");
        requireText(requestId, "请求标识不能为空");
        requireText(dealId, "牌局标识不能为空");
        if (!"original".equals(modelId)) {
            throw new IllegalArgumentException("默认 DouZero 请求的模型标识必须为 original");
        }
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("模型截止时间必须为正数");
        }
        requireText(position, "本方座位不能为空");
        hand = List.copyOf(Objects.requireNonNull(hand, "手牌不能为空"));
        bottomCards = List.copyOf(Objects.requireNonNull(bottomCards, "底牌不能为空"));
        if (bottomCards.size() != 3) {
            throw new IllegalArgumentException("默认 DouZero 请求必须包含三张底牌");
        }
        actionHistory = copyActions(actionHistory, "动作历史不能为空");
    }

    private static List<List<Integer>> copyActions(List<List<Integer>> actions, String message) {
        Objects.requireNonNull(actions, message);
        return actions.stream().map(List::copyOf).toList();
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
