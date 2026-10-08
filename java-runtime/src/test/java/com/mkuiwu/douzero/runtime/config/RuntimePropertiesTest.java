package com.mkuiwu.douzero.runtime.config;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class RuntimePropertiesTest {
    /** 验证关闭的 worker 可以省略命令，而启用时必须提供完整可启动配置。 */
    @Test
    void disabledWorkersMayHaveNoCommandButEnabledWorkersMustBeFullyConfigured() {
        assertDoesNotThrow(() -> new RuntimeProperties.Recognition(false, List.of(), "", 5000));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Recognition(true, List.of(), "manifest.json", 5000));
        assertDoesNotThrow(() -> new RuntimeProperties.Model(
                false, List.of(), "", 1500));
        assertThrows(IllegalArgumentException.class, () -> new RuntimeProperties.Model(
                true, List.of(), "resnet2", 1500));
        assertThrows(IllegalArgumentException.class, () -> new RuntimeProperties.Model(
                true, List.of("python"), "", 1500));
        assertDoesNotThrow(() -> new RuntimeProperties.PreplayModel(
                false, List.of(), "", "", 1500));
        assertThrows(IllegalArgumentException.class, () -> new RuntimeProperties.PreplayModel(
                true, List.of("python"), "", "bid-v1", 1500));
    }

    /** 验证所有 worker 超时始终为正数，防止启用状态变化后带入无效配置。 */
    @Test
    void workerTimeoutsMustRemainPositiveEvenWhenDisabled() {
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Model(false, List.of(), "", 0));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Recognition(false, List.of(), "", 0));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.PreplayModel(false, List.of(), "", "", 0));
    }

    /** 验证编排器各阶段截止时间均为正数，避免任务立即超时或永不受控。 */
    @Test
    void allOrchestrationDeadlinesMustBePositive() {
        assertDoesNotThrow(() -> orchestration(1, 1, 1, 1, 1, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(0, 1, 1, 1, 1, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 0, 1, 1, 1, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 0, 1, 1, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 1, 0, 1, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 1, 1, 0, 1, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 1, 1, 1, 0, 1, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 1, 1, 1, 1, 0, 1));
        assertThrows(IllegalArgumentException.class,
                () -> orchestration(1, 1, 1, 1, 1, 1, 1, 0));
    }

    /** 验证桌面网关只有在端口、启动超时和每次启动令牌均有效时才允许启用。 */
    @Test
    void enabledDesktopGatewayRequiresValidLoopbackPortTimeoutAndLaunchToken() {
        assertDoesNotThrow(() -> new RuntimeProperties.Desktop(
                true, 17373, "0123456789abcdef0123456789abcdef", 5000));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Desktop(true, 0, "0123456789abcdef0123456789abcdef", 5000));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Desktop(true, 17373, "too-short", 5000));
        assertThrows(IllegalArgumentException.class,
                () -> new RuntimeProperties.Desktop(false, 17373, "", 0));
    }

    private static RuntimeProperties.Orchestration orchestration(
            long newGameMs,
            long preplayRecognitionMs,
            long dealMs,
            long settlementMs,
            long localTurnMs,
            long turnEndMs,
            long preplayDecisionMs,
            long playDecisionMs
    ) {
        return new RuntimeProperties.Orchestration(
                true, newGameMs, preplayRecognitionMs, dealMs, settlementMs,
                localTurnMs, turnEndMs, preplayDecisionMs, playDecisionMs);
    }
}
