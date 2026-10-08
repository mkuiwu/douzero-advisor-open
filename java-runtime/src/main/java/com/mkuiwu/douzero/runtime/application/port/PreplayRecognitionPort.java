package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;

import java.time.Instant;
import java.util.Objects;
import java.util.Set;

/** 负责交付一个稳定的本方叫地主、抢地主或加倍提示。 */
public interface PreplayRecognitionPort {
    /**
     * 等待完整局前提示；Python CV 自行完成截图、读牌、按钮识别和多帧稳定。
     *
     * @param request 当前局前提示任务
     * @return 可随正式牌局事实闭合或当前牌局结束而取消的任务
     */
    RecognitionJob<Result> waitForPrompt(Request request);

    /**
     * 局前提示识别请求。
     *
     * @param identity 当前牌局和单次提示任务身份
     * @param entryMode Python CV 确认提示边沿的模式；不表示 Java 预判用户操作
     * @param deadlineMs Python CV 交付完整提示的最长时间，单位为毫秒
     */
    record Request(
            GameTaskIdentity identity,
            EntryMode entryMode,
            long deadlineMs
    ) {
        public Request {
            Objects.requireNonNull(identity, "局前提示任务身份不能为空");
            Objects.requireNonNull(entryMode, "局前提示边沿模式不能为空");
            if (deadlineMs <= 0) {
                throw new IllegalArgumentException("局前提示截止时间必须为正数");
            }
        }
    }

    /** Python CV 如何确认请求对应的是新的本方局前提示。 */
    enum EntryMode {
        /** 建局后的首次请求可以直接接受当前已稳定的局前提示。 */
        ACCEPT_CURRENT_STABLE_PROMPT,

        /** 后续请求必须观察提示内容变化，或旧提示离开后新的提示进入并稳定。 */
        REQUIRE_CHANGE_OR_EXIT_THEN_NEW
    }

    /** 局前提示任务只可能交付完整提示或明确失败。 */
    sealed interface Result permits Ready, Failed {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 已稳定确认本方当前局前提示；Java 仍需建立快照后才能请求模型。
     *
     * @param identity 对应的局内任务身份
     * @param stage 当前稳定确认的叫地主、抢地主或加倍阶段
     * @param hand 当前稳定确认的本方手牌
     * @param bottomCards 当前可见且稳定的三张底牌；尚未展示时为空集合
     * @param availableActions 当前画面确实提供给本方的语义动作集合
     * @param observedAt 当前提示稳定成立的业务观察时间
     */
    record Ready(
            GameTaskIdentity identity,
            PreplayStage stage,
            CardSet hand,
            CardSet bottomCards,
            Set<PreplayAction> availableActions,
            Instant observedAt
    ) implements Result {
        public Ready {
            Objects.requireNonNull(identity, "局前提示结果身份不能为空");
            Objects.requireNonNull(stage, "局前提示阶段不能为空");
            Objects.requireNonNull(hand, "局前提示手牌不能为空");
            if (hand.isEmpty()) {
                throw new IllegalArgumentException("局前提示手牌不能为空");
            }
            Objects.requireNonNull(bottomCards, "局前提示底牌不能为空");
            validateCardCounts(stage, hand, bottomCards);
            availableActions = Set.copyOf(Objects.requireNonNull(
                    availableActions, "局前可用动作不能为空"));
            if (availableActions.isEmpty()
                    || availableActions.stream().anyMatch(action -> action.stage() != stage)) {
                throw new IllegalArgumentException("局前可用动作必须属于当前阶段且至少包含一项");
            }
            Objects.requireNonNull(observedAt, "局前提示观察时间不能为空");
        }
    }

    /**
     * Python CV 无法在任务截止前确认完整局前提示。
     *
     * @param identity 对应的局内任务身份
     * @param failure 明确失败原因；该失败不终止并行执行的牌局初始化任务
     */
    record Failed(
            GameTaskIdentity identity,
            RecognitionFailure failure
    ) implements Result {
        public Failed {
            Objects.requireNonNull(identity, "局前提示失败身份不能为空");
            Objects.requireNonNull(failure, "局前提示失败原因不能为空");
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
