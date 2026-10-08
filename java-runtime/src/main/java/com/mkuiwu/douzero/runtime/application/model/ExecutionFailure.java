package com.mkuiwu.douzero.runtime.application.model;

/** Python 执行端口明确失败时，Java 控制层可以稳定处理的失败分类。 */
public enum ExecutionFailure {
    /** 当前画面手牌与 Java 权威手牌不一致，拒绝执行。 */
    HAND_MISMATCH,

    /** 建议出的牌不在 Java 权威手牌集合内。 */
    RECOMMENDED_NOT_IN_HAND,

    /** 画面中找不到对应牌槽位，无法选牌。 */
    SLOT_NOT_FOUND,

    /** 选牌后视觉验证失败（牌未正确顶起或几何漂移超限）。 */
    VERIFY_FAILED,

    /** 点击前视觉证据超过新鲜度阈值，fail-closed 拒绝点击。 */
    EVIDENCE_STALE,

    /** 提交出牌对应的选牌任务未确认成功或身份不匹配。 */
    SELECTION_NOT_CONFIRMED,

    /** 画面中找不到出牌或不出按钮。 */
    BUTTON_NOT_FOUND,

    /** Java 牌局代际已过期或会话已关闭，授权失效。 */
    AUTHORITY_EXPIRED,

    /** 执行任务超过 Java 或 Python 硬截止时间。 */
    TIMEOUT,

    /** 自动选牌期间检测到用户按下鼠标左键，用户接管，自动化主动放弃。 */
    USER_INTERVENTION,

    /** 执行 Worker 内部异常，未分类的失败。 */
    INTERNAL_ERROR
}
