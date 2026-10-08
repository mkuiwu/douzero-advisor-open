package com.mkuiwu.douzero.runtime.contract.model.preplay;

/**
 * Python 局前模型进程启动后的协议就绪声明。
 *
 * @param contractVersion worker 实际支持的局前模型协议版本
 * @param messageType 消息类型，固定为 READY
 */
public record PreplayInferenceReady(String contractVersion, String messageType) {
    public PreplayInferenceReady {
        if (contractVersion == null || contractVersion.isBlank()) {
            throw new IllegalArgumentException("局前模型就绪协议版本不能为空");
        }
        if (!"READY".equals(messageType)) {
            throw new IllegalArgumentException("局前模型就绪消息类型必须为 READY");
        }
    }
}
