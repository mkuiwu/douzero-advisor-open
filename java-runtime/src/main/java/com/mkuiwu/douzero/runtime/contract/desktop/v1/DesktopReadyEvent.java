package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/**
 * 鉴权成功后的 desktop.v1 能力声明。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 READY
 * @param sequence 固定为零，不参与业务事件去重
 * @param emittedAt READY 产生时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param orchestrationEnabled Java 状态机是否已显式启用；false 时连接只证明网关可用
 */
public record DesktopReadyEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        boolean orchestrationEnabled
) implements DesktopEvent {
}
