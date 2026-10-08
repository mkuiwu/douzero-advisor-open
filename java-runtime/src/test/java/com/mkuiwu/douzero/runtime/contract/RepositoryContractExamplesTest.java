package com.mkuiwu.douzero.runtime.contract;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceRequest;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceResponse;
import com.mkuiwu.douzero.runtime.contract.model.douzero.DouZeroRequest;
import com.mkuiwu.douzero.runtime.contract.model.douzero.DouZeroResponse;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopEventType;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopGameFinishedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPhaseChangedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPlayAdviceEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPlayTurnStartedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPreplayAdviceEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopReadyEvent;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionCancel;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionMessageType;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionResult;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PlayRecord;
import com.mkuiwu.douzero.runtime.domain.Player;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.RecognitionProtocolAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;
import com.mkuiwu.douzero.runtime.infrastructure.model.preplay.PreplayProtocolAdapter;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ProtocolAdapter;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RepositoryContractExamplesTest {
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
    private final RecognitionProtocolAdapter recognition = new RecognitionProtocolAdapter();
    private final DouZeroCardCodec cardCodec = new DouZeroCardCodec();
    private final ResNet2ProtocolAdapter resNet2 = new ResNet2ProtocolAdapter(cardCodec);

    /** 验证五份 desktop.v1 规范样例均能由对应 Java 明确事件 DTO 无损解析。 */
    @Test
    void allFiveDesktopExamplesParseThroughExplicitEventContracts() throws Exception {
        Path directory = repositoryRoot().resolve("contracts/desktop/v1");
        List<Path> examples;
        try (Stream<Path> files = Files.list(directory)) {
            examples = files.filter(path -> path.getFileName().toString().endsWith(".example.json"))
                    .sorted().toList();
        }
        assertEquals(5, examples.size());
        for (Path example : examples) {
            JsonNode json = mapper.readTree(example.toFile());
            DesktopEventType type = DesktopEventType.valueOf(json.path("type").asText());
            Class<? extends DesktopEvent> contract = switch (type) {
                case READY -> DesktopReadyEvent.class;
                case PHASE_CHANGED -> DesktopPhaseChangedEvent.class;
                case PREPLAY_ADVICE -> DesktopPreplayAdviceEvent.class;
                case PLAY_TURN_STARTED -> DesktopPlayTurnStartedEvent.class;
                case PLAY_ADVICE -> DesktopPlayAdviceEvent.class;
                case GAME_FINISHED -> DesktopGameFinishedEvent.class;
            };
            DesktopEvent event = mapper.treeToValue(json, contract);
            assertEquals("desktop.v1", event.protocol());
            assertEquals(json, mapper.readTree(mapper.writeValueAsString(event)));
        }
    }

    /** 验证仓库中的四个正式推理样例均能由生产契约无损解析。 */
    @Test
    void allFourInferenceExamplesParseThroughProductionContracts() throws Exception {
        Path directory = repositoryRoot().resolve("contracts/inference/v1");
        List<Path> examples;
        try (Stream<Path> files = Files.list(directory)) {
            examples = files.filter(path -> path.getFileName().toString()
                            .endsWith(".example.json"))
                    .sorted().toList();
        }
        assertEquals(Set.of(
                        "douzero.request.example.json",
                        "douzero.response.example.json",
                        "resnet2.request.example.json",
                        "resnet2.response.example.json"),
                examples.stream().map(path -> path.getFileName().toString())
                        .collect(java.util.stream.Collectors.toUnmodifiableSet()));

        Map<String, Object> parsed = new java.util.HashMap<>();
        for (Path example : examples) {
            String name = example.getFileName().toString();
            Object value = switch (name) {
                case "douzero.request.example.json" ->
                        mapper.readValue(example.toFile(), DouZeroRequest.class);
                case "douzero.response.example.json" ->
                        mapper.readValue(example.toFile(), DouZeroResponse.class);
                case "resnet2.request.example.json" ->
                        mapper.readValue(example.toFile(), ResNet2Request.class);
                case "resnet2.response.example.json" ->
                        mapper.readValue(example.toFile(), ResNet2Response.class);
                default -> throw new IllegalArgumentException("未知推理合同示例: " + name);
            };
            parsed.put(name, value);
        }

        DouZeroRequest douZeroRequest = (DouZeroRequest) parsed.get(
                "douzero.request.example.json");
        DouZeroResponse douZeroResponse = (DouZeroResponse) parsed.get(
                "douzero.response.example.json");
        assertEquals(douZeroRequest.requestId(), douZeroResponse.requestId());
        assertEquals(douZeroRequest.dealId(), douZeroResponse.dealId());
        assertEquals(17, cardCodec.decode(douZeroRequest.hand()).size());
        assertEquals(3, cardCodec.decode(douZeroRequest.bottomCards()).size());
        assertInstanceOf(PlayAction.Play.class,
                cardCodec.decodeAction(douZeroResponse.action()));
        assertTrue(douZeroResponse.actionScores().stream()
                .map(score -> cardCodec.decodeAction(score.action()))
                .anyMatch(PlayAction.Pass.class::isInstance));

        ResNet2Request resNet2Request = (ResNet2Request) parsed.get(
                "resnet2.request.example.json");
        ResNet2Response resNet2Response = (ResNet2Response) parsed.get(
                "resnet2.response.example.json");
        AdviceQuery query = adviceQuery(resNet2Request);
        assertEquals(resNet2Request, resNet2.encode(query));
        AdviceResult.Recommendation recommendation = assertInstanceOf(
                AdviceResult.Recommendation.class,
                resNet2.decode(query, resNet2Response));
        assertInstanceOf(PlayAction.Play.class, recommendation.action());
        assertTrue(recommendation.actionScores().stream()
                .anyMatch(score -> score.action() instanceof PlayAction.Pass));
    }

    /** 验证十三份识别协议样例与 Java 生产适配器的字段和语义保持一致。 */
    @Test
    void allThirteenRecognitionExamplesParseAndMatchJavaAdapters() throws Exception {
        Path directory = repositoryRoot().resolve("contracts/recognition/v1");
        List<Path> examples;
        try (Stream<Path> files = Files.list(directory)) {
            examples = files.filter(path -> path.getFileName().toString().endsWith(".example.json"))
                    .sorted().toList();
        }
        assertEquals(13, examples.size());

        Map<RecognitionTaskType, RecognitionSubmit> submits =
                new EnumMap<>(RecognitionTaskType.class);
        Map<RecognitionTaskType, RecognitionResult> results =
                new EnumMap<>(RecognitionTaskType.class);
        RecognitionCancel cancel = null;
        for (Path example : examples) {
            JsonNode json = mapper.readTree(example.toFile());
            RecognitionMessageType type = RecognitionMessageType.valueOf(
                    json.get("messageType").asText());
            switch (type) {
                case SUBMIT -> {
                    RecognitionSubmit submit = mapper.treeToValue(json, RecognitionSubmit.class);
                    assertEquals(json, mapper.readTree(mapper.writeValueAsString(submit)));
                    submits.put(submit.taskType(), submit);
                }
                case RESULT -> {
                    RecognitionResult result = mapper.treeToValue(json, RecognitionResult.class);
                    assertEquals(RecognitionProtocolAdapter.CONTRACT_VERSION,
                            result.contractVersion());
                    results.put(result.taskType(), result);
                }
                case CANCEL -> {
                    cancel = mapper.treeToValue(json, RecognitionCancel.class);
                    assertEquals(json, mapper.readTree(mapper.writeValueAsString(cancel)));
                }
                case READY -> throw new IllegalArgumentException("examples 不应包含 READY");
            }
        }
        assertEquals(6, submits.size());
        assertEquals(6, results.size());
        assertNotNull(cancel);

        for (RecognitionTaskType taskType : RecognitionTaskType.values()) {
            RecognitionSubmit submit = submits.get(taskType);
            RecognitionResult result = results.get(taskType);
            assertNotNull(submit);
            assertNotNull(result);
            Object request = requestFrom(submit);
            assertEquals(submit, encode(request));
            assertNotNull(decode(request, result));
        }
        assertEquals(cancel, recognition.cancel(submits.get(RecognitionTaskType.LOCAL_TURN)));
    }

    /** 验证局前推理请求和响应样例都能通过生产适配器完成双向契约校验。 */
    @Test
    void bothPreplayInferenceExamplesParseAndMatchJavaAdapter() throws Exception {
        Path directory = repositoryRoot().resolve("contracts/preplay-inference/v1");
        Path requestPath = directory.resolve("request.example.json");
        Path responsePath = directory.resolve("response.example.json");
        PreplayInferenceRequest wireRequest = mapper.readValue(
                requestPath.toFile(), PreplayInferenceRequest.class);
        PreplayInferenceResponse wireResponse = mapper.readValue(
                responsePath.toFile(), PreplayInferenceResponse.class);
        assertEquals(mapper.readTree(requestPath.toFile()),
                mapper.readTree(mapper.writeValueAsString(wireRequest)));
        assertEquals(mapper.readTree(responsePath.toFile()),
                mapper.readTree(mapper.writeValueAsString(wireResponse)));

        PreplayProtocolAdapter adapter = new PreplayProtocolAdapter(wireRequest.modelId());
        PreplayQuery query = preplayQuery(wireRequest);
        PreplayInferenceRequest encoded = adapter.encode(query);
        assertEquals(wireRequest.contractVersion(), encoded.contractVersion());
        assertEquals(wireRequest.requestId(), encoded.requestId());
        assertEquals(wireRequest.dealId(), encoded.dealId());
        assertEquals(wireRequest.generation(), encoded.generation());
        assertEquals(wireRequest.modelId(), encoded.modelId());
        assertEquals(wireRequest.deadlineMs(), encoded.deadlineMs());
        assertEquals(wireRequest.stage(), encoded.stage());
        assertEquals(wireRequest.hand(), encoded.hand());
        assertEquals(wireRequest.bottomCards(), encoded.bottomCards());
        assertEquals(Set.copyOf(wireRequest.availableActions()),
                Set.copyOf(encoded.availableActions()));
        assertEquals(wireRequest.callPromptSeen(), encoded.callPromptSeen());
        assertEquals(wireRequest.robPromptSeen(), encoded.robPromptSeen());
        assertInstanceOf(PreplayResult.Recommendation.class,
                adapter.decode(query, wireResponse));
    }

    private Object requestFrom(RecognitionSubmit submit) {
        if (submit.taskType() == RecognitionTaskType.NEW_GAME) {
            return new NewGameRecognitionPort.Request(submit.requestId(), submit.deadlineMs());
        }
        GameTaskIdentity identity = new GameTaskIdentity(
                submit.requestId(), submit.dealId(), submit.generation());
        return switch (submit.taskType()) {
            case NEW_GAME -> throw new IllegalStateException("NEW_GAME 已在上方处理");
            case PREPLAY_PROMPT -> new PreplayRecognitionPort.Request(
                    identity,
                    "accept_current_stable_prompt".equals(submit.entryMode())
                            ? PreplayRecognitionPort.EntryMode.ACCEPT_CURRENT_STABLE_PROMPT
                            : PreplayRecognitionPort.EntryMode.REQUIRE_CHANGE_OR_EXIT_THEN_NEW,
                    submit.deadlineMs());
            case DEAL -> new DealRecognitionPort.Request(identity, submit.deadlineMs());
            case LOCAL_TURN -> new LocalTurnRecognitionPort.Request(
                    identity,
                    seat(submit.localSeat()),
                    submit.requiredActors().stream().map(this::seat)
                            .collect(java.util.stream.Collectors.toUnmodifiableSet()),
                    "accept_current_stable_turn".equals(submit.turnEntryMode())
                            ? LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN
                            : LocalTurnRecognitionPort.TurnEntryMode.REQUIRE_EXIT_THEN_REENTER,
                    submit.deadlineMs());
            case TURN_END -> {
                java.util.Map<Seat, PlayAction> actions = new java.util.LinkedHashMap<>();
                submit.baselineActionsBySeat().forEach((seat, symbols) -> {
                    CardSet actionCards = cards(symbols);
                    actions.put(seat(seat), actionCards.isEmpty()
                            ? new PlayAction.Pass()
                            : new PlayAction.Play(actionCards));
                });
                yield new TurnEndRecognitionPort.Request(identity, seat(submit.localSeat()),
                        cards(submit.baselineHand()), actions, submit.deadlineMs());
            }
            case SETTLEMENT -> new SettlementWatchPort.Request(identity, submit.deadlineMs());
        };
    }

    private RecognitionSubmit encode(Object request) {
        if (request instanceof NewGameRecognitionPort.Request value) {
            return recognition.encode(value);
        }
        if (request instanceof PreplayRecognitionPort.Request value) {
            return recognition.encode(value);
        }
        if (request instanceof DealRecognitionPort.Request value) {
            return recognition.encode(value);
        }
        if (request instanceof LocalTurnRecognitionPort.Request value) {
            return recognition.encode(value);
        }
        if (request instanceof TurnEndRecognitionPort.Request value) {
            return recognition.encode(value);
        }
        return recognition.encode((SettlementWatchPort.Request) request);
    }

    private Object decode(Object request, RecognitionResult result) {
        if (request instanceof NewGameRecognitionPort.Request value) {
            return recognition.decode(value, result);
        }
        if (request instanceof PreplayRecognitionPort.Request value) {
            return recognition.decode(value, result);
        }
        if (request instanceof DealRecognitionPort.Request value) {
            return recognition.decode(value, result);
        }
        if (request instanceof LocalTurnRecognitionPort.Request value) {
            return recognition.decode(value, result);
        }
        if (request instanceof TurnEndRecognitionPort.Request value) {
            return recognition.decode(value, result);
        }
        return recognition.decode((SettlementWatchPort.Request) request, result);
    }

    private PreplayQuery preplayQuery(PreplayInferenceRequest request) {
        PreplayStage stage = switch (request.stage()) {
            case "call" -> PreplayStage.CALL_LANDLORD;
            case "rob" -> PreplayStage.ROB_LANDLORD;
            case "double" -> PreplayStage.DOUBLE;
            default -> throw new IllegalArgumentException("未知局前阶段");
        };
        Set<PreplayAction> actions = request.availableActions().stream()
                .map(value -> PreplayAction.valueOf(value.toUpperCase(java.util.Locale.ROOT)))
                .collect(java.util.stream.Collectors.toUnmodifiableSet());
        GameTaskIdentity identity = new GameTaskIdentity(
                request.requestId(), request.dealId(), request.generation());
        PreplaySnapshot snapshot = new PreplaySnapshot(
                request.dealId(), request.generation(), stage,
                cards(request.hand()), cards(request.bottomCards()), actions,
                request.callPromptSeen(), request.robPromptSeen());
        return new PreplayQuery(identity, request.deadlineMs(), snapshot);
    }

    private AdviceQuery adviceQuery(ResNet2Request request) {
        Seat localSeat = seat(request.position());
        List<PlayRecord> history = new java.util.ArrayList<>();
        Seat actor = Seat.LANDLORD;
        for (List<Integer> action : request.actionHistory()) {
            history.add(new PlayRecord(new Player(actor), cardCodec.decodeAction(action)));
            actor = actor.next();
        }
        GameSnapshot snapshot = new GameSnapshot(
                request.dealId(),
                GamePhase.PLAYING,
                localSeat,
                cardCodec.decode(request.hand()),
                cardCodec.decode(request.bottomCards()),
                history);
        return new AdviceQuery(
                request.requestId(), request.dealId(), request.deadlineMs(), snapshot);
    }

    private CardSet cards(List<String> symbols) {
        return new CardSet(symbols.stream()
                .map(CardRank::fromSymbol).map(Card::new).toList());
    }

    private Seat seat(String value) {
        return switch (value) {
            case "landlord" -> Seat.LANDLORD;
            case "landlord_down" -> Seat.LANDLORD_DOWN;
            case "landlord_up" -> Seat.LANDLORD_UP;
            default -> throw new IllegalArgumentException("未知座位: " + value);
        };
    }

    private Path repositoryRoot() throws IOException {
        String configured = System.getProperty("maven.multiModuleProjectDirectory");
        Path candidate = configured == null ? Path.of("..").toAbsolutePath()
                : Path.of(configured).toAbsolutePath();
        if (!Files.isDirectory(candidate.resolve("contracts"))) {
            throw new IOException("无法定位仓库 contracts 目录: " + candidate);
        }
        return candidate.normalize();
    }
}
