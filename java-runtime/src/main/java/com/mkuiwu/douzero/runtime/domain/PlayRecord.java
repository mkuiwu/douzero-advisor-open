package com.mkuiwu.douzero.runtime.domain;

import java.util.Objects;

/**
 * 一次已确认的出牌或不出记录；不负责判断牌型是否合法。
 *
 * @param player 执行本次动作的玩家
 * @param action 明确区分“出牌”和“不出”的领域动作，不使用空集合隐含业务含义
 */
public record PlayRecord(
        Player player,
        PlayAction action
) {
    public PlayRecord {
        Objects.requireNonNull(player, "出牌玩家不能为空");
        Objects.requireNonNull(action, "出牌动作不能为空");
    }
}
