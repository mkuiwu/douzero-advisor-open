package com.mkuiwu.douzero.runtime.domain;

/**
 * 当前牌局中的玩家身份，不包含会变化的手牌和出牌历史。
 *
 * @param seat 地主确认后的相对座位；一局内保持不变
 */
public record Player(Seat seat) {
    public Player {
        if (seat == null) {
            throw new NullPointerException("玩家座位不能为空");
        }
    }

    /** 阵营由座位派生，不在玩家对象中重复存储。 */
    public Role role() {
        return seat.role();
    }
}
