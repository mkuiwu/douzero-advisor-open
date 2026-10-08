package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionCancel;

import java.util.Objects;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.atomic.AtomicBoolean;

/** JsonLineRecognitionClient 的通用业务端口任务句柄。 */
final class JsonLineRecognitionJob<R> implements RecognitionJob<R> {
    /** 单次业务任务标识。 */
    private final String requestId;

    /** 已适配为业务结果的完成阶段。 */
    private final CompletionStage<R> completion;

    /** 传输客户端。 */
    private final JsonLineRecognitionClient client;

    /** 与原提交身份完全一致的取消消息。 */
    private final RecognitionCancel cancel;

    /** 防止业务层重复取消产生重复传输。 */
    private final AtomicBoolean cancelled = new AtomicBoolean();

    JsonLineRecognitionJob(String requestId, CompletionStage<R> completion,
                           JsonLineRecognitionClient client, RecognitionCancel cancel) {
        this.requestId = Objects.requireNonNull(requestId, "识别任务标识不能为空");
        this.completion = Objects.requireNonNull(completion, "识别完成阶段不能为空");
        this.client = Objects.requireNonNull(client, "识别客户端不能为空");
        this.cancel = Objects.requireNonNull(cancel, "识别取消消息不能为空");
    }

    @Override
    public String requestId() {
        return requestId;
    }

    @Override
    public CompletionStage<R> completion() {
        return completion;
    }

    @Override
    public void cancel() {
        if (cancelled.compareAndSet(false, true)) {
            client.cancel(cancel);
        }
    }
}
