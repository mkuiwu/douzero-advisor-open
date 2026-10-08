package com.mkuiwu.douzero.runtime.application.port;

import java.util.concurrent.CompletionStage;

/**
 * 一个正在执行的 Python CV 业务任务；只抽象所有识别能力都一致的取消和完成语义。
 *
 * @param <R> 该业务端口自己的最终结果类型
 */
public interface RecognitionJob<R> {
    /** 返回提交请求的标识，用于 GameContext 持有和核对任务。 */
    String requestId();

    /** 返回最终成功或明确失败；不得发布截图、候选帧或内部重试进度。 */
    CompletionStage<R> completion();

    /** 取消整个业务任务；实现必须幂等。 */
    void cancel();
}
