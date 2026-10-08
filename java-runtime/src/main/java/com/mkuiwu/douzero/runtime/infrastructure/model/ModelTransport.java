package com.mkuiwu.douzero.runtime.infrastructure.model;

/** 模型线协议的传输边界，可由 HTTP、进程管道或测试 Fake 实现。 */
public interface ModelTransport<REQUEST, RESPONSE> {
    /**
     * 发送一个已经编码的模型请求并等待对应响应。
     *
     * @param request 某个模型明确类型的线协议请求
     * @return 同一模型明确类型的线协议响应
     * @throws ModelTransportException 连接不可用或超过请求截止时间时抛出
     */
    RESPONSE exchange(REQUEST request);
}
