package com.mkuiwu.douzero.runtime.contract.recognition.v1;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Python CV 返回的扁平最终结果；牌面使用稳定符号，空动作列表只在此线协议边界表示不出。
 *
 * @param contractVersion 识别协议版本，必须为 recognition.v1
 * @param messageType 消息类型，固定为 RESULT
 * @param taskType 产生结果的业务识别能力
 * @param requestId 对应提交任务标识
 * @param dealId 当前牌局标识；NEW_GAME 时为空
 * @param generation 当前 Java 牌局代际；NEW_GAME 时为空
 * @param status 最终成功或失败状态
 * @param observedAt 成功业务事实的观察时间
 * @param promptType PREPLAY_PROMPT 成功时的局前提示类型
 * @param hand DEAL 成功时的本方正式手牌
 * @param currentHand LOCAL_TURN 成功时的本方当前手牌
 * @param bottomCards DEAL 成功时确认的三张底牌
 * @param localSeat DEAL 成功时确认的本方地主坐标系座位
 * @param landlordOpeningPlay 农民牌局在 DEAL 中确认的地主首手牌
 * @param actionsBySeat LOCAL_TURN 两侧结果区按语义座位索引的动作，空列表表示不出
 * @param availableActions PREPLAY_PROMPT 当前界面允许的局前动作
 * @param errorCode 失败时的稳定 lower_snake 错误分类
 * @param errorMessage 失败时不包含画面内容的简短诊断
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record RecognitionResult(
        String contractVersion,
        RecognitionMessageType messageType,
        RecognitionTaskType taskType,
        String requestId,
        String dealId,
        Long generation,
        RecognitionStatus status,
        Instant observedAt,
        String promptType,
        List<String> hand,
        List<String> currentHand,
        List<String> bottomCards,
        String localSeat,
        List<String> landlordOpeningPlay,
        Map<String, List<String>> actionsBySeat,
        List<String> availableActions,
        String errorCode,
        String errorMessage
) {
    public RecognitionResult {
        requireText(contractVersion, "识别结果协议版本不能为空");
        if (messageType != RecognitionMessageType.RESULT) {
            throw new IllegalArgumentException("识别结果消息类型必须为 RESULT");
        }
        Objects.requireNonNull(taskType, "识别结果任务类型不能为空");
        requireText(requestId, "识别结果请求标识不能为空");
        validateIdentity(taskType, dealId, generation);
        Objects.requireNonNull(status, "识别结果状态不能为空");
        hand = copy(hand);
        currentHand = copy(currentHand);
        bottomCards = copy(bottomCards);
        landlordOpeningPlay = copy(landlordOpeningPlay);
        availableActions = copy(availableActions);
        actionsBySeat = copyActions(actionsBySeat);
        if (status == RecognitionStatus.OK) {
            Objects.requireNonNull(observedAt, "成功识别结果必须包含观察时间");
            if (errorCode != null || errorMessage != null) {
                throw new IllegalArgumentException("成功识别结果不能携带错误信息");
            }
            validateSuccessShape(taskType, promptType, hand, currentHand, bottomCards,
                    localSeat, landlordOpeningPlay, actionsBySeat, availableActions);
        } else {
            requireText(errorCode, "失败识别结果必须包含错误分类");
            requireText(errorMessage, "失败识别结果必须包含诊断说明");
            if (observedAt != null || promptType != null || hand != null || currentHand != null
                    || bottomCards != null || localSeat != null || landlordOpeningPlay != null
                    || actionsBySeat != null || availableActions != null) {
                throw new IllegalArgumentException("失败识别结果不能携带业务事实");
            }
        }
    }

    private static void validateSuccessShape(
            RecognitionTaskType taskType,
            String promptType,
            List<String> hand,
            List<String> currentHand,
            List<String> bottomCards,
            String localSeat,
            List<String> landlordOpeningPlay,
            Map<String, List<String>> actionsBySeat,
            List<String> availableActions
    ) {
        boolean valid = switch (taskType) {
            case NEW_GAME, TURN_END, SETTLEMENT -> promptType == null && hand == null
                    && currentHand == null && bottomCards == null && localSeat == null
                    && landlordOpeningPlay == null && actionsBySeat == null
                    && availableActions == null;
            case PREPLAY_PROMPT -> promptType != null && hand != null
                    && currentHand == null && bottomCards != null && localSeat == null
                    && landlordOpeningPlay == null && actionsBySeat == null
                    && availableActions != null;
            case DEAL -> promptType == null && hand != null && currentHand == null
                    && bottomCards != null && localSeat != null && landlordOpeningPlay != null
                    && actionsBySeat == null && availableActions == null;
            case LOCAL_TURN -> promptType == null && hand == null && currentHand != null
                    && bottomCards == null && localSeat == null && landlordOpeningPlay == null
                    && actionsBySeat != null && availableActions == null;
        };
        if (!valid) {
            throw new IllegalArgumentException("成功识别结果字段与任务类型不一致");
        }
    }

    private static List<String> copy(List<String> values) {
        return values == null ? null : List.copyOf(values);
    }

    private static Map<String, List<String>> copyActions(Map<String, List<String>> actions) {
        if (actions == null) {
            return null;
        }
        return actions.entrySet().stream().collect(java.util.stream.Collectors.toUnmodifiableMap(
                Map.Entry::getKey, entry -> List.copyOf(entry.getValue())));
    }

    private static void validateIdentity(RecognitionTaskType taskType, String dealId,
                                         Long generation) {
        if (taskType == RecognitionTaskType.NEW_GAME) {
            if (dealId != null || generation != null) {
                throw new IllegalArgumentException("新局结果不能携带牌局身份");
            }
            return;
        }
        requireText(dealId, "局内识别结果必须包含牌局标识");
        if (generation == null || generation <= 0) {
            throw new IllegalArgumentException("局内识别结果必须包含正数代际");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
