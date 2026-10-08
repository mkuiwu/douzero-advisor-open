package com.mkuiwu.douzero.runtime.contract.recognition.v1;

/** Python CV 对外提供的六种业务识别能力。 */
public enum RecognitionTaskType {
    /** 等待可靠的新局边界。 */
    NEW_GAME,
    /** 等待本方叫地主、抢地主或加倍提示稳定出现。 */
    PREPLAY_PROMPT,
    /** 等待正式牌局初始化事实闭合。 */
    DEAL,
    /** 等待一个可归并的稳定本方回合快照。 */
    LOCAL_TURN,
    /** 等待当前已建议的本方回合离开。 */
    TURN_END,
    /** 整局旁路等待结算页面。 */
    SETTLEMENT
}
