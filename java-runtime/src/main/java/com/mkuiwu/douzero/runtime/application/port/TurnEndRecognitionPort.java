package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

/** 负责确认已建议的本方回合已经离开；不等待下一次本方回合，也不推导动作历史。 */
public interface TurnEndRecognitionPort {
    /**
     * 根据 Java 在建议时保存的语义快照，等待当前本方回合稳定离开。
     *
     * @param request 当前回合结束探测任务
     * @return 可由当前牌局统一取消的完整识别任务
     */
    RecognitionJob<Result> waitUntilEnded(Request request);

    /**
     * 本方当前回合结束探测请求。
     *
     * @param identity 当前牌局、代次和一次性任务身份
     * @param localSeat 本方在地主坐标系中的固定座位
     * @param baselineHand 建议时稳定确认的本方手牌；仅用于识别当前回合已离开，不能由 Python 写入历史
     * @param baselineActionsBySeat 建议时两侧已稳定读取的动作；仅用作画面变化的辅助边界，不要求包含未知侧
     * @param deadlineMs Python CV 交付“当前回合已结束”或明确失败的最长时间，单位为毫秒
     */
    record Request(
            GameTaskIdentity identity,
            Seat localSeat,
            CardSet baselineHand,
            Map<Seat, PlayAction> baselineActionsBySeat,
            long deadlineMs
    ) {
        public Request {
            Objects.requireNonNull(identity, "回合结束任务身份不能为空");
            Objects.requireNonNull(localSeat, "回合结束任务本方座位不能为空");
            Objects.requireNonNull(baselineHand, "回合结束任务基线手牌不能为空");
            if (baselineHand.isEmpty()) {
                throw new IllegalArgumentException("回合结束任务基线手牌不能为空");
            }
            baselineActionsBySeat = Map.copyOf(Objects.requireNonNull(
                    baselineActionsBySeat, "回合结束任务基线动作不能为空"));
            if (baselineActionsBySeat.containsKey(localSeat)
                    || baselineActionsBySeat.size() > 2) {
                throw new IllegalArgumentException("回合结束任务基线动作只能包含至多两名对手");
            }
            if (deadlineMs <= 0) {
                throw new IllegalArgumentException("回合结束识别截止时间必须为正数");
            }
        }
    }

    /** 回合结束探测只可能确认边界或交付明确失败。 */
    sealed interface Result permits Ended, Failed {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 已确认建议所在的本方回合已经离开。
     *
     * @param identity 对应的局内任务身份
     * @param observedAt 视觉上确认当前回合离开的时间
     */
    record Ended(GameTaskIdentity identity, Instant observedAt) implements Result {
        public Ended {
            Objects.requireNonNull(identity, "回合结束结果身份不能为空");
            Objects.requireNonNull(observedAt, "回合结束观察时间不能为空");
        }
    }

    /**
     * Python CV 未能在截止前确认当前本方回合离开。
     *
     * @param identity 对应的局内任务身份
     * @param failure 明确失败原因
     */
    record Failed(GameTaskIdentity identity, RecognitionFailure failure) implements Result {
        public Failed {
            Objects.requireNonNull(identity, "回合结束失败身份不能为空");
            Objects.requireNonNull(failure, "回合结束识别失败不能为空");
        }
    }
}
