package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceRequest;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceResponse;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;

class PreplayProtocolAdapterTest {
    private final PreplayProtocolAdapter adapter = new PreplayProtocolAdapter("bid-v1");

    /** 验证局前请求使用唯一扁平契约，并明确携带真实观察到的提示历史。 */
    @Test
    void requestHasOneFlatExactContractAndPromptHistoryFacts() throws Exception {
        PreplayInferenceRequest request = adapter.encode(query());
        JsonNode json = new ObjectMapper().readTree(new ObjectMapper().writeValueAsString(request));
        assertEquals(Set.of("contractVersion", "requestId", "dealId", "generation", "modelId",
                "deadlineMs", "stage", "hand", "bottomCards", "availableActions",
                "callPromptSeen", "robPromptSeen"),
                new ObjectMapper().convertValue(json,
                        new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
                        }).keySet());
        assertEquals("rob", request.stage());
        assertEquals(List.of("no_rob", "rob"), request.availableActions());
        assertEquals(17, request.hand().size());
        assertEquals("D", request.hand().get(0));
    }

    /** 验证局前模型原始评分不被包装成概率，决策原因仅作为诊断元数据。 */
    @Test
    void scoreStaysRawAndDecisionReasonBecomesMetadata() {
        PreplayQuery query = query();
        PreplayInferenceResponse response = new PreplayInferenceResponse(
                PreplayProtocolAdapter.CONTRACT_VERSION, "model-1", "deal-1", 4,
                "bid-v1", "OK", "rob", -0.37, -0.5, "高于抢地主阈值",
                "artifact-7", 30, null, null);
        PreplayResult.Recommendation recommendation = assertInstanceOf(
                PreplayResult.Recommendation.class, adapter.decode(query, response));
        assertEquals(-0.37, recommendation.score());
        assertEquals("高于抢地主阈值", recommendation.metadata().get("decisionReason"));
    }

    /** 验证模型建议不在画面已观察动作集合中时明确拒绝。 */
    @Test
    void rejectsActionOutsideObservedAvailableActions() {
        PreplayQuery query = query();
        PreplayInferenceResponse response = new PreplayInferenceResponse(
                PreplayProtocolAdapter.CONTRACT_VERSION, "model-1", "deal-1", 4,
                "bid-v1", "OK", "call", 0.8, 0.5, "错误跨阶段动作",
                "artifact-7", 30, null, null);
        PreplayResult.Unavailable unavailable = assertInstanceOf(
                PreplayResult.Unavailable.class, adapter.decode(query, response));
        assertEquals(AdviceFailure.ILLEGAL_ACTION, unavailable.failure());
    }

    private PreplayQuery query() {
        GameTaskIdentity identity = new GameTaskIdentity("model-1", "deal-1", 4);
        CardSet hand = new CardSet(List.of(CardRank.BIG_JOKER, CardRank.SMALL_JOKER,
                        CardRank.TWO, CardRank.TWO, CardRank.ACE, CardRank.ACE,
                        CardRank.KING, CardRank.KING, CardRank.QUEEN, CardRank.QUEEN,
                        CardRank.JACK, CardRank.JACK, CardRank.TEN, CardRank.NINE,
                        CardRank.EIGHT, CardRank.SEVEN, CardRank.THREE)
                .stream().map(Card::new).toList());
        PreplaySnapshot snapshot = new PreplaySnapshot("deal-1", 4,
                PreplayStage.ROB_LANDLORD, hand, CardSet.empty(),
                Set.of(PreplayAction.ROB, PreplayAction.NO_ROB), true, true);
        return new PreplayQuery(identity, 500, snapshot);
    }
}
