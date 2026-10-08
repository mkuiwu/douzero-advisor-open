package com.mkuiwu.douzero.runtime.contract.recognition.v1;

import java.util.List;
import java.util.Objects;

/**
 * Python CV 进程启动后的协议就绪声明。
 *
 * @param contractVersion 进程实际支持的识别协议版本
 * @param messageType 消息类型，固定为 READY
 * @param taskTypes 进程实际提供的业务识别能力
 */
public record RecognitionReady(
        String contractVersion,
        RecognitionMessageType messageType,
        List<RecognitionTaskType> taskTypes
) {
    public RecognitionReady {
        if (contractVersion == null || contractVersion.isBlank()) {
            throw new IllegalArgumentException("就绪协议版本不能为空");
        }
        if (messageType != RecognitionMessageType.READY) {
            throw new IllegalArgumentException("就绪消息类型必须为 READY");
        }
        taskTypes = List.copyOf(Objects.requireNonNull(taskTypes, "就绪能力列表不能为空"));
        if (taskTypes.isEmpty() || new java.util.HashSet<>(taskTypes).size() != taskTypes.size()) {
            throw new IllegalArgumentException("就绪能力列表不能为空或重复");
        }
    }
}
