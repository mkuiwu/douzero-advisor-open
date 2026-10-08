package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

/** 持久局前模型进程或 JSONL 管道不可用时的明确异常。 */
public final class PreplayModelTransportException extends RuntimeException {
    /** 传输失败的稳定处理分类。 */
    private final Reason reason;

    public PreplayModelTransportException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public PreplayModelTransportException(Reason reason, String message, Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }

    /** Java Core 可稳定区分的局前模型传输失败。 */
    public enum Reason {
        /** 请求截止时间内没有收到完整单行响应。 */
        TIMEOUT,
        /** worker、管道或 stdout 线协议不可用。 */
        UNAVAILABLE
    }
}
