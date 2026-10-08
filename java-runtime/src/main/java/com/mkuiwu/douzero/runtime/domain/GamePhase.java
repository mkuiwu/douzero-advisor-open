package com.mkuiwu.douzero.runtime.domain;

/** 牌局生命周期阶段；阶段推进规则由后续应用服务负责。 */
public enum GamePhase {
    /** 未检测到可进入的牌局，运行时只等待新局证据。 */
    WAIT_NEW_GAME,

    /** 已确认新局，叫地主、抢地主或加倍提示与正式牌局初始化正在并行识别。 */
    PREPLAY,

    /** 发牌或手牌动画尚未结束，识别结果不能用于锁定牌局。 */
    DEALING,

    /** 底牌正在展示，等待底牌和角色相关证据稳定。 */
    BOTTOM_CARDS_REVEAL,

    /** 地主角色及三个相对座位已在本局锁定。 */
    ROLE_CONFIRMED,

    /** 正式出牌阶段，可以基于已确认状态请求只读建议。 */
    PLAYING,

    /** 牌局已结束，等待归档或进入下一局。 */
    SETTLEMENT
}
