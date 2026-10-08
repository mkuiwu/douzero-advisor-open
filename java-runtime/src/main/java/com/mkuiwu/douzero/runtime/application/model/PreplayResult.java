package com.mkuiwu.douzero.runtime.application.model;

import com.mkuiwu.douzero.runtime.domain.PreplayAction;

import java.util.Map;
import java.util.Objects;

/** 局前模型的语义结果；结果只能用于展示，不能证明用户已执行建议。 */
public sealed interface PreplayResult permits PreplayResult.Recommendation, PreplayResult.Unavailable {
    /** 返回产生结果的局内任务身份。 */
    GameTaskIdentity identity();

    /**
     * 已通过模型适配器校验的只读局前建议。
     *
     * @param identity 对应的局前模型任务身份
     * @param modelId 实际产生建议的模型标识
     * @param action 推荐的局前语义动作，必须出现在请求的可用动作中
     * @param score 模型给出的原始评分，不是概率或胜率
     * @param threshold 本次建议使用的业务阈值，仅用于解释模型结论
     * @param metadata 面向诊断展示的模型版本及耗时信息
     */
    record Recommendation(
            GameTaskIdentity identity,
            String modelId,
            PreplayAction action,
            double score,
            double threshold,
            Map<String, String> metadata
    ) implements PreplayResult {
        public Recommendation {
            Objects.requireNonNull(identity, "局前建议身份不能为空");
            requireText(modelId, "局前建议模型标识不能为空");
            Objects.requireNonNull(action, "局前建议动作不能为空");
            if (!Double.isFinite(score) || !Double.isFinite(threshold)) {
                throw new IllegalArgumentException("局前建议评分和阈值必须是有限数值");
            }
            metadata = Map.copyOf(Objects.requireNonNull(metadata, "局前建议元数据不能为空"));
        }
    }

    /**
     * 局前模型当前无法形成可信建议，牌局初始化识别仍须继续。
     *
     * @param identity 对应的局前模型任务身份
     * @param modelId 处理请求的模型标识
     * @param failure 稳定的模型失败分类
     * @param detail 面向诊断的简短说明，不得包含敏感信息
     */
    record Unavailable(
            GameTaskIdentity identity,
            String modelId,
            AdviceFailure failure,
            String detail
    ) implements PreplayResult {
        public Unavailable {
            Objects.requireNonNull(identity, "局前失败身份不能为空");
            requireText(modelId, "局前失败模型标识不能为空");
            Objects.requireNonNull(failure, "局前失败原因不能为空");
            requireText(detail, "局前失败说明不能为空");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
