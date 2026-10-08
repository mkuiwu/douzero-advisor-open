package com.mkuiwu.douzero.runtime.contract.recognition.v1;

/** Python CV 最终任务结果状态。 */
public enum RecognitionStatus {
    /** 已形成该能力要求的完整稳定业务事实。 */
    OK,
    /** 内部重试和恢复后仍无法交付完整结果。 */
    FAILED
}
