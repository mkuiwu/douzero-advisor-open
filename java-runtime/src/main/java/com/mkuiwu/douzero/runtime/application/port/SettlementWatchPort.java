package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;

import java.time.Instant;
import java.util.Objects;

/** 负责当前 GameContext 整局有效的结算旁路任务。 */
public interface SettlementWatchPort {
    /**
     * 从 GameContext 创建开始等待结算页；成功后可抢占任何正常状态。
     *
     * @param request 当前牌局的结算旁路任务
     * @return 由结算确认或 GameContext 关闭终止的任务
     */
    RecognitionJob<Result> watch(Request request);

    /**
     * 结算旁路请求。
     *
     * @param identity 当前牌局和任务身份；整局内保持同一任务
     * @param deadlineMs Python CV 本次结算旁路任务的最长存活时间，单位为毫秒
     */
    record Request(GameTaskIdentity identity, long deadlineMs) {
        public Request {
            Objects.requireNonNull(identity, "结算任务身份不能为空");
            if (deadlineMs <= 0) {
                throw new IllegalArgumentException("结算任务截止时间必须为正数");
            }
        }
    }

    /** 结算旁路只可能确认结算或明确失败。 */
    sealed interface Result permits Detected, Failed {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 已稳定确认当前局结算页。
     *
     * @param identity 对应的局内任务身份
     * @param observedAt 结算页的业务观察时间
     */
    record Detected(
            GameTaskIdentity identity,
            Instant observedAt
    ) implements Result {
        public Detected {
            Objects.requireNonNull(identity, "结算结果身份不能为空");
            Objects.requireNonNull(observedAt, "结算观察时间不能为空");
        }
    }

    /**
     * Python CV 无法继续当前结算旁路任务。
     *
     * @param identity 对应的局内任务身份
     * @param failure 明确失败原因
     */
    record Failed(
            GameTaskIdentity identity,
            RecognitionFailure failure
    ) implements Result {
        public Failed {
            Objects.requireNonNull(identity, "结算失败身份不能为空");
            Objects.requireNonNull(failure, "结算识别失败不能为空");
        }
    }
}
