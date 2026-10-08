package com.mkuiwu.douzero.runtime.application.model;

/** Python CV 无法交付完整识别任务结果时的稳定失败原因。 */
public enum RecognitionFailureCode {
    /** 任务截止时间内没有形成最终语义结果。 */
    DEADLINE_EXCEEDED,

    /** Python CV 内部无法取得可用画面。 */
    CAPTURE_UNAVAILABLE,

    /** 画面存在但无法形成可信业务事实。 */
    OBSERVATION_UNCERTAIN,

    /** 任务被 Java Core 在上下文关闭或状态替换时取消。 */
    TASK_CANCELLED,

    /** Python CV 返回的任务结果身份或内容不完整。 */
    INVALID_RESULT,

    /** Python CV 服务或对应识别能力当前不可用。 */
    SERVICE_UNAVAILABLE
}
