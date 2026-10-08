package com.mkuiwu.douzero.runtime.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.application.GameOrchestrator;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.DecisionPort;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.JsonLineRecognitionClient;
import com.mkuiwu.douzero.runtime.infrastructure.desktop.DesktopWebSocketGateway;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.JsonLineResNet2ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ModelAdvisor;
import org.junit.jupiter.api.Test;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.LockSupport;
import java.util.function.BooleanSupplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GameRuntimeConfigurationTest {
    private static final String[] ENABLED_PROPERTIES = {
            "douzero.recognition.enabled=true",
            "douzero.recognition.command[0]=python",
            "douzero.recognition.calibration-manifest=manifest.json",
            "douzero.recognition.startup-timeout-ms=5000",
            "douzero.model.name=resnet2",
            "douzero.model.timeout-ms=1500",
            "douzero.orchestration.enabled=true",
            "douzero.orchestration.new-game-ms=30000",
            "douzero.orchestration.preplay-recognition-ms=15000",
            "douzero.orchestration.deal-ms=45000",
            "douzero.orchestration.settlement-ms=600000",
            "douzero.orchestration.local-turn-ms=30000",
            "douzero.orchestration.turn-end-ms=30000",
            "douzero.orchestration.preplay-decision-ms=8000",
            "douzero.orchestration.play-decision-ms=1500"
    };

    /** 验证默认关闭识别时不会创建或启动模型传输，保持无副作用启动。 */
    @Test
    void defaultDisabledConfigurationDoesNotCreateOrLaunchModelTransport() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        CountingLauncherConfiguration.class,
                        RuntimeConfiguration.class,
                        GameRuntimeConfiguration.class)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).doesNotHaveBean(JsonLineResNet2ModelTransport.class);
                    assertThat(context.getBean(CountingProcessLauncher.class).launchCount())
                            .isZero();
                });
    }

    /** 验证正式模型服务可在未启用识别和 Java 编排器时独立完成 READY 握手。 */
    @Test
    void modelServiceCanStartWithoutRecognitionOrchestration() {
        new ApplicationContextRunner()
                .withUserConfiguration(ModelOnlyConfiguration.class, RuntimeConfiguration.class)
                .withPropertyValues(
                        "douzero.recognition.enabled=false",
                        "douzero.recognition.command[0]=unused",
                        "douzero.recognition.startup-timeout-ms=5000",
                        "douzero.model.enabled=true",
                        "douzero.model.command[0]=python",
                        "douzero.model.name=resnet2",
                        "douzero.model.timeout-ms=500",
                        "douzero.orchestration.enabled=false",
                        "douzero.orchestration.new-game-ms=30000",
                        "douzero.orchestration.preplay-recognition-ms=15000",
                        "douzero.orchestration.deal-ms=45000",
                        "douzero.orchestration.settlement-ms=600000",
                        "douzero.orchestration.local-turn-ms=30000",
                        "douzero.orchestration.turn-end-ms=30000",
                        "douzero.orchestration.preplay-decision-ms=8000",
                        "douzero.orchestration.play-decision-ms=1500")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(JsonLineResNet2ModelTransport.class);
                    assertThat(context).doesNotHaveBean(JsonLineRecognitionClient.class);
                    assertThat(context.getBean(ReadyModelProcessLauncher.class).launchCount())
                            .isEqualTo(1);
                });
    }

    /** 验证服务 profile 会分别启动识别和正式模型进程，并保持 Java 编排器关闭。 */
    @Test
    void independentServiceProfileStartsRecognitionAndModelSeparately() {
        new ApplicationContextRunner()
                .withUserConfiguration(IndependentServicesConfiguration.class, RuntimeConfiguration.class)
                .withPropertyValues(
                        "douzero.recognition.enabled=true",
                        "douzero.recognition.command[0]=recognition-service",
                        "douzero.recognition.calibration-manifest=manifest.json",
                        "douzero.recognition.startup-timeout-ms=5000",
                        "douzero.model.enabled=true",
                        "douzero.model.command[0]=model-service",
                        "douzero.model.name=resnet2",
                        "douzero.model.timeout-ms=500",
                        "douzero.orchestration.enabled=false",
                        "douzero.orchestration.new-game-ms=30000",
                        "douzero.orchestration.preplay-recognition-ms=15000",
                        "douzero.orchestration.deal-ms=45000",
                        "douzero.orchestration.settlement-ms=600000",
                        "douzero.orchestration.local-turn-ms=30000",
                        "douzero.orchestration.turn-end-ms=30000",
                        "douzero.orchestration.preplay-decision-ms=8000",
                        "douzero.orchestration.play-decision-ms=1500")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(JsonLineRecognitionClient.class);
                    assertThat(context).hasSingleBean(JsonLineResNet2ModelTransport.class);
                    assertThat(context).doesNotHaveBean(GameOrchestrator.class);
                    assertThat(context.getBean(IndependentServicesProcessLauncher.class)
                            .launchCount()).isEqualTo(2);
                });
    }

    /** 验证识别能力关闭时，即使局前模型开关开启也不会误启动 Python worker。 */
    @Test
    void disabledRecognitionSuppressesEnabledPreplayModelAndNeverLaunchesWorker() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        CountingLauncherConfiguration.class,
                        RuntimeConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(
                        "douzero.recognition.enabled=false",
                        "douzero.recognition.command[0]=unused",
                        "douzero.recognition.startup-timeout-ms=5000",
                        "douzero.preplay-model.enabled=true",
                        "douzero.preplay-model.command[0]=python",
                        "douzero.preplay-model.legacy-root=/unused/legacy",
                        "douzero.preplay-model.model-id=bid-v1",
                        "douzero.preplay-model.timeout-ms=1500",
                        "douzero.model.enabled=true",
                        "douzero.model.command[0]=unused-model-worker",
                        "douzero.model.name=resnet2",
                        "douzero.model.timeout-ms=1500")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).doesNotHaveBean(GameOrchestrator.class);
                    assertThat(context).doesNotHaveBean(GameOrchestratorLifecycle.class);
                    assertThat(context).doesNotHaveBean(PreplayDecisionPort.class);
                    assertThat(context.getBean(CountingProcessLauncher.class).launchCount())
                            .isZero();
                });
    }

    /** 验证正式模型开启后会装配生产 JSONL 传输和真实 DecisionPort。 */
    @Test
    void enabledModelCreatesProductionJsonLineTransportAndRealDecisionPort() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        ProductionDecisionCapabilitiesConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(ENABLED_PROPERTIES)
                .withPropertyValues(
                        "douzero.model.enabled=true",
                        "douzero.model.command[0]=python",
                        "douzero.model.command[1]=resnet2-worker.py",
                        "douzero.model.timeout-ms=500")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(JsonLineResNet2ModelTransport.class);
                    assertThat(context).hasSingleBean(DecisionPort.class);
                    assertThat(context.getBean(DecisionPort.class))
                            .isInstanceOf(ResNet2ModelAdvisor.class);
                    assertThat(context.getBean(ReadyModelProcessLauncher.class).launchCount())
                            .isEqualTo(1);
                });
    }

    /** 验证应用就绪后开始等待新局，Spring 关闭时会取消任务并回收执行器。 */
    @Test
    void readyEventStartsWaitingAndContextCloseCancelsJobAndExecutors() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        CompleteCapabilitiesConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(ENABLED_PROPERTIES)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    TrackingNewGamePort newGamePort = context.getBean(TrackingNewGamePort.class);
                    ExecutorService eventExecutor = context.getBean(
                            "gameEventExecutor", ExecutorService.class);
                    ExecutorService decisionExecutor = context.getBean(
                            "gameDecisionExecutor", ExecutorService.class);
                    ScheduledExecutorService timeoutScheduler = context.getBean(
                            "gameTimeoutScheduler", ScheduledExecutorService.class);
                    CountDownLatch decisionStarted = new CountDownLatch(1);
                    AtomicBoolean decisionInterrupted = new AtomicBoolean();

                    context.publishEvent(new ApplicationReadyEvent(
                            new SpringApplication(),
                            new String[0],
                            context.getSourceApplicationContext(),
                            Duration.ZERO));

                    await(() -> newGamePort.request() != null, "ApplicationReady 后未开始等待新局");
                    assertThat(newGamePort.request().deadlineMs()).isEqualTo(30000);
                    decisionExecutor.execute(() -> {
                        decisionStarted.countDown();
                        try {
                            new CountDownLatch(1).await();
                        } catch (InterruptedException interrupted) {
                            decisionInterrupted.set(true);
                            Thread.currentThread().interrupt();
                        }
                    });
                    await(() -> decisionStarted.getCount() == 0,
                            "测试决策任务必须先进入在途状态");

                    context.getSourceApplicationContext().close();

                    assertTrue(newGamePort.job().cancelled(), "关闭上下文必须取消新局任务");
                    await(decisionInterrupted::get, "关闭上下文必须中断在途决策任务");
                    assertThat(eventExecutor.isShutdown()).isTrue();
                    assertThat(decisionExecutor.isShutdown()).isTrue();
                    assertThat(timeoutScheduler.isShutdown()).isTrue();
                    assertThat(eventExecutor.awaitTermination(1, TimeUnit.SECONDS)).isTrue();
                    assertThat(decisionExecutor.awaitTermination(1, TimeUnit.SECONDS)).isTrue();
                    assertThat(timeoutScheduler.awaitTermination(1, TimeUnit.SECONDS)).isTrue();
                });
    }

    /** 验证启用桌面网关后，状态机把权威事件交给 WS 网关而不是默认日志观察器。 */
    @Test
    void enabledDesktopGatewayBecomesTheRuntimeObserver() throws IOException {
        int port;
        try (ServerSocket socket = new ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        new ApplicationContextRunner()
                .withBean(ObjectMapper.class, ObjectMapper::new)
                .withUserConfiguration(
                        CompleteCapabilitiesConfiguration.class,
                        DesktopWebSocketConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(ENABLED_PROPERTIES)
                .withPropertyValues(
                        "douzero.desktop.enabled=true",
                        "douzero.desktop.port=" + port,
                        "douzero.desktop.token=0123456789abcdef0123456789abcdef",
                        "douzero.desktop.startup-timeout-ms=5000")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context.getBean(GameRuntimeObserver.class))
                            .isSameAs(context.getBean(DesktopWebSocketGateway.class));
                });
    }

    /** 验证同时关闭两类建议时不启动模型 worker，Java 仍可识别、跟踪并安全收口牌局。 */
    @Test
    void disabledAdviceCapabilitiesKeepTheReadOnlyRuntimeAvailableWithoutModelWorkers() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        RecognitionWithoutAdviceCapabilitiesConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(ENABLED_PROPERTIES)
                .withPropertyValues(
                        "douzero.model.enabled=false",
                        "douzero.preplay-model.enabled=false",
                        "douzero.preplay-model.command[0]=unused-preplay-worker",
                        "douzero.preplay-model.timeout-ms=1500")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(GameOrchestrator.class);
                    assertThat(context).hasSingleBean(DecisionPort.class);
                    assertThat(context).hasSingleBean(PreplayDecisionPort.class);
                    assertThat(context).doesNotHaveBean(JsonLineResNet2ModelTransport.class);
                });
    }

    /** 验证缺少唯一正式决策传输时配置会按能力名称快速失败。 */
    @Test
    void missingDecisionTransportFailsFastWithCapabilityName() {
        new ApplicationContextRunner()
                .withUserConfiguration(
                        RecognitionCapabilitiesConfiguration.class,
                        GameRuntimeConfiguration.class)
                .withPropertyValues(ENABLED_PROPERTIES)
                .run(context -> {
                    assertThat(context).hasFailed();
                    assertThat(context.getStartupFailure())
                            .hasStackTraceContaining("douzero.model.enabled")
                            .hasStackTraceContaining("禁止 no-op 建议");
                });
    }

    private static void await(BooleanSupplier condition, String failureMessage) {
        long deadline = System.nanoTime() + Duration.ofSeconds(2).toNanos();
        while (!condition.getAsBoolean() && System.nanoTime() < deadline) {
            LockSupport.parkNanos(Duration.ofMillis(5).toNanos());
        }
        assertTrue(condition.getAsBoolean(), failureMessage);
    }

    @Configuration(proxyBeanMethods = false)
    static class CountingLauncherConfiguration {
        @Bean
        CountingProcessLauncher processLauncher() {
            return new CountingProcessLauncher();
        }
    }

    @Configuration(proxyBeanMethods = false)
    static class RecognitionCapabilitiesConfiguration {
        @Bean
        TrackingNewGamePort newGameRecognitionPort() {
            return new TrackingNewGamePort();
        }

        @Bean
        PreplayRecognitionPort preplayRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        DealRecognitionPort dealRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        LocalTurnRecognitionPort localTurnRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        TurnEndRecognitionPort turnEndRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        SettlementWatchPort settlementWatchPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        PreplayDecisionPort preplayDecisionPort() {
            return query -> null;
        }
    }

    @Configuration(proxyBeanMethods = false)
    static class RecognitionWithoutAdviceCapabilitiesConfiguration {
        @Bean
        TrackingNewGamePort newGameRecognitionPort() {
            return new TrackingNewGamePort();
        }

        @Bean
        PreplayRecognitionPort preplayRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        DealRecognitionPort dealRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        LocalTurnRecognitionPort localTurnRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        TurnEndRecognitionPort turnEndRecognitionPort() {
            return request -> pending(request.identity().requestId());
        }

        @Bean
        SettlementWatchPort settlementWatchPort() {
            return request -> pending(request.identity().requestId());
        }
    }

    @Configuration(proxyBeanMethods = false)
    @Import(RecognitionCapabilitiesConfiguration.class)
    static class CompleteCapabilitiesConfiguration {
        @Bean
        DecisionPort decisionPort() {
            return query -> null;
        }
    }

    @Configuration(proxyBeanMethods = false)
    @Import(RecognitionCapabilitiesConfiguration.class)
    static class ProductionDecisionCapabilitiesConfiguration {
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper();
        }

        @Bean
        ReadyModelProcessLauncher processLauncher() throws IOException {
            return new ReadyModelProcessLauncher();
        }
    }

    @Configuration(proxyBeanMethods = false)
    static class ModelOnlyConfiguration {
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper();
        }

        @Bean
        ReadyModelProcessLauncher processLauncher() throws IOException {
            return new ReadyModelProcessLauncher();
        }
    }

    @Configuration(proxyBeanMethods = false)
    static class IndependentServicesConfiguration {
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper();
        }

        @Bean
        IndependentServicesProcessLauncher processLauncher() {
            return new IndependentServicesProcessLauncher();
        }
    }

    private static final class CountingProcessLauncher implements ProcessLauncher {
        private final AtomicInteger launches = new AtomicInteger();

        @Override
        public Process launch(List<String> command) throws IOException {
            launches.incrementAndGet();
            throw new IOException("测试禁止启动真实进程");
        }

        private int launchCount() {
            return launches.get();
        }
    }

    /** 只声明 ResNet2 READY 的受控进程启动器，用于验证 Spring 生产接线。 */
    private static final class ReadyModelProcessLauncher implements ProcessLauncher {
        private final AtomicInteger launches = new AtomicInteger();
        private final ReadyModelProcess process;

        private ReadyModelProcessLauncher() throws IOException {
            process = new ReadyModelProcess();
        }

        @Override
        public Process launch(List<String> command) {
            launches.incrementAndGet();
            return process;
        }

        private int launchCount() {
            return launches.get();
        }
    }

    /** 按服务命令返回不同 READY 的受控进程，验证 Java 没有把两个服务合并成一个 worker。 */
    private static final class IndependentServicesProcessLauncher implements ProcessLauncher {
        private final AtomicInteger launches = new AtomicInteger();

        @Override
        public Process launch(List<String> command) throws IOException {
            launches.incrementAndGet();
            boolean recognition = command.stream().anyMatch("recognition-service"::equals);
            return new ReadyModelProcess(recognition
                    ? "{\"contractVersion\":\"recognition.v1\","
                    + "\"messageType\":\"READY\",\"taskTypes\":[\"NEW_GAME\","
                    + "\"PREPLAY_PROMPT\",\"DEAL\",\"LOCAL_TURN\",\"TURN_END\",\"SETTLEMENT\"]}"
                    : "{\"contractVersion\":\"inference.v1\","
                    + "\"messageType\":\"READY\",\"modelId\":\"resnet2\"}");
        }

        private int launchCount() {
            return launches.get();
        }
    }

    /** stdout 在 READY 后保持打开，直到 Spring 销毁 transport。 */
    private static final class ReadyModelProcess extends Process {
        private final PipedInputStream stdinReader = new PipedInputStream();
        private final PipedOutputStream javaStdin;
        private final PipedInputStream javaStdout = new PipedInputStream();
        private final PipedOutputStream stdoutWriter;
        private final PipedInputStream javaStderr = new PipedInputStream();
        private final PipedOutputStream stderrWriter;
        private final CountDownLatch exited = new CountDownLatch(1);
        private volatile boolean alive = true;

        private ReadyModelProcess() throws IOException {
            this("{\"contractVersion\":\"inference.v1\","
                    + "\"messageType\":\"READY\",\"modelId\":\"resnet2\"}");
        }

        private ReadyModelProcess(String ready) throws IOException {
            javaStdin = new PipedOutputStream(stdinReader);
            stdoutWriter = new PipedOutputStream(javaStdout);
            stderrWriter = new PipedOutputStream(javaStderr);
            stdoutWriter.write((ready + "\n").getBytes(StandardCharsets.UTF_8));
            stdoutWriter.flush();
        }

        @Override
        public OutputStream getOutputStream() {
            return javaStdin;
        }

        @Override
        public InputStream getInputStream() {
            return javaStdout;
        }

        @Override
        public InputStream getErrorStream() {
            return javaStderr;
        }

        @Override
        public int waitFor() throws InterruptedException {
            exited.await();
            return 0;
        }

        @Override
        public int exitValue() {
            if (alive) {
                throw new IllegalThreadStateException("进程仍在运行");
            }
            return 0;
        }

        @Override
        public synchronized void destroy() {
            if (!alive) {
                return;
            }
            alive = false;
            try {
                javaStdin.close();
                stdinReader.close();
                stdoutWriter.close();
                stderrWriter.close();
            } catch (IOException ignored) {
                // 测试进程关闭阶段只需释放管道。
            }
            exited.countDown();
        }

        @Override
        public boolean isAlive() {
            return alive;
        }
    }

    private static final class TrackingNewGamePort implements NewGameRecognitionPort {
        private volatile Request request;
        private volatile TrackingJob<Result> job;

        @Override
        public RecognitionJob<Result> waitForNewGame(Request value) {
            TrackingJob<Result> created = new TrackingJob<>(value.requestId());
            job = created;
            request = value;
            return created;
        }

        private Request request() {
            return request;
        }

        private TrackingJob<Result> job() {
            return job;
        }
    }

    private static <R> RecognitionJob<R> pending(String requestId) {
        return new TrackingJob<>(requestId);
    }

    private static final class TrackingJob<R> implements RecognitionJob<R> {
        private final String requestId;
        private final CompletableFuture<R> completion = new CompletableFuture<>();
        private final AtomicBoolean cancelled = new AtomicBoolean();

        private TrackingJob(String requestId) {
            this.requestId = requestId;
        }

        @Override
        public String requestId() {
            return requestId;
        }

        @Override
        public CompletionStage<R> completion() {
            return completion;
        }

        @Override
        public void cancel() {
            if (cancelled.compareAndSet(false, true)) {
                completion.cancel(false);
            }
        }

        private boolean cancelled() {
            return cancelled.get();
        }
    }
}
