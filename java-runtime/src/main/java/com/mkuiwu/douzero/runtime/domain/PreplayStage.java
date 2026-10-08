package com.mkuiwu.douzero.runtime.domain;

/** 本方当前可见的局前操作阶段；只表示稳定提示，不表示任何按钮已经执行。 */
public enum PreplayStage {
    /** 本方正在决定是否首次叫地主。 */
    CALL_LANDLORD,

    /** 本方正在决定是否抢地主。 */
    ROB_LANDLORD,

    /** 地主已经确定，本方正在决定加倍档位。 */
    DOUBLE
}
