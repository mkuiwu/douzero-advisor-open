package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/**
 * 当前 GameContext 已收口的事件。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 GAME_FINISHED
 * @param sequence 当前 Java 实例内单调递增的业务序号
 * @param emittedAt 上下文收口时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param dealId 已收口牌局的唯一标识
 * @param reason Java 权威的稳定收口原因
 */
public record DesktopGameFinishedEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        String dealId,
        String reason
) implements DesktopEvent {
}
