package com.mkuiwu.douzero.runtime.infrastructure.execution;

/** execution.v1 协议传输层异常；表示 Python 执行 Worker 不可用或协议污染。 */
public class ExecutionTransportException extends RuntimeException {
    public ExecutionTransportException(String message) {
        super(message);
    }

    public ExecutionTransportException(String message, Throwable cause) {
        super(message, cause);
    }
}
