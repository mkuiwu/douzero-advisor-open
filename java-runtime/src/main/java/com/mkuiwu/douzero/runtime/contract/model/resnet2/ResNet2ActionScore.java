package com.mkuiwu.douzero.runtime.contract.model.resnet2;

import java.util.List;
import java.util.Objects;

/**
 * ResNet2 对一个候选动作的线协议评分。
 *
 * @param action Python 规则层生成的候选动作；空集合表示不出
 * @param value 模型预测的原始回报价值，只能在本次请求的候选动作之间比较
 */
public record ResNet2ActionScore(List<Integer> action, double value) {
    public ResNet2ActionScore {
        action = List.copyOf(Objects.requireNonNull(action, "候选动作不能为空"));
        if (!Double.isFinite(value)) {
            throw new IllegalArgumentException("候选动作价值必须是有限数值");
        }
    }
}
