package com.mkuiwu.douzero.runtime.contract.recognition.v1;

/** Java 与 Python CV 之间 JSONL 消息的稳定类型。 */
public enum RecognitionMessageType {
    /** Java 提交一个完整业务识别任务。 */
    SUBMIT,
    /** Java 取消仍在执行的业务识别任务。 */
    CANCEL,
    /** Python 返回任务的最终成功或失败。 */
    RESULT,
    /** Python 进程启动后声明协议已经可用。 */
    READY
}
