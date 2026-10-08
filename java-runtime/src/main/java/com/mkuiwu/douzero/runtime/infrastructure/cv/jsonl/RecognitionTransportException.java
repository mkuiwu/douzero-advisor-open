package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

/** Python CV 进程、管道或线协议失效时交付给全部待处理任务的明确异常。 */
public final class RecognitionTransportException extends RuntimeException {
    public RecognitionTransportException(String message) {
        super(message);
    }

    public RecognitionTransportException(String message, Throwable cause) {
        super(message, cause);
    }
}
