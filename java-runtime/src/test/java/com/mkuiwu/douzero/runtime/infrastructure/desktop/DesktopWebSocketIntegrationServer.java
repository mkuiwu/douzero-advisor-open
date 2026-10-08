package com.mkuiwu.douzero.runtime.infrastructure.desktop;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.domain.GamePhase;

import java.time.Duration;

/** 仅供跨语言验收启动的受控 Java 网关进程，不进入生产制品。 */
public final class DesktopWebSocketIntegrationServer {
    private DesktopWebSocketIntegrationServer() {
    }

    /** 启动随机回环端口、预置一个权威阶段事件，并在标准输入收到结束信号后退出。 */
    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 1 || arguments[0].length() < 32) {
            throw new IllegalArgumentException("跨语言验收必须传入至少三十二字符的临时令牌");
        }
        try (DesktopWebSocketGateway gateway = new DesktopWebSocketGateway(
                0, arguments[0], true, new ObjectMapper())) {
            gateway.startAndAwait(Duration.ofSeconds(3));
            gateway.onPhaseChanged(GamePhase.PLAYING, "integration-deal-1");
            System.out.println("DESKTOP_WS_PORT=" + gateway.boundPort());
            System.out.flush();
            System.in.read();
        }
    }
}
