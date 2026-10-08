package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.time.Instant;
import java.util.Objects;

/**
 * 负责把新局识别到可建立正式出牌状态的完整牌局初始事实。
 *
 * <p>成功结果本身就是正式 play-stage ready 门禁：角色、正式手牌、底牌及农民所需的
 * 地主首手必须在同一任务内闭合。局前按钮仍可见或正式出牌画面未成立时不得提前成功。</p>
 */
public interface DealRecognitionPort {
    /**
     * 等待正式出牌阶段已经成立，且本方座位、当前手牌、底牌及必要的地主首手全部闭合。
     *
     * @param request 当前 GameContext 的牌局初始化任务
     * @return 可随状态替换或 GameContext 关闭而取消的任务
     */
    RecognitionJob<Result> recognizeDeal(Request request);

    /**
     * 牌局初始化请求。
     *
     * @param identity 当前牌局和任务身份
     * @param deadlineMs Python CV 交付完整初始事实的最长时间，单位为毫秒
     */
    record Request(GameTaskIdentity identity, long deadlineMs) {
        public Request {
            Objects.requireNonNull(identity, "牌局初始化身份不能为空");
            requireDeadline(deadlineMs);
        }
    }

    /** 牌局初始化任务只可能交付完整牌局或明确失败。 */
    sealed interface Result permits Recognized, Failed {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 已识别出足以让 Java 建立正式出牌状态的完整事实。
     *
     * @param identity 对应的局内任务身份
     * @param localSeat 本方在地主坐标系中的稳定座位
     * @param hand 正式出牌开始时的本方手牌；地主 20 张，农民 17 张
     * @param bottomCards 已确认的三张底牌
     * @param landlordOpeningPlay 农民牌局用于确认座位的地主首手牌；本方为地主时为空
     * @param observedAt 完整初始事实的业务观察时间
     */
    record Recognized(
            GameTaskIdentity identity,
            Seat localSeat,
            CardSet hand,
            CardSet bottomCards,
            CardSet landlordOpeningPlay,
            Instant observedAt
    ) implements Result {
        public Recognized {
            Objects.requireNonNull(identity, "牌局初始化结果身份不能为空");
            Objects.requireNonNull(localSeat, "本方座位不能为空");
            requireCards(hand, "牌局初始化手牌不能为空");
            Objects.requireNonNull(bottomCards, "底牌不能为空");
            Objects.requireNonNull(landlordOpeningPlay, "地主首手牌不能为空");
            Objects.requireNonNull(observedAt, "牌局初始化观察时间不能为空");
            if (bottomCards.size() != 3) {
                throw new IllegalArgumentException("牌局初始化必须包含三张底牌");
            }
            int expectedHandSize = localSeat == Seat.LANDLORD ? 20 : 17;
            if (hand.size() != expectedHandSize) {
                throw new IllegalArgumentException("牌局初始化手牌数量与本方座位不一致");
            }
            if (localSeat == Seat.LANDLORD && !landlordOpeningPlay.isEmpty()) {
                throw new IllegalArgumentException("本方地主牌局不能预先携带地主首手牌");
            }
            if (localSeat != Seat.LANDLORD && landlordOpeningPlay.isEmpty()) {
                throw new IllegalArgumentException("农民牌局必须携带用于确认座位的地主首手牌");
            }
        }
    }

    /**
     * Python CV 无法形成完整牌局初始事实。
     *
     * @param identity 对应的局内任务身份
     * @param failure 明确失败原因
     */
    record Failed(
            GameTaskIdentity identity,
            RecognitionFailure failure
    ) implements Result {
        public Failed {
            Objects.requireNonNull(identity, "牌局初始化失败身份不能为空");
            Objects.requireNonNull(failure, "牌局初始化失败不能为空");
        }
    }

    private static void requireDeadline(long deadlineMs) {
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("牌局初始化截止时间必须为正数");
        }
    }

    private static void requireCards(CardSet cards, String message) {
        Objects.requireNonNull(cards, message);
        if (cards.isEmpty()) {
            throw new IllegalArgumentException(message);
        }
    }
}
