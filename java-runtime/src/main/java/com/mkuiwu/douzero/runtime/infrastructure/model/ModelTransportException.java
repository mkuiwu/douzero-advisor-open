package com.mkuiwu.douzero.runtime.infrastructure.model;

import java.util.Objects;

/** 模型线协议传输失败；用于区分超时与一般不可用，不能携带敏感响应内容。 */
public final class ModelTransportException extends RuntimeException {
    /** 传输层可稳定交给模型适配器处理的失败类别。 */
    public enum Reason {
        /** 请求超过调用方给定的截止时间，迟到响应必须丢弃。 */
        TIMEOUT,

        /** 响应无法与当前请求身份对应，必须拒绝且淘汰当前传输代次。 */
        INVALID_RESPONSE,

        /** 模型进程、连接或传输协议当前不可用。 */
        UNAVAILABLE
    }

    /** 本次传输失败的稳定类别。 */
    private final Reason reason;

    public ModelTransportException(Reason reason, String message, Throwable cause) {
        super(message, cause);
        this.reason = Objects.requireNonNull(reason, "模型传输失败类别不能为空");
    }

    /** 返回不包含底层实现细节的稳定失败类别。 */
    public Reason reason() {
        return reason;
    }
}
