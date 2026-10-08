package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.ExecutionFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;

import java.time.Instant;
import java.util.Objects;

/** Java Core 面向局前按钮执行 Worker 的显式端口。 */
public interface PreplayButtonExecutionPort {
    /** 仅执行可点击的正向局前动作；NO_* 动作不应创建请求。 */
    RecognitionJob<ExecutionResult> execute(ExecutionRequest request);

    /** 局前按钮执行请求。 */
    record ExecutionRequest(
            GameTaskIdentity identity,
            PreplayStage stage,
            PreplayAction action,
            long deadlineMs
    ) {
        public ExecutionRequest {
            Objects.requireNonNull(identity, "局前执行任务身份不能为空");
            Objects.requireNonNull(stage, "局前执行阶段不能为空");
            Objects.requireNonNull(action, "局前执行动作不能为空");
            if (action.stage() != stage) {
                throw new IllegalArgumentException("局前执行动作与阶段不匹配");
            }
            if (action == PreplayAction.NO_CALL
                    || action == PreplayAction.NO_ROB
                    || action == PreplayAction.NO_DOUBLE) {
                throw new IllegalArgumentException("不叫、不抢和不加倍动作禁止自动点击");
            }
            if (deadlineMs <= 0) {
                throw new IllegalArgumentException("局前执行截止时间必须为正数");
            }
        }
    }

    /** 局前点击任务只能确认成功、明确失败或不确定。 */
    sealed interface ExecutionResult permits Executed, ExecutionRejected, ExecutionUncertain {
        GameTaskIdentity identity();
    }

    /** 按钮已点击且局前阶段已离开或目标按钮已消失。 */
    record Executed(GameTaskIdentity identity, Instant verifiedAt) implements ExecutionResult {
        public Executed {
            Objects.requireNonNull(identity, "局前执行确认身份不能为空");
            Objects.requireNonNull(verifiedAt, "局前执行确认时间不能为空");
        }
    }

    /** 点击前找不到可信按钮或执行任务明确失败。 */
    record ExecutionRejected(
            GameTaskIdentity identity,
            ExecutionFailure failure,
            String detail
    ) implements ExecutionResult {
        public ExecutionRejected {
            Objects.requireNonNull(identity, "局前执行失败身份不能为空");
            Objects.requireNonNull(failure, "局前执行失败原因不能为空");
            if (detail == null || detail.isBlank()) {
                throw new IllegalArgumentException("局前执行失败说明不能为空");
            }
        }
    }

    /** 点击可能已经发生但无法确认后续状态。 */
    record ExecutionUncertain(GameTaskIdentity identity, String detail) implements ExecutionResult {
        public ExecutionUncertain {
            Objects.requireNonNull(identity, "局前执行不确定身份不能为空");
            if (detail == null || detail.isBlank()) {
                throw new IllegalArgumentException("局前执行不确定说明不能为空");
            }
        }
    }
}
