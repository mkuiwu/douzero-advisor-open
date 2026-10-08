package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.ExecutionFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.time.Instant;
import java.util.Objects;

/**
 * Java Core 面向 Python 执行 Worker 的本方出牌执行端口。
 *
 * <p>该端口只有一个 {@link #execute} 方法，用 {@link ExecutionRequest#autoSubmit()}
 * 参数控制选牌后是否真正点击提交按钮：
 * <ul>
 *   <li>{@code autoSubmit=false}：只执行选牌+视觉验证，不点击出牌/不出按钮。用于
 *       在不信任自动提交时先验证选牌准确率，Java 拿到 {@link Executed} 后由用户手工提交。</li>
 *   <li>{@code autoSubmit=true}：选牌验证通过后自动点击对应动作按钮；效果由 Java 的 TURN_END 任务确认。</li>
 * </ul>
 * </p>
 *
 * <p>Python 内部仍保留"选牌→验证→（条件）点击→验证"的两阶段实现，但对 Java 不暴露。
 * 端口不暴露任何鼠标坐标、像素位置、窗口句柄或点击延迟。</p>
 *
 * <p>所有执行任务都必须携带 Java 本局身份；Python 在点击前校验代际仍匹配，
 * 代际过期即拒绝。不确定结果（{@link ExecutionUncertain}）是 fail-closed 信号，
 * Java 必须重新识别当前状态，绝不能假设成功。</p>
 */
public interface CardPlayExecutionPort {
    /**
     * 执行本方出牌：按 Java 权威建议选牌，并根据 {@code autoSubmit} 决定是否提交。
     *
     * <p>Python 必须先截图核对当前手牌与 {@code authoritativeHand} 完全一致，
     * 不一致直接返回 {@link ExecutionRejected}，不尝试选牌。</p>
     *
     * @param request 包含任务身份、本方座位、Java 权威手牌、建议出的牌、动作类型和是否自动提交
     * @return 可取消的异步任务，完成时返回已执行、明确失败或不确定
     */
    RecognitionJob<ExecutionResult> execute(ExecutionRequest request);

    /**
     * 出牌执行请求。
     *
     * @param identity 当前牌局和任务身份
     * @param localSeat 本方稳定座位
     * @param authoritativeHand Java 权威当前手牌，Python 必须截图核对完全一致
     * @param recommendedCards 建议出的牌，必须是权威手牌的子集；PASS 时必须为空
     * @param actionType 出牌（PLAY）或不出（PASS）
     * @param autoSubmit true 表示选牌验证通过后自动点击提交按钮；false 表示只选牌不提交
     * @param deadlineMs 本次执行任务的硬截止时间，单位为毫秒
     */
    record ExecutionRequest(
            GameTaskIdentity identity,
            Seat localSeat,
            CardSet authoritativeHand,
            CardSet recommendedCards,
            ActionType actionType,
            boolean autoSubmit,
            long deadlineMs
    ) {
        public ExecutionRequest {
            Objects.requireNonNull(identity, "执行任务身份不能为空");
            Objects.requireNonNull(localSeat, "本方座位不能为空");
            Objects.requireNonNull(authoritativeHand, "权威手牌不能为空");
            if (authoritativeHand.isEmpty()) {
                throw new IllegalArgumentException("权威手牌不能为空");
            }
            Objects.requireNonNull(actionType, "动作类型不能为空");
            Objects.requireNonNull(recommendedCards, "建议出的牌不能为空");
            if (actionType == ActionType.PLAY) {
                if (recommendedCards.isEmpty()) {
                    throw new IllegalArgumentException("出牌动作的建议牌不能为空");
                }
                if (!authoritativeHand.cards().containsAll(recommendedCards.cards())) {
                    throw new IllegalArgumentException("建议出的牌必须是权威手牌的子集");
                }
            } else if (!recommendedCards.isEmpty()) {
                throw new IllegalArgumentException("不出动作的建议牌必须为空");
            }
            if (deadlineMs <= 0) {
                throw new IllegalArgumentException("执行截止时间必须为正数");
            }
        }
    }

    /** 提交阶段的动作类型。 */
    enum ActionType {
        /** 出牌：选择 recommendedCards 后点击出牌按钮。 */
        PLAY,
        /** 不出：不选牌，直接点击不出按钮。 */
        PASS
    }

    /** 执行任务只可能确认成功、明确失败或不确定。 */
    sealed interface ExecutionResult permits Executed, ExecutionRejected, ExecutionUncertain {
        /** 返回产生结果的局内任务身份。 */
        GameTaskIdentity identity();
    }

    /**
     * 执行已确认。
     *
     * <p>当 {@code autoSubmit=false} 时表示选牌已视觉验证通过（未点击提交按钮，等待用户手工操作）；
     * 当 {@code autoSubmit=true} 时表示物理点击已发出；按钮消失或状态区变化由后续 TURN_END 确认。</p>
     *
     * @param identity 对应的局内任务身份
     * @param autoSubmitEcho 回显请求中的 autoSubmit，供 Java 区分"仅选牌"和"已提交"
     * @param verifiedAt Python 视觉验证通过的业务时间
     */
    record Executed(
            GameTaskIdentity identity,
            boolean autoSubmitEcho,
            Instant verifiedAt
    ) implements ExecutionResult {
        public Executed {
            Objects.requireNonNull(identity, "执行确认身份不能为空");
            Objects.requireNonNull(verifiedAt, "执行确认时间不能为空");
        }
    }

    /**
     * 执行明确失败，不应重试。
     *
     * @param identity 对应的局内任务身份
     * @param failure 稳定的内部失败原因
     * @param detail 面向诊断的简短说明，不得包含截图或坐标
     */
    record ExecutionRejected(
            GameTaskIdentity identity,
            ExecutionFailure failure,
            String detail
    ) implements ExecutionResult {
        public ExecutionRejected {
            Objects.requireNonNull(identity, "执行失败身份不能为空");
            Objects.requireNonNull(failure, "执行失败原因不能为空");
            Objects.requireNonNull(detail, "执行失败说明不能为空");
            if (detail.isBlank()) {
                throw new IllegalArgumentException("执行失败说明不能为空");
            }
        }
    }

    /**
     * 不确定：可能已点击但无法确认结果。
     *
     * <p>这是 fail-closed 信号。Java 必须重新走 LOCAL_TURN 识别当前状态，
     * 绝不能假设成功或继续下一轮。</p>
     *
     * @param identity 对应的局内任务身份
     * @param detail 面向诊断的简短说明，不得包含截图或坐标
     */
    record ExecutionUncertain(
            GameTaskIdentity identity,
            String detail
    ) implements ExecutionResult {
        public ExecutionUncertain {
            Objects.requireNonNull(identity, "执行不确定身份不能为空");
            Objects.requireNonNull(detail, "执行不确定说明不能为空");
            if (detail.isBlank()) {
                throw new IllegalArgumentException("执行不确定说明不能为空");
            }
        }
    }
}
