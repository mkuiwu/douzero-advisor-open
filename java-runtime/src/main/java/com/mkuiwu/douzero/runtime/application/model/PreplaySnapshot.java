package com.mkuiwu.douzero.runtime.application.model;

import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;

import java.util.Objects;
import java.util.Set;

/**
 * Java 根据稳定局前提示建立的只读业务快照；不包含按钮坐标或视觉实现字段。
 *
 * @param dealId 当前牌局标识，一局内保持不变
 * @param generation 当前牌局代次，用于拒绝上一局迟到结果
 * @param stage 当前稳定确认的本方局前操作阶段
 * @param hand 当前稳定确认的本方手牌，叫抢阶段通常为十七张
 * @param bottomCards 当前可见并已确认的底牌；尚未展示时为空集合
 * @param availableActions 当前画面确实提供给本方的语义动作集合
 * @param callPromptSeen 本局是否观察过本方叫地主提示；只记录提示事实，不表示本方选择了叫地主
 * @param robPromptSeen 本局是否观察过本方抢地主提示；只记录提示事实，不表示本方选择了抢地主
 */
public record PreplaySnapshot(
        String dealId,
        long generation,
        PreplayStage stage,
        CardSet hand,
        CardSet bottomCards,
        Set<PreplayAction> availableActions,
        boolean callPromptSeen,
        boolean robPromptSeen
) {
    public PreplaySnapshot {
        requireText(dealId, "局前快照牌局标识不能为空");
        if (generation <= 0) {
            throw new IllegalArgumentException("局前快照牌局代次必须为正数");
        }
        Objects.requireNonNull(stage, "局前快照阶段不能为空");
        Objects.requireNonNull(hand, "局前快照手牌不能为空");
        if (hand.isEmpty()) {
            throw new IllegalArgumentException("局前快照手牌不能为空");
        }
        Objects.requireNonNull(bottomCards, "局前快照底牌不能为空");
        validateCardCounts(stage, hand, bottomCards);
        availableActions = Set.copyOf(Objects.requireNonNull(
                availableActions, "局前可用动作不能为空"));
        if (availableActions.isEmpty()
                || availableActions.stream().anyMatch(action -> action.stage() != stage)) {
            throw new IllegalArgumentException("局前可用动作必须属于当前阶段且至少包含一项");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }

    private static void validateCardCounts(
            PreplayStage stage,
            CardSet hand,
            CardSet bottomCards
    ) {
        if (!bottomCards.isEmpty() && bottomCards.size() != 3) {
            throw new IllegalArgumentException("局前底牌只能尚未展示或完整确认三张");
        }
        if ((stage == PreplayStage.CALL_LANDLORD || stage == PreplayStage.ROB_LANDLORD)
                && hand.size() != 17) {
            throw new IllegalArgumentException("叫地主和抢地主提示必须携带十七张本方手牌");
        }
        if (stage == PreplayStage.DOUBLE && hand.size() != 17 && hand.size() != 20) {
            throw new IllegalArgumentException("加倍提示只能携带十七张农民手牌或二十张地主手牌");
        }
        if (stage == PreplayStage.DOUBLE && hand.size() == 20 && bottomCards.size() != 3) {
            throw new IllegalArgumentException("地主加倍提示必须携带三张已确认底牌");
        }
    }
}
