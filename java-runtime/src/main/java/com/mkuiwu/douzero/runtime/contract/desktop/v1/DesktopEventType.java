package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/** desktop.v1 明确允许的服务端事件类型。 */
public enum DesktopEventType {
    /** 鉴权成功后声明服务实例和编排器启用状态。 */
    READY,
    /** Java 权威牌局阶段发生变化。 */
    PHASE_CHANGED,
    /** Java 已校验的一条局前只读建议。 */
    PREPLAY_ADVICE,
    /** Java 已校验的一条正式出牌只读结果。 */
    PLAY_ADVICE,
    /** Java 已确认轮到本方，正式模型即将生成新建议。 */
    PLAY_TURN_STARTED,
    /** 当前牌局上下文已经收口。 */
    GAME_FINISHED
}
