package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/**
 * Java 权威阶段变化事件。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 PHASE_CHANGED
 * @param sequence 当前 Java 实例内单调递增的业务序号
 * @param emittedAt 阶段变化发生时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param phase Java 权威牌局阶段
 * @param dealId 当前牌局标识；WAIT_NEW_GAME 尚无牌局时为空字符串
 */
public record DesktopPhaseChangedEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        String phase,
        String dealId
) implements DesktopEvent {
}
