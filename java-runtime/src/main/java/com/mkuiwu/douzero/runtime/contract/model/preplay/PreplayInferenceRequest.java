package com.mkuiwu.douzero.runtime.contract.model.preplay;

import java.util.List;
import java.util.Objects;

/**
 * Java 调用局前模型的唯一扁平线协议请求。
 *
 * @param contractVersion 局前模型协议版本，当前固定为 preplay-inference.v1
 * @param requestId 单次局前决策标识
 * @param dealId 当前牌局标识
 * @param generation 当前 Java 牌局代际
 * @param modelId 配置中锁定的局前模型标识
 * @param deadlineMs 本次局前推理允许的最长耗时，单位为毫秒
 * @param stage 当前提示阶段，使用 lower_snake 稳定值
 * @param hand 本方手牌的稳定牌面符号
 * @param bottomCards 当前已确认底牌；尚未展示时为空数组
 * @param availableActions 当前界面实际允许的局前动作，使用 lower_snake 稳定值
 * @param callPromptSeen 本局是否观察过本方叫地主提示，不表示本方选择了叫地主
 * @param robPromptSeen 本局是否观察过本方抢地主提示，不表示本方选择了抢地主
 */
public record PreplayInferenceRequest(
        String contractVersion,
        String requestId,
        String dealId,
        long generation,
        String modelId,
        long deadlineMs,
        String stage,
        List<String> hand,
        List<String> bottomCards,
        List<String> availableActions,
        boolean callPromptSeen,
        boolean robPromptSeen
) {
    public PreplayInferenceRequest {
        requireText(contractVersion, "局前模型协议版本不能为空");
        requireText(requestId, "局前模型请求标识不能为空");
        requireText(dealId, "局前模型牌局标识不能为空");
        if (generation <= 0) {
            throw new IllegalArgumentException("局前模型牌局代际必须为正数");
        }
        requireText(modelId, "局前模型标识不能为空");
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("局前模型截止时间必须为正数");
        }
        requireText(stage, "局前模型阶段不能为空");
        hand = List.copyOf(Objects.requireNonNull(hand, "局前模型手牌不能为空"));
        if (hand.isEmpty()) {
            throw new IllegalArgumentException("局前模型手牌不能为空");
        }
        bottomCards = List.copyOf(Objects.requireNonNull(bottomCards, "局前模型底牌不能为空"));
        availableActions = List.copyOf(Objects.requireNonNull(
                availableActions, "局前模型可用动作不能为空"));
        if (availableActions.isEmpty()) {
            throw new IllegalArgumentException("局前模型可用动作不能为空");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
