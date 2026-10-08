package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionMessageType;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionResult;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionStatus;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;

class RecognitionProtocolAdapterTest {
    private final RecognitionProtocolAdapter adapter = new RecognitionProtocolAdapter();
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());

    /** 验证本方回合请求采用固定扁平字段和稳定 lower_snake_case 枚举值。 */
    @Test
    void localTurnUsesExactFlatFieldsAndStableLowerSnakeValues() throws Exception {
        GameTaskIdentity identity = new GameTaskIdentity("turn-7", "deal-2", 3);
        RecognitionSubmit submit = adapter.encode(new LocalTurnRecognitionPort.Request(
                identity, Seat.LANDLORD_UP, Set.of(Seat.LANDLORD, Seat.LANDLORD_DOWN),
                LocalTurnRecognitionPort.TurnEntryMode.REQUIRE_EXIT_THEN_REENTER, 1500));

        JsonNode json = mapper.readTree(mapper.writeValueAsString(submit));
        assertEquals(Set.of("contractVersion", "messageType", "taskType", "requestId",
                        "dealId", "generation", "deadlineMs", "localSeat", "requiredActors",
                        "turnEntryMode"),
                mapper.convertValue(json, new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
                }).keySet());
        assertEquals("SUBMIT", json.get("messageType").asText());
        assertEquals("LOCAL_TURN", json.get("taskType").asText());
        assertEquals("landlord_up", json.get("localSeat").asText());
        assertEquals("require_exit_then_reenter", json.get("turnEntryMode").asText());
    }

    /** 验证回合响应把牌点符号还原为领域牌，并把空动作还原为显式 Pass。 */
    @Test
    void localTurnDecodesRankSymbolsAndEmptyActionAsExplicitPass() {
        GameTaskIdentity identity = new GameTaskIdentity("turn-8", "deal-2", 3);
        LocalTurnRecognitionPort.Request request = new LocalTurnRecognitionPort.Request(
                identity, Seat.LANDLORD_UP, Set.of(Seat.LANDLORD, Seat.LANDLORD_DOWN),
                LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN, 1500);
        RecognitionResult wire = result(RecognitionTaskType.LOCAL_TURN, identity,
                List.of("D", "X", "2", "10", "3"),
                Map.of("landlord", List.of(), "landlord_down", List.of("A", "A")));

        LocalTurnRecognitionPort.Ready ready = assertInstanceOf(
                LocalTurnRecognitionPort.Ready.class, adapter.decode(request, wire));
        assertEquals(5, ready.currentHand().size());
        assertInstanceOf(PlayAction.Pass.class, ready.actionsBySeat().get(Seat.LANDLORD));
        PlayAction.Play play = assertInstanceOf(PlayAction.Play.class,
                ready.actionsBySeat().get(Seat.LANDLORD_DOWN));
        assertEquals(List.of("A", "A"), play.cards().cards().stream()
                .map(card -> card.rank().symbol()).toList());
    }

    /** 验证局前协议携带提示边沿模式，并拒绝与当前阶段不一致的可用动作。 */
    @Test
    void preplayUsesPromptEdgeAndRequiresStageConsistentActions() {
        GameTaskIdentity identity = new GameTaskIdentity("prompt-1", "deal-9", 2);
        PreplayRecognitionPort.Request request = new PreplayRecognitionPort.Request(identity,
                PreplayRecognitionPort.EntryMode.REQUIRE_CHANGE_OR_EXIT_THEN_NEW, 1200);
        RecognitionSubmit submit = adapter.encode(request);
        assertEquals("require_change_then_new", submit.entryMode());

        RecognitionResult wire = new RecognitionResult(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.RESULT, RecognitionTaskType.PREPLAY_PROMPT,
                identity.requestId(), identity.dealId(), identity.generation(),
                RecognitionStatus.OK, Instant.parse("2026-08-26T08:00:00Z"), "double",
                seventeenCards(), null, List.of(), null, null, null,
                List.of("double", "super_double", "no_double"), null, null);
        PreplayRecognitionPort.Ready ready = assertInstanceOf(PreplayRecognitionPort.Ready.class,
                adapter.decode(request, wire));
        assertEquals(Set.of(PreplayAction.DOUBLE, PreplayAction.SUPER_DOUBLE,
                PreplayAction.NO_DOUBLE), ready.availableActions());
    }

    /** 验证结果 generation 不匹配时在生成业务结果前即被拒绝。 */
    @Test
    void rejectsMismatchedGenerationBeforeBusinessResultExists() {
        GameTaskIdentity identity = new GameTaskIdentity("turn-9", "deal-2", 3);
        LocalTurnRecognitionPort.Request request = new LocalTurnRecognitionPort.Request(
                identity, Seat.LANDLORD, Set.of(),
                LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN, 1500);
        RecognitionResult stale = result(RecognitionTaskType.LOCAL_TURN,
                new GameTaskIdentity("turn-9", "deal-2", 2), List.of("3"), Map.of());
        assertThrows(IllegalArgumentException.class, () -> adapter.decode(request, stale));
    }

    private RecognitionResult result(RecognitionTaskType type, GameTaskIdentity identity,
                                     List<String> hand, Map<String, List<String>> actions) {
        return new RecognitionResult(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.RESULT, type, identity.requestId(), identity.dealId(),
                identity.generation(), RecognitionStatus.OK,
                Instant.parse("2026-08-26T08:00:00Z"), null, null, hand, null, null,
                null, actions, null, null, null);
    }

    private List<String> seventeenCards() {
        return List.of("D", "X", "2", "2", "A", "A", "K", "K", "Q", "Q",
                "J", "J", "10", "9", "8", "7", "3");
    }
}
