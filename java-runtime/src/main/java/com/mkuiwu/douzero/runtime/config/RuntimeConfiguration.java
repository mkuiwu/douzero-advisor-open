package com.mkuiwu.douzero.runtime.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.DealJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.DefaultProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.JsonLineRecognitionClient;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.LocalTurnJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.TurnEndJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.NewGameJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.PreplayJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.RecognitionProtocolAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.SettlementJsonLineAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.preplay.JsonLinePreplayModelClient;
import com.mkuiwu.douzero.runtime.infrastructure.model.preplay.PreplayModelAdvisor;
import com.mkuiwu.douzero.runtime.infrastructure.model.preplay.PreplayProtocolAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.JsonLineResNet2ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ProtocolAdapter;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

import java.util.ArrayList;
import java.util.EnumSet;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

@Configuration
@EnableConfigurationProperties(RuntimeProperties.class)
public class RuntimeConfiguration {
    /** 接线 Profile 持续运行，直到 IDEA Stop 或 Ctrl+C 触发 Spring 统一关闭。 */
    @Bean(destroyMethod = "close")
    @Profile("python-services")
    RuntimeKeepAlive runtimeKeepAlive() {
        return new RuntimeKeepAlive();
    }

    @Bean
    @ConditionalOnMissingBean(ProcessLauncher.class)
    ProcessLauncher processLauncher() {
        return new DefaultProcessLauncher();
    }

    /** 正式模型传输可脱离 Java 编排器独立启动，这里只负责进程和 READY 握手。 */
    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero.model", name = "enabled", havingValue = "true")
    @ConditionalOnProperty(prefix = "douzero.orchestration", name = "enabled",
            havingValue = "false")
    @ConditionalOnMissingBean(ModelTransport.class)
    JsonLineResNet2ModelTransport resNet2ModelTransport(
            ObjectMapper objectMapper,
            ProcessLauncher launcher,
            RuntimeProperties properties
    ) {
        RuntimeProperties.Model model = Objects.requireNonNull(
                properties.model(), "启用正式模型服务时必须配置 douzero.model");
        if (!ResNet2ProtocolAdapter.MODEL_ID.equals(model.name())) {
            throw new IllegalStateException("当前 Java 正式出牌适配器只支持 resnet2");
        }
        JsonLineResNet2ModelTransport transport = new JsonLineResNet2ModelTransport(
                objectMapper, launcher, model.command(), model.timeoutMs());
        transport.start();
        return transport;
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    RecognitionProtocolAdapter recognitionProtocolAdapter() {
        return new RecognitionProtocolAdapter();
    }

    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    JsonLineRecognitionClient recognitionClient(ObjectMapper objectMapper,
                                                ProcessLauncher launcher,
                                                RuntimeProperties properties) {
        RuntimeProperties.Recognition configuration = properties.recognition();
        java.util.List<String> command = new ArrayList<>(configuration.command());
        command.add("--calibration-manifest");
        command.add(configuration.calibrationManifest());
        JsonLineRecognitionClient client = new JsonLineRecognitionClient(
                objectMapper, launcher, command);
        try {
            var ready = client.start().toCompletableFuture().get(
                    configuration.startupTimeoutMs(), TimeUnit.MILLISECONDS);
            if (!RecognitionProtocolAdapter.CONTRACT_VERSION.equals(ready.contractVersion())
                    || !EnumSet.copyOf(ready.taskTypes()).containsAll(
                    EnumSet.allOf(RecognitionTaskType.class))) {
                throw new IllegalStateException("Python CV READY 协议或能力清单不完整");
            }
            return client;
        } catch (Exception error) {
            client.close();
            throw new IllegalStateException("Python CV worker 未能可靠就绪", error);
        }
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    NewGameRecognitionPort newGameRecognitionPort(JsonLineRecognitionClient client,
                                                  RecognitionProtocolAdapter protocol) {
        return new NewGameJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    PreplayRecognitionPort preplayRecognitionPort(JsonLineRecognitionClient client,
                                                  RecognitionProtocolAdapter protocol) {
        return new PreplayJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    DealRecognitionPort dealRecognitionPort(JsonLineRecognitionClient client,
                                            RecognitionProtocolAdapter protocol) {
        return new DealJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    LocalTurnRecognitionPort localTurnRecognitionPort(JsonLineRecognitionClient client,
                                                      RecognitionProtocolAdapter protocol) {
        return new LocalTurnJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    TurnEndRecognitionPort turnEndRecognitionPort(JsonLineRecognitionClient client,
                                                  RecognitionProtocolAdapter protocol) {
        return new TurnEndJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero.recognition", name = "enabled", havingValue = "true")
    SettlementWatchPort settlementWatchPort(JsonLineRecognitionClient client,
                                            RecognitionProtocolAdapter protocol) {
        return new SettlementJsonLineAdapter(client, protocol);
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero", name = {
            "recognition.enabled", "preplay-model.enabled"
    }, havingValue = "true")
    PreplayProtocolAdapter preplayProtocolAdapter(RuntimeProperties properties) {
        return new PreplayProtocolAdapter(properties.preplayModel().modelId());
    }

    @Bean(destroyMethod = "close")
    @ConditionalOnProperty(prefix = "douzero", name = {
            "recognition.enabled", "preplay-model.enabled"
    }, havingValue = "true")
    JsonLinePreplayModelClient preplayModelClient(ObjectMapper objectMapper,
                                                  ProcessLauncher launcher,
                                                  RuntimeProperties properties) {
        JsonLinePreplayModelClient client = new JsonLinePreplayModelClient(
                objectMapper, launcher, preplayCommand(properties.preplayModel()));
        client.start(properties.preplayModel().timeoutMs());
        return client;
    }

    @Bean
    @ConditionalOnProperty(prefix = "douzero", name = {
            "recognition.enabled", "preplay-model.enabled"
    }, havingValue = "true")
    PreplayDecisionPort preplayDecisionPort(RuntimeProperties properties,
                                            PreplayProtocolAdapter protocol,
                                            JsonLinePreplayModelClient client) {
        return new PreplayModelAdvisor(properties.preplayModel().modelId(), protocol, client);
    }

    private java.util.List<String> preplayCommand(RuntimeProperties.PreplayModel configuration) {
        java.util.List<String> command = new ArrayList<>(configuration.command());
        command.add("--legacy-root");
        command.add(configuration.legacyRoot());
        return command;
    }
}
