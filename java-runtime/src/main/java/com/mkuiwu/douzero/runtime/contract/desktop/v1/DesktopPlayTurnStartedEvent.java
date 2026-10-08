package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/**
 * Java 已确认本方回合、但正式模型尚未返回结果的状态事件。
 *
 * @param protocol 固定协议版本 desktop.v1
 * @param type 固定事件类型 PLAY_TURN_STARTED
 * @param sequence 当前 Java 实例内单调递增的业务序号
 * @param emittedAt 本方回合确认时的 ISO-8601 时间
 * @param serverInstanceId 当前 Java 进程的随机实例标识
 * @param dealId 当前牌局标识
 * @param phase 固定为 PLAYING
 * @param localSeat 本方在地主坐标系中的稳定座位
 * @param currentSeat 当前应行动座位；必须与 localSeat 相同
 */
public record DesktopPlayTurnStartedEvent(
        String protocol,
        DesktopEventType type,
        long sequence,
        String emittedAt,
        String serverInstanceId,
        String dealId,
        String phase,
        String localSeat,
        String currentSeat
) implements DesktopEvent {
}
