package com.mkuiwu.douzero.runtime.application.model;

import com.mkuiwu.douzero.runtime.domain.PlayAction;

import java.util.List;
import java.util.Map;
import java.util.Objects;

/** 模型端口返回的领域结果；不暴露 HTTP 状态、JSON 或模型牌面编码。 */
public sealed interface AdviceResult permits AdviceResult.Recommendation, AdviceResult.Unavailable {
    /** 返回产生结果的模型适配器标识。 */
    String modelId();

    /**
     * 已通过协议和身份校验的只读建议；候选动作由 Python 固定规则实现生成。
     *
     * @param modelId 实际产生建议的模型标识
     * @param action 已解码为领域语义的推荐动作
     * @param actionValue 推荐动作的模型原始回报价值，不是概率或胜率
     * @param actionMargin 推荐动作价值减去第二名价值；只有一个候选时为 0
     * @param actionScores Python 生成的全部候选动作及其模型原始价值，保持模型评估顺序
     * @param metadata 仅供诊断展示的模型版本和耗时信息
     */
    record Recommendation(
            String modelId,
            PlayAction action,
            double actionValue,
            double actionMargin,
            List<ActionScore> actionScores,
            Map<String, String> metadata
    ) implements AdviceResult {
        public Recommendation {
            requireText(modelId, "模型标识不能为空");
            Objects.requireNonNull(action, "推荐动作不能为空");
            if (!Double.isFinite(actionValue)) {
                throw new IllegalArgumentException("推荐动作价值必须是有限数值");
            }
            if (!Double.isFinite(actionMargin) || actionMargin < 0) {
                throw new IllegalArgumentException("推荐动作领先值必须是非负有限数值");
            }
            actionScores = List.copyOf(Objects.requireNonNull(
                    actionScores, "候选动作评分不能为空"));
            if (actionScores.isEmpty()) {
                throw new IllegalArgumentException("候选动作评分不能为空");
            }
            metadata = Map.copyOf(Objects.requireNonNull(metadata, "模型元数据不能为空"));
        }
    }

    /**
     * 当前请求不能形成可信建议，调用方必须进入等待或恢复流程。
     *
     * @param modelId 处理请求的模型适配器标识
     * @param failure 稳定的内部失败原因
     * @param detail 面向诊断的简短说明，不得包含敏感信息
     */
    record Unavailable(
            String modelId,
            AdviceFailure failure,
            String detail
    ) implements AdviceResult {
        public Unavailable {
            requireText(modelId, "模型标识不能为空");
            Objects.requireNonNull(failure, "建议失败原因不能为空");
            requireText(detail, "建议失败说明不能为空");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
