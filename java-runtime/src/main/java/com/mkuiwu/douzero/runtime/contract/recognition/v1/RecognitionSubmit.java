package com.mkuiwu.douzero.runtime.contract.recognition.v1;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Java 提交给 Python CV 的扁平业务任务；不同能力只使用自己需要的可选字段。
 *
 * @param contractVersion 识别协议版本，当前固定为 recognition.v1
 * @param messageType 消息类型，固定为 SUBMIT
 * @param taskType 本次提交的业务识别能力
 * @param requestId 单次任务标识，重试必须生成新值
 * @param dealId 当前牌局标识；NEW_GAME 时为空，其余任务必填
 * @param generation 当前 Java 牌局代际；NEW_GAME 时为空，其余任务必须为正数
 * @param deadlineMs Python CV 交付最终结果的最长时间，单位为毫秒。LOCAL_TURN 和 TURN_END 均从提交起计；
 *                   后续旧协议的重新进入模式仍从出牌按钮按 turnEntryMode 重新出现起计
 * @param entryMode PREPLAY_PROMPT 的提示入口边沿模式
 * @param localSeat 本方地主坐标系座位；DEAL 前未知时为空
 * @param requiredActors LOCAL_TURN 本次必须稳定读取的对手座位
 * @param turnEntryMode LOCAL_TURN 的本方回合入口边沿模式
 * @param baselineHand TURN_END 建议时稳定确认的本方手牌，用于确认当前回合已经离开
 * @param baselineActionsBySeat TURN_END 建议时已读取的两侧动作，用于确认结果区相对基线发生变化
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record RecognitionSubmit(
        String contractVersion,
        RecognitionMessageType messageType,
        RecognitionTaskType taskType,
        String requestId,
        String dealId,
        Long generation,
        long deadlineMs,
        String entryMode,
        String localSeat,
        List<String> requiredActors,
        String turnEntryMode,
        List<String> baselineHand,
        Map<String, List<String>> baselineActionsBySeat
) {
    public RecognitionSubmit {
        requireText(contractVersion, "识别协议版本不能为空");
        if (messageType != RecognitionMessageType.SUBMIT) {
            throw new IllegalArgumentException("识别提交消息类型必须为 SUBMIT");
        }
        Objects.requireNonNull(taskType, "识别任务类型不能为空");
        requireText(requestId, "识别请求标识不能为空");
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("识别截止时间必须为正数");
        }
        validateIdentity(taskType, dealId, generation);
        requiredActors = requiredActors == null ? null : List.copyOf(requiredActors);
        baselineHand = baselineHand == null ? null : List.copyOf(baselineHand);
        baselineActionsBySeat = copyActions(baselineActionsBySeat);
        if (taskType == RecognitionTaskType.PREPLAY_PROMPT) {
            requireText(entryMode, "局前提示任务必须包含入口边沿模式");
        } else if (entryMode != null) {
            throw new IllegalArgumentException("非局前提示任务不能携带提示入口字段");
        }
        if (taskType == RecognitionTaskType.LOCAL_TURN) {
            requireText(localSeat, "本方回合任务必须包含本方座位");
            Objects.requireNonNull(requiredActors, "本方回合任务必须包含必需动作座位");
            requireText(turnEntryMode, "本方回合任务必须包含入口边沿模式");
            if (baselineHand != null || baselineActionsBySeat != null) {
                throw new IllegalArgumentException("本方回合任务不能携带回合结束基线字段");
            }
        } else if (taskType == RecognitionTaskType.TURN_END) {
            requireText(localSeat, "回合结束任务必须包含本方座位");
            if (requiredActors != null || turnEntryMode != null) {
                throw new IllegalArgumentException("回合结束任务不能携带本方回合入口字段");
            }
            if (baselineHand == null || baselineHand.isEmpty()) {
                throw new IllegalArgumentException("回合结束任务必须包含基线手牌");
            }
            Objects.requireNonNull(baselineActionsBySeat, "回合结束任务必须包含基线动作");
        } else if (localSeat != null || requiredActors != null || turnEntryMode != null
                || baselineHand != null || baselineActionsBySeat != null) {
            throw new IllegalArgumentException("非本方回合任务不能携带回合专属字段");
        }
    }

    private static void validateIdentity(RecognitionTaskType taskType, String dealId,
                                         Long generation) {
        if (taskType == RecognitionTaskType.NEW_GAME) {
            if (dealId != null || generation != null) {
                throw new IllegalArgumentException("新局任务不能预设牌局身份");
            }
            return;
        }
        requireText(dealId, "局内识别任务必须包含牌局标识");
        if (generation == null || generation <= 0) {
            throw new IllegalArgumentException("局内识别任务必须包含正数代际");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }

    private static Map<String, List<String>> copyActions(Map<String, List<String>> actions) {
        if (actions == null) {
            return null;
        }
        return actions.entrySet().stream().collect(java.util.stream.Collectors.toUnmodifiableMap(
                Map.Entry::getKey, entry -> List.copyOf(entry.getValue())));
    }
}
