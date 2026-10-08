package com.mkuiwu.douzero.runtime.application.model;

/** 当前 GameContext 结束并回到 WAIT_NEW_GAME 的业务原因。 */
public enum FinishReason {
    /** 结算任务交付了稳定结算页。 */
    SETTLEMENT_DETECTED,

    /** 当前状态任务或 Java 状态 timeout 使牌局失信。 */
    STATE_TIMEOUT,

    /** 硬门禁识别或结算旁路交付不可恢复失败，当前局不能继续相信。 */
    RECOGNITION_FAILED,

    /** Java Core 主动关闭当前牌局上下文。 */
    OPERATOR_ABORTED
}
