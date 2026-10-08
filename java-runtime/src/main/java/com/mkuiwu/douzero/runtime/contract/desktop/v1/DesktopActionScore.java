package com.mkuiwu.douzero.runtime.contract.desktop.v1;

import java.util.Objects;

/**
 * 桌面协议中一个候选动作的模型原始评分。
 *
 * @param action 已恢复为领域牌面的候选动作
 * @param value 同一次模型请求内可比较的原始回报价值，不是概率或胜率
 */
public record DesktopActionScore(DesktopAction action, double value) {
    public DesktopActionScore {
        Objects.requireNonNull(action, "桌面候选动作不能为空");
        if (!Double.isFinite(value)) {
            throw new IllegalArgumentException("桌面候选动作评分必须是有限数值");
        }
    }
}
