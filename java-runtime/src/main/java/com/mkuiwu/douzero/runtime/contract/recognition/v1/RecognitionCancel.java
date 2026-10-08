package com.mkuiwu.douzero.runtime.contract.recognition.v1;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.Objects;

/**
 * Java 取消 Python CV 业务任务的扁平消息。
 *
 * @param contractVersion 识别协议版本，当前固定为 recognition.v1
 * @param messageType 消息类型，固定为 CANCEL
 * @param taskType 被取消任务的业务能力
 * @param requestId 被取消的单次任务标识
 * @param dealId 当前牌局标识；NEW_GAME 时为空
 * @param generation 当前 Java 牌局代际；NEW_GAME 时为空
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record RecognitionCancel(
        String contractVersion,
        RecognitionMessageType messageType,
        RecognitionTaskType taskType,
        String requestId,
        String dealId,
        Long generation
) {
    public RecognitionCancel {
        requireText(contractVersion, "识别协议版本不能为空");
        if (messageType != RecognitionMessageType.CANCEL) {
            throw new IllegalArgumentException("识别取消消息类型必须为 CANCEL");
        }
        Objects.requireNonNull(taskType, "取消任务类型不能为空");
        requireText(requestId, "取消请求标识不能为空");
        if (taskType == RecognitionTaskType.NEW_GAME) {
            if (dealId != null || generation != null) {
                throw new IllegalArgumentException("新局取消不能携带牌局身份");
            }
        } else {
            requireText(dealId, "局内取消必须包含牌局标识");
            if (generation == null || generation <= 0) {
                throw new IllegalArgumentException("局内取消必须包含正数代际");
            }
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
