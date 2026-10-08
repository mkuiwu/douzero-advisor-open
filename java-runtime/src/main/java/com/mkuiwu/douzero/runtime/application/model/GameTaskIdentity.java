package com.mkuiwu.douzero.runtime.application.model;

/**
 * 一个局内异步任务的稳定身份；所有回调都必须与当前 GameContext 比对后才能生效。
 *
 * @param requestId 单次业务任务标识；重试必须生成新标识
 * @param dealId 当前牌局标识；一局内保持不变
 * @param generation 当前 GameContext 代际；用于拒绝上一局或已关闭上下文的迟到结果
 */
public record GameTaskIdentity(
        String requestId,
        String dealId,
        long generation
) {
    public GameTaskIdentity {
        requireText(requestId, "任务请求标识不能为空");
        requireText(dealId, "牌局标识不能为空");
        if (generation <= 0) {
            throw new IllegalArgumentException("牌局代际必须为正数");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
