package com.mkuiwu.douzero.runtime.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.infrastructure.desktop.DesktopWebSocketGateway;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;
import java.util.Objects;

/** 按显式配置启动独立的本机 desktop.v1 网关，不改变 Spring 的非 Web 应用类型。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(RuntimeProperties.class)
public class DesktopWebSocketConfiguration {
    /**
     * 创建并启动唯一桌面网关；该 Bean 同时实现只读 GameRuntimeObserver 端口。
     */
    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero.desktop", name = "enabled", havingValue = "true")
    DesktopWebSocketGateway desktopWebSocketGateway(
            RuntimeProperties properties,
            ObjectMapper objectMapper
    ) {
        RuntimeProperties.Desktop desktop = Objects.requireNonNull(
                properties.desktop(), "启用桌面网关时必须配置 douzero.desktop");
        boolean orchestrationEnabled = properties.orchestration() != null
                && properties.orchestration().enabled()
                && properties.recognition() != null
                && properties.recognition().enabled();
        DesktopWebSocketGateway gateway = new DesktopWebSocketGateway(
                desktop.port(), desktop.token(), orchestrationEnabled, objectMapper);
        gateway.startAndAwait(Duration.ofMillis(desktop.startupTimeoutMs()));
        return gateway;
    }
}
