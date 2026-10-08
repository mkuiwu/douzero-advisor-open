package com.mkuiwu.douzero.runtime.domain;

import java.util.List;
import java.util.Objects;

/**
 * Java 控制层确认后的语义化牌局快照，不包含模型编码、JSON 或视觉实现类型。
 *
 * @param dealId 当前已锁定牌局的唯一标识，一局内保持不变并用于隔离跨局状态
 * @param phase 当前牌局阶段；只有允许建议的阶段才能交给模型端口
 * @param localSeat 本方在地主坐标系中的相对座位，一局内锁定后不再变化
 * @param hand 当前本方手牌
 * @param bottomCards 已确认的三张底牌；尚未确认时为空集合
 * @param history 从地主首次行动开始、按列表下标和座位顺序排列的完整动作记录
 */
public record GameSnapshot(
        String dealId,
        GamePhase phase,
        Seat localSeat,
        CardSet hand,
        CardSet bottomCards,
        List<PlayRecord> history
) {
    public GameSnapshot {
        if (dealId == null || dealId.isBlank()) {
            throw new IllegalArgumentException("牌局标识不能为空");
        }
        Objects.requireNonNull(phase, "游戏阶段不能为空");
        Objects.requireNonNull(localSeat, "本方座位不能为空");
        Objects.requireNonNull(hand, "手牌不能为空");
        Objects.requireNonNull(bottomCards, "底牌不能为空");
        history = List.copyOf(Objects.requireNonNull(history, "出牌历史不能为空"));
        Seat expectedSeat = Seat.LANDLORD;
        boolean activeLead = false;
        int trailingPasses = 0;
        for (PlayRecord record : history) {
            if (record.player().seat() != expectedSeat) {
                throw new IllegalArgumentException("出牌历史必须从地主开始并按座位顺序完整记录");
            }
            if (record.action() instanceof PlayAction.Play) {
                activeLead = true;
                trailingPasses = 0;
            } else if (!activeLead) {
                throw new IllegalArgumentException("当前墩没有有效领出时不能记录不出");
            } else if (++trailingPasses == 2) {
                activeLead = false;
                trailingPasses = 0;
            }
            expectedSeat = expectedSeat.next();
        }
    }

    /** 根据完整历史长度派生当前应行动座位。 */
    public Seat currentSeat() {
        return Seat.values()[history.size() % Seat.values().length];
    }

    /** 根据历史派生当前墩的有效领出；连续两家不出后返回空集合。 */
    public CardSet lastMove() {
        int trailingPasses = 0;
        for (int index = history.size() - 1; index >= 0; index--) {
            if (history.get(index).action() instanceof PlayAction.Pass) {
                trailingPasses++;
            } else {
                break;
            }
        }
        if (trailingPasses >= 2) {
            return CardSet.empty();
        }
        for (int index = history.size() - 1; index >= 0; index--) {
            PlayAction action = history.get(index).action();
            if (action instanceof PlayAction.Play play) {
                return play.cards();
            }
        }
        return CardSet.empty();
    }
}
