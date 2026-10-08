package com.mkuiwu.douzero.runtime.contract.desktop.v1;

import java.util.Objects;

/**
 * Java 权威历史中的一条语义动作。
 *
 * @param seat 动作玩家在地主坐标系中的稳定座位
 * @param action 明确区分出牌和不出的动作
 */
public record DesktopHistoryItem(String seat, DesktopAction action) {
    public DesktopHistoryItem {
        if (seat == null || seat.isBlank()) {
            throw new IllegalArgumentException("桌面历史座位不能为空");
        }
        Objects.requireNonNull(action, "桌面历史动作不能为空");
    }
}
