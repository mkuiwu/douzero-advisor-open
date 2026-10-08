package com.mkuiwu.douzero.runtime.contract.desktop.v1;

import java.util.List;
import java.util.Objects;

/**
 * 桌面协议中的显式出牌动作。
 *
 * @param pass 是否明确表示不出；为 true 时 cards 必须为空
 * @param cards 实际打出的领域牌面符号；出牌时至少包含一张
 */
public record DesktopAction(boolean pass, List<String> cards) {
    public DesktopAction {
        cards = List.copyOf(Objects.requireNonNull(cards, "桌面动作牌集合不能为空"));
        if (pass != cards.isEmpty()) {
            throw new IllegalArgumentException("不出动作必须为空牌，出牌动作必须包含牌");
        }
    }
}
