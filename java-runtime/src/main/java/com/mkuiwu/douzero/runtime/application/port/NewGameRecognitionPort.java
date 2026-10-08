package com.mkuiwu.douzero.runtime.application.port;

import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;

import java.time.Instant;
import java.util.Objects;

/** 只负责在 WAIT_NEW_GAME 中等待一个可靠的新局边界。 */
public interface NewGameRecognitionPort {
    /**
     * 等待新局；Python CV 自己截图、稳定和恢复，Java 不解释旧局画面。
     *
     * @param request 本次无局态任务
     * @return 可由程序停止或新局确认后取消的任务
     */
    RecognitionJob<Result> waitForNewGame(Request request);

    /**
     * 新局识别请求。
     *
     * @param requestId 单次任务标识；WAIT_NEW_GAME 中重提任务时必须更新
     * @param deadlineMs Python CV 交付本次结果的最长时间，单位为毫秒
     */
    record Request(String requestId, long deadlineMs) {
        public Request {
            requireText(requestId, "新局任务标识不能为空");
            requireDeadline(deadlineMs);
        }
    }

    /** 新局任务只可能确认边界或明确失败。 */
    sealed interface Result permits Detected, Failed {
        /** 返回产生结果的请求标识。 */
        String requestId();
    }

    /**
     * 已稳定确认新局边界；dealId 和 generation 由 Java 随后创建。
     *
     * @param requestId 对应的新局任务标识
     * @param observedAt 新局边界的业务观察时间
     */
    record Detected(
            String requestId,
            Instant observedAt
    ) implements Result {
        public Detected {
            requireText(requestId, "新局结果任务标识不能为空");
            Objects.requireNonNull(observedAt, "新局观察时间不能为空");
        }
    }

    /**
     * Python CV 无法在本次任务内确认新局。
     *
     * @param requestId 对应的新局任务标识
     * @param failure 明确失败原因
     */
    record Failed(String requestId, RecognitionFailure failure) implements Result {
        public Failed {
            requireText(requestId, "新局失败任务标识不能为空");
            Objects.requireNonNull(failure, "新局识别失败不能为空");
        }
    }

    private static void requireDeadline(long deadlineMs) {
        if (deadlineMs <= 0) {
            throw new IllegalArgumentException("新局识别截止时间必须为正数");
        }
    }

    private static void requireText(String value, String message) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(message);
        }
    }
}
