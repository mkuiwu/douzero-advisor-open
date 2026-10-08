package com.mkuiwu.douzero.runtime.application.model;

import com.mkuiwu.douzero.runtime.domain.PlayAction;

import java.util.Objects;

/**
 * 模型对一个候选动作给出的原始价值评估。
 *
 * @param action Python 按固定 DouZero 规则生成的候选动作
 * @param value 模型预测的原始回报价值，只能在同一模型、同一次请求内比较，不是概率或胜率
 */
public record ActionScore(PlayAction action, double value) {
    public ActionScore {
        Objects.requireNonNull(action, "候选动作不能为空");
        if (!Double.isFinite(value)) {
            throw new IllegalArgumentException("候选动作价值必须是有限数值");
        }
    }
}
