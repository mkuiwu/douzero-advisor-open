package com.mkuiwu.douzero.runtime.domain;

/** 局前模型可以给出的只读语义建议；该类型不具备点击或执行能力。 */
public enum PreplayAction {
    /** 建议叫地主。 */
    CALL,

    /** 建议不叫地主。 */
    NO_CALL,

    /** 建议抢地主。 */
    ROB,

    /** 建议不抢地主。 */
    NO_ROB,

    /** 建议普通加倍。 */
    DOUBLE,

    /** 建议使用超级加倍。 */
    SUPER_DOUBLE,

    /** 建议不加倍。 */
    NO_DOUBLE;

    /** 返回该动作所属局前阶段，用于拒绝跨阶段建议。 */
    public PreplayStage stage() {
        return switch (this) {
            case CALL, NO_CALL -> PreplayStage.CALL_LANDLORD;
            case ROB, NO_ROB -> PreplayStage.ROB_LANDLORD;
            case DOUBLE, SUPER_DOUBLE, NO_DOUBLE -> PreplayStage.DOUBLE;
        };
    }
}
