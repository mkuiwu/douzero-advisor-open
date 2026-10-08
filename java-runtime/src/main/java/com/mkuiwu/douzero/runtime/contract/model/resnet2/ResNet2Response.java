package com.mkuiwu.douzero.runtime.contract.model.resnet2;

import com.mkuiwu.douzero.runtime.contract.common.ContractStatus;
import com.mkuiwu.douzero.runtime.contract.common.ErrorCode;

import java.util.List;
import java.util.Objects;

/**
 * ResNet2 的完整线协议返回；推荐动作直接位于 action 字段。
 *
 * @param contractVersion 响应协议版本，必须与请求版本一致
 * @param requestId 对应请求标识，不匹配的响应必须丢弃
 * @param dealId 对应牌局标识，跨局响应必须丢弃
 * @param modelId 模型标识，固定为 resnet2
 * @param status 推理结果状态；只有 OK 才包含推荐动作
 * @param action 推荐动作的 DouZero 整数编码；OK 时非 null，空集合明确表示不出
 * @param actionValue 推荐动作的模型原始回报价值，不是概率或胜率
 * @param actionMargin 推荐动作价值减去第二名价值；只有一个候选时为 0
 * @param actionScores Python 生成的全部候选动作及其模型原始价值，保持模型评估顺序
 * @param modelVersion 实际加载的模型制品版本；OK 时必填
 * @param latencyMs 模型服务处理耗时，单位为毫秒
 * @param errorCode 结构化错误码；OK 时必须为 NONE
 * @param errorMessage 失败诊断说明；非 OK 时必填且不得包含敏感信息
 */
public record ResNet2Response(
        String contractVersion,
        String requestId,
        String dealId,
        String modelId,
        ContractStatus status,
        List<Integer> action,
        Double actionValue,
        Double actionMargin,
        List<ResNet2ActionScore> actionScores,
        String modelVersion,
        long latencyMs,
        ErrorCode errorCode,
        String errorMessage
) {
    public ResNet2Response {
        requireText(contractVersion, "协议版本不能为空");
        requireText(requestId, "请求标识不能为空");
        requireText(dealId, "牌局标识不能为空");
        if (!"resnet2".equals(modelId)) {
            throw new IllegalArgumentException("ResNet2 响应的模型标识必须为 resnet2");
        }
        Objects.requireNonNull(status, "响应状态不能为空");
        Objects.requireNonNull(errorCode, "错误码不能为空");
        action = action == null ? null : List.copyOf(action);
        actionScores = actionScores == null ? null : List.copyOf(actionScores);
        if (latencyMs < 0) {
            throw new IllegalArgumentException("模型耗时不能为负数");
        }
        if (status == ContractStatus.OK) {
            Objects.requireNonNull(action, "成功响应必须包含动作");
            validateScores(action, actionValue, actionMargin, actionScores);
            requireText(modelVersion, "成功响应必须包含模型版本");
            if (errorCode != ErrorCode.NONE) {
                throw new IllegalArgumentException("成功响应不能携带错误码");
            }
        } else {
            if (action != null || actionValue != null || actionMargin != null
                    || actionScores != null) {
                throw new IllegalArgumentException("等待或错误响应不能携带动作评分");
            }
            if (errorCode == ErrorCode.NONE) {
                throw new IllegalArgumentException("等待或错误响应必须携带错误码");
            }
            requireText(errorMessage, "等待或错误响应必须包含诊断说明");
        }
    }

    private static void validateScores(List<Integer> action, Double actionValue,
                                       Double actionMargin,
                                       List<ResNet2ActionScore> actionScores) {
        Objects.requireNonNull(actionValue, "成功响应必须包含推荐动作价值");
        Objects.requireNonNull(actionMargin, "成功响应必须包含推荐动作领先值");
        Objects.requireNonNull(actionScores, "成功响应必须包含全部候选动作评分");
        if (!Double.isFinite(actionValue)) {
            throw new IllegalArgumentException("推荐动作价值必须是有限数值");
        }
        if (!Double.isFinite(actionMargin) || actionMargin < 0) {
            throw new IllegalArgumentException("推荐动作领先值必须是非负有限数值");
        }
        if (actionScores.isEmpty()) {
            throw new IllegalArgumentException("候选动作评分不能为空");
        }
        boolean selectedScorePresent = actionScores.stream().anyMatch(candidate ->
                candidate.action().equals(action)
                        && Double.compare(candidate.value(), actionValue) == 0);
        if (!selectedScorePresent) {
            throw new IllegalArgumentException("推荐动作与候选动作评分不一致");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
