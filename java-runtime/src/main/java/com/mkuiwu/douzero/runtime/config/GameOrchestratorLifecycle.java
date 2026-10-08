package com.mkuiwu.douzero.runtime.config;

import com.mkuiwu.douzero.runtime.application.GameOrchestrator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationListener;

import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 把 Spring 应用生命周期连接到 Java 编排器；只有 ApplicationReady 后才开始等待新局。
 */
public final class GameOrchestratorLifecycle
        implements ApplicationListener<ApplicationReadyEvent>, AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(GameOrchestratorLifecycle.class);

    /** 当前 Spring 上下文唯一的牌局编排器。 */
    private final GameOrchestrator orchestrator;

    /** 是否已经消费过当前上下文的 ApplicationReady 事件。 */
    private final AtomicBoolean started = new AtomicBoolean();

    public GameOrchestratorLifecycle(GameOrchestrator orchestrator) {
        this.orchestrator = Objects.requireNonNull(orchestrator, "牌局编排器不能为空");
    }

    /** ApplicationReady 可能被重复发布，但编排器只能启动一次。 */
    @Override
    public void onApplicationEvent(ApplicationReadyEvent event) {
        if (started.compareAndSet(false, true)) {
            orchestrator.start();
        }
    }

    /** Spring 关闭时等待事件线程完成任务取消，随后执行器 Bean 才能安全销毁。 */
    @Override
    public void close() {
        try {
            orchestrator.stopAsync().toCompletableFuture().get(5, TimeUnit.SECONDS);
        } catch (Exception error) {
            LOGGER.error("等待牌局编排器停止失败", error);
        }
    }
}
