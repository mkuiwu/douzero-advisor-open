package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/** 负责交付一个稳定本方回合中的手牌和两侧玩家动作快照。 */
public interface LocalTurnRecognitionPort {
    /**
     * 等待本方出牌按钮、当前手牌和 Java 指定的对手动作区域全部稳定。
     *
     * @param request 当前本方回合任务
     * @return 可随回合结束或 GameContext 关闭而取消的任务
     */
    RecognitionJob<Result> waitUntilReady(Request request);

    /**
     * 本方回合确认请求。
     *
     * @param identity 当前牌局和任务身份
     * @param localSeat 本方稳定座位
     * @param requiredActors 本次必须稳定读取、且尚未进入权威历史的对手座位。首次请求从 Deal
     *                       已建立的历史游标开始，之后每次包含本方动作后的两个对手；空集合表示
     *                       已直接轮到本方，Python 不得因左右结果区失败。
     * @param turnEntryMode Python CV 确认出牌按钮边沿的模式；不表示 Python 维护局内轮次
     * @param deadlineMs 首次接受当前稳定回合时从任务提交起计、后续等待重新进入时从按钮重新出现起计的
     *                   最长识别时间，单位为毫秒；由 Python CV 独占解释和完成任务
     */
    record Request(
            GameTaskIdentity identity,
            Seat localSeat,
            Set<Seat> requiredActors,
            TurnEntryMode turnEntryMode,
            long deadlineMs
    ) {
        public Request {
            Objects.requireNonNull(identity, "本方回合任务身份不能为空");
            Objects.requireNonNull(localSeat, "本方座位不能为空");
            requiredActors = Set.copyOf(Objects.requireNonNull(
                    requiredActors, "必需动作座位不能为空"));
            if (requiredActors.size() > 2 || requiredActors.contains(localSeat)) {
                throw new IllegalArgumentException("必需动作座位只能包含至多两个对手座位");
            }
            Objects.requireNonNull(turnEntryMode, "本方回合边沿模式不能为空");
            requireDeadline(deadlineMs);
        }
    }

    /** Python CV 如何确认这是请求对应的本方回合入口边沿。 */
    enum TurnEntryMode {
        /** Deal 建局后的首次请求可以直接接受当前已稳定出现的出牌按钮。 */
        ACCEPT_CURRENT_STABLE_TURN,

        /** 后续请求必须先连续两帧观察出牌按钮消失，再观察其重新出现并稳定。 */
        REQUIRE_EXIT_THEN_REENTER
    }

    /** 本方回合任务只可能确认可建议状态或明确失败。 */
    sealed interface Result permits Ready, Failed {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 已确认本方回合快照可用；Java 仍需按自身历史核对并原子归并后才能请求建议。
     *
     * @param identity 对应的局内任务身份
     * @param currentHand 画面中稳定确认的当前本方手牌
     * @param actionsBySeat 画面两侧结果区按地主坐标系座位索引的稳定动作；可以包含本轮不需要消费的旧结果
     * @param observedAt 本方回合可用的业务观察时间
     */
    record Ready(
            GameTaskIdentity identity,
            CardSet currentHand,
            Map<Seat, PlayAction> actionsBySeat,
            Instant observedAt
    ) implements Result {
        public Ready {
            Objects.requireNonNull(identity, "本方回合结果身份不能为空");
            requireCards(currentHand, "本方回合确认手牌不能为空");
            actionsBySeat = Map.copyOf(Objects.requireNonNull(
                    actionsBySeat, "两侧玩家动作不能为空"));
            Objects.requireNonNull(observedAt, "本方回合观察时间不能为空");
        }
    }

    /**
     * Python CV 无法确认本方回合已经可用于建议。
     *
     * @param identity 对应的局内任务身份
     * @param failure 明确失败原因
     */
    record Failed(
            GameTaskIdentity identity,
            RecognitionFailure failure
    ) implements Result {
        public Failed {
            Objects.requireNonNull(identity, "本方回合失败身份不能为空");
            Objects.requireNonNull(failure, "本方回合识别失败不能为空");
        }
    }

    private static void requireDeadline(long deadlineMs) {
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("本方回合识别截止时间必须为正数");
        }
    }

    private static void requireCards(CardSet cards, String message) {
        Objects.requireNonNull(cards, message);
        if (cards.isEmpty()) {
            throw new IllegalArgumentException(message);
        }
    }
}
