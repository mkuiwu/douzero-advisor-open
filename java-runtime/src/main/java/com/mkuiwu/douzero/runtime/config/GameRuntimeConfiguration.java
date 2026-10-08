package com.mkuiwu.douzero.runtime.config;

import com.mkuiwu.douzero.runtime.application.GameOrchestrator;
import com.mkuiwu.douzero.runtime.application.GameSessionFactory;
import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.port.CardPlayExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.DecisionPort;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayButtonExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.RuntimeIdentitySource;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.infrastructure.DefaultRuntimeIdentitySource;
import com.mkuiwu.douzero.runtime.infrastructure.LoggingGameRuntimeObserver;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.execution.JsonLineExecutionClient;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.JsonLineResNet2ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ModelAdvisor;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ProtocolAdapter;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

/** recognition 与 orchestration 均显式启用且全部业务能力齐备时，组装只读 Java 状态机。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(RuntimeProperties.class)
@ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
@ConditionalOnProperty(prefix = "douzero.orchestration", name = "enabled",
        havingValue = "true", matchIfMissing = true)
public class GameRuntimeConfiguration {
    /** 为每局创建全新的会话，避免 Spring 单例持有局内状态。 */
    @Bean
    @ConditionalOnMissingBean
    GameSessionFactory gameSessionFactory() {
        return com.mkuiwu.douzero.runtime.application.GameSession::new;
    }

    /** 默认身份来源只在启用真实识别状态机时存在。 */
    @Bean
    @ConditionalOnMissingBean
    RuntimeIdentitySource runtimeIdentitySource() {
        return new DefaultRuntimeIdentitySource();
    }

    /** 默认观察器只输出只读阶段和建议摘要。 */
    @Bean
    @ConditionalOnMissingBean
    GameRuntimeObserver gameRuntimeObserver() {
        return new LoggingGameRuntimeObserver();
    }

    /** 所有状态回调共用的唯一事件线程。 */
    @Bean(destroyMethod = "shutdownNow")
    ExecutorService gameEventExecutor() {
        return Executors.newSingleThreadExecutor(namedThreads("douzero-game-event"));
    }

    /** 同步 Python 模型调用使用的独立单线程，不能阻塞状态事件。 */
    @Bean(destroyMethod = "shutdownNow")
    ExecutorService gameDecisionExecutor() {
        return Executors.newSingleThreadExecutor(namedThreads("douzero-game-decision"));
    }

    /** Java 硬截止时间只在该调度线程触发，再汇入事件线程处理。 */
    @Bean(destroyMethod = "shutdownNow")
    ScheduledExecutorService gameTimeoutScheduler() {
        return Executors.newSingleThreadScheduledExecutor(namedThreads("douzero-game-timeout"));
    }

    /** 识别主链和正式模型都显式启用时，启动唯一的持久 ResNet2 JSONL worker。 */
    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero.model", name = "enabled", havingValue = "true")
    @ConditionalOnMissingBean(ModelTransport.class)
    JsonLineResNet2ModelTransport resNet2ModelTransport(
            ObjectMapper objectMapper,
            ProcessLauncher launcher,
            RuntimeProperties properties
    ) {
        RuntimeProperties.Model model = Objects.requireNonNull(
                properties.model(), "启用识别状态机时必须配置 douzero.model");
        if (!ResNet2ProtocolAdapter.MODEL_ID.equals(model.name())) {
            throw new IllegalStateException("当前 Java 正式出牌适配器只支持 resnet2");
        }
        JsonLineResNet2ModelTransport transport = new JsonLineResNet2ModelTransport(
                objectMapper, launcher, model.command(), model.timeoutMs());
        transport.start();
        return transport;
    }

    /** 正式出牌建议关闭时返回明确不可用结果，状态机仍可继续识别并安全收口。 */
    @Bean
    @ConditionalOnProperty(prefix = "douzero.model", name = "enabled", havingValue = "false")
    @ConditionalOnMissingBean(DecisionPort.class)
    DecisionPort disabledDecisionPort() {
        return query -> new AdviceResult.Unavailable(
                "disabled", AdviceFailure.NO_RECOMMENDATION, "正式出牌建议已由启动配置关闭");
    }

    /** 由生产 JSONL 传输组装 ResNet2 DecisionPort；能力缺失时明确失败而不产生假建议。 */
    @Bean
    @ConditionalOnMissingBean(DecisionPort.class)
    DecisionPort resNet2DecisionPort(
            RuntimeProperties properties,
            ObjectProvider<ModelTransport<ResNet2Request, ResNet2Response>> transports
    ) {
        RuntimeProperties.Model model = Objects.requireNonNull(
                properties.model(), "启用识别状态机时必须配置 douzero.model");
        if (!model.enabled()) {
            throw new IllegalStateException(
                    "启用识别状态机时必须显式开启 douzero.model.enabled；禁止 no-op 建议");
        }
        if (!ResNet2ProtocolAdapter.MODEL_ID.equals(model.name())) {
            throw new IllegalStateException("当前 Java 正式出牌适配器只支持 resnet2");
        }
        ModelTransport<ResNet2Request, ResNet2Response> transport = transports.getIfUnique();
        if (transport == null) {
            throw new IllegalStateException(
                    "启用识别状态机时必须创建唯一的 ResNet2 ModelTransport；禁止 no-op 建议");
        }
        return new ResNet2ModelAdvisor(
                new ResNet2ProtocolAdapter(new DouZeroCardCodec()), transport);
    }

    /** 局前建议关闭时返回明确不可用结果，牌局识别和正式阶段初始化不受阻断。 */
    @Bean
    @ConditionalOnProperty(prefix = "douzero.preplay-model", name = "enabled", havingValue = "false")
    @ConditionalOnMissingBean(PreplayDecisionPort.class)
    PreplayDecisionPort disabledPreplayDecisionPort() {
        return query -> new PreplayResult.Unavailable(
                query.identity(), "disabled", AdviceFailure.NO_RECOMMENDATION, "局前建议已由启动配置关闭");
    }

    /**
     * 显式启用正式出牌或局前按钮执行时创建持久 Python execution worker；默认只读模式不创建此 bean。
     * Java 编排器通过 ObjectProvider 可选注入，未启用时不影响只读建议主链。
     */
    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero.execution", name = "enabled", havingValue = "true")
    @ConditionalOnMissingBean({CardPlayExecutionPort.class, PreplayButtonExecutionPort.class})
    JsonLineExecutionClient cardPlayExecutionClient(
            ObjectMapper objectMapper,
            ProcessLauncher launcher,
            RuntimeProperties properties
    ) {
        RuntimeProperties.Execution execution = Objects.requireNonNull(
                properties.execution(), "启用执行链时必须配置 douzero.execution");
        JsonLineExecutionClient client = new JsonLineExecutionClient(
                objectMapper, launcher, execution.command(), execution.autoPassDelayMs());
        return client;
    }

    /** 全部业务 Port 齐备后创建唯一编排器；任一缺失都以明确能力名称 fail-fast。 */
    @Bean
    GameOrchestrator gameOrchestrator(
            ObjectProvider<NewGameRecognitionPort> newGamePorts,
            ObjectProvider<PreplayRecognitionPort> preplayRecognitionPorts,
            ObjectProvider<DealRecognitionPort> dealPorts,
            ObjectProvider<LocalTurnRecognitionPort> localTurnPorts,
            ObjectProvider<TurnEndRecognitionPort> turnEndPorts,
            ObjectProvider<SettlementWatchPort> settlementPorts,
            ObjectProvider<PreplayDecisionPort> preplayDecisionPorts,
            ObjectProvider<DecisionPort> decisionPorts,
            ObjectProvider<CardPlayExecutionPort> executionPorts,
            ObjectProvider<PreplayButtonExecutionPort> preplayExecutionPorts,
            GameRuntimeObserver observer,
            RuntimeIdentitySource identitySource,
            GameSessionFactory sessionFactory,
            @Qualifier("gameEventExecutor") ExecutorService eventExecutor,
            @Qualifier("gameDecisionExecutor") ExecutorService decisionExecutor,
            @Qualifier("gameTimeoutScheduler") ScheduledExecutorService timeoutScheduler,
            RuntimeProperties properties
    ) {
        RuntimeProperties.Orchestration value = Objects.requireNonNull(
                properties.orchestration(),
                "启用识别状态机时必须配置 douzero.orchestration");
        RuntimeProperties.Execution execution = properties.execution();
        boolean cardPlayEnabled = execution != null && execution.cardPlayEnabled();
        boolean autoPassEnabled = execution != null && execution.autoPassEnabled();
        CardPlayExecutionPort executionPort = cardPlayEnabled || autoPassEnabled
                ? executionPorts.getIfUnique() : null;
        PreplayButtonExecutionPort preplayExecutionPort = execution != null
                && execution.preplayButtonsEnabled() ? preplayExecutionPorts.getIfUnique() : null;
        long executeMs = 0;
        boolean autoSubmit = false;
        long preplayExecuteMs = 0;
        if (executionPort != null) {
            execution = Objects.requireNonNull(execution,
                    "启用正式出牌执行时必须配置 douzero.execution");
            executeMs = execution.executeMs();
            autoSubmit = execution.autoSubmit();
        }
        if (preplayExecutionPort != null) {
            execution = Objects.requireNonNull(execution,
                    "启用局前按钮执行时必须配置 douzero.execution");
            preplayExecuteMs = execution.executeMs();
        }
        return new GameOrchestrator(
                required(newGamePorts, "NewGameRecognitionPort"),
                required(preplayRecognitionPorts, "PreplayRecognitionPort"),
                required(dealPorts, "DealRecognitionPort"),
                required(localTurnPorts, "LocalTurnRecognitionPort"),
                required(turnEndPorts, "TurnEndRecognitionPort"),
                required(settlementPorts, "SettlementWatchPort"),
                required(preplayDecisionPorts, "PreplayDecisionPort"),
                required(decisionPorts, "DecisionPort"),
                observer,
                identitySource,
                sessionFactory,
                eventExecutor,
                decisionExecutor,
                timeoutScheduler,
                new GameOrchestrator.Deadlines(
                        value.newGameMs(), value.preplayRecognitionMs(), value.dealMs(),
                        value.settlementMs(), value.localTurnMs(), value.turnEndMs(),
                        value.preplayDecisionMs(), value.playDecisionMs()),
                executionPort,
                executeMs,
                autoSubmit,
                cardPlayEnabled,
                autoPassEnabled,
                preplayExecutionPort,
                preplayExecuteMs
        );
    }

    /** ApplicationReady 后启动，Spring 销毁时先停止编排器再关闭执行器。 */
    @Bean(destroyMethod = "close")
    GameOrchestratorLifecycle gameOrchestratorLifecycle(GameOrchestrator orchestrator) {
        return new GameOrchestratorLifecycle(orchestrator);
    }

    private static <T> T required(ObjectProvider<T> provider, String capability) {
        T value = provider.getIfUnique();
        if (value == null) {
            throw new IllegalStateException(
                    "启用识别状态机时必须且只能提供一个 " + capability);
        }
        return value;
    }

    private static ThreadFactory namedThreads(String prefix) {
        AtomicInteger sequence = new AtomicInteger();
        return task -> {
            Thread thread = new Thread(task, prefix + "-" + sequence.incrementAndGet());
            thread.setDaemon(false);
            return thread;
        };
    }
}
