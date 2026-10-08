package com.mkuiwu.douzero.runtime.contract.model.preplay;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Python 局前模型返回的唯一扁平线协议响应。
 *
 * @param contractVersion 响应协议版本，必须与请求一致
 * @param requestId 对应请求标识
 * @param dealId 对应牌局标识
 * @param generation 对应 Java 牌局代际
 * @param modelId 实际处理请求的模型标识
 * @param status 推理状态，固定为 OK 或 FAILED
 * @param action 成功时推荐的 lower_snake 局前动作
 * @param score 成功时模型原始评分，只用于模型业务阈值比较，不是概率或胜率
 * @param threshold 成功时该模型本次使用的业务阈值
 * @param decisionReason 成功时面向操作员解释建议的简短原因
 * @param modelVersion 成功时实际加载的模型制品版本
 * @param latencyMs 模型处理耗时，单位为毫秒
 * @param errorCode 失败时的稳定 lower_snake 错误分类
 * @param errorMessage 失败时不包含敏感信息的简短诊断
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record PreplayInferenceResponse(
        String contractVersion,
        String requestId,
        String dealId,
        long generation,
        String modelId,
        String status,
        String action,
        Double score,
        Double threshold,
        String decisionReason,
        String modelVersion,
        long latencyMs,
        String errorCode,
        String errorMessage
) {
    public PreplayInferenceResponse {
        requireText(contractVersion, "局前响应协议版本不能为空");
        requireText(requestId, "局前响应请求标识不能为空");
        requireText(dealId, "局前响应牌局标识不能为空");
        if (generation <= 0) {
            throw new IllegalArgumentException("局前响应牌局代际必须为正数");
        }
        requireText(modelId, "局前响应模型标识不能为空");
        if (latencyMs < 0) {
            throw new IllegalArgumentException("局前模型耗时不能为负数");
        }
        if ("OK".equals(status)) {
            requireText(action, "局前成功响应必须包含动作");
            if (score == null || !Double.isFinite(score)
                    || threshold == null || !Double.isFinite(threshold)) {
                throw new IllegalArgumentException("局前成功响应必须包含有限评分和阈值");
            }
            requireText(decisionReason, "局前成功响应必须包含决策原因");
            requireText(modelVersion, "局前成功响应必须包含模型版本");
            if (errorCode != null || errorMessage != null) {
                throw new IllegalArgumentException("局前成功响应不能携带错误信息");
            }
        } else if ("FAILED".equals(status)) {
            if (action != null || score != null || threshold != null || decisionReason != null
                    || modelVersion != null) {
                throw new IllegalArgumentException("局前失败响应不能携带建议");
            }
            requireText(errorCode, "局前失败响应必须包含错误分类");
            requireText(errorMessage, "局前失败响应必须包含诊断说明");
        } else {
            throw new IllegalArgumentException("局前响应状态必须为 OK 或 FAILED");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
