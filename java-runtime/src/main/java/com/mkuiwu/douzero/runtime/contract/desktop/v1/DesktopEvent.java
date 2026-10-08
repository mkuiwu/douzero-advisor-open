package com.mkuiwu.douzero.runtime.contract.desktop.v1;

/**
 * Java Runtime 向 Electron 发布的一条 desktop.v1 只读事件。
 *
 * <p>序号只对同一个 serverInstanceId 有意义；READY 的序号固定为零，业务事件从一开始递增。</p>
 */
public sealed interface DesktopEvent permits DesktopReadyEvent, DesktopPhaseChangedEvent,
        DesktopPreplayAdviceEvent, DesktopPlayAdviceEvent, DesktopPlayTurnStartedEvent,
        DesktopGameFinishedEvent {
    /** 返回固定协议版本 desktop.v1。 */
    String protocol();

    /** 返回稳定事件类型。 */
    DesktopEventType type();

    /** 返回当前 Java 进程内单调递增的业务事件序号。 */
    long sequence();

    /** 返回事件产生时的 ISO-8601 时间。 */
    String emittedAt();

    /** 返回当前 Java 进程的随机实例标识，用于隔离重启前后的序号。 */
    String serverInstanceId();
}
