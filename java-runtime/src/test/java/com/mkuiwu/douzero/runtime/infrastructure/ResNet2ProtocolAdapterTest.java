package com.mkuiwu.douzero.runtime.infrastructure;

import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.contract.common.ContractStatus;
import com.mkuiwu.douzero.runtime.contract.common.ErrorCode;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2ActionScore;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PlayRecord;
import com.mkuiwu.douzero.runtime.domain.Player;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransportException;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ModelAdvisor;
import com.mkuiwu.douzero.runtime.infrastructure.model.resnet2.ResNet2ProtocolAdapter;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;

class ResNet2ProtocolAdapterTest {
    private final ResNet2ProtocolAdapter adapter = new ResNet2ProtocolAdapter(
            new DouZeroCardCodec());

    /** 验证领域牌面只在 ResNet2 线协议边界转换为模型编码。 */
    @Test
    void encodesOnlyAtTheResNet2WireBoundary() {
        ResNet2Request request = adapter.encode(query());

        assertEquals("resnet2", request.modelId());
        assertEquals("landlord_down", request.position());
        assertEquals(17, request.hand().get(0));
        assertEquals(List.of(17, 17), request.actionHistory().get(0));
    }

    /** 验证模型返回空动作时解码为显式 Pass，而不是空值或失败。 */
    @Test
    void decodesEmptyModelActionAsExplicitPass() {
        AdviceResult result = adapter.decode(query(), responseWithAction());

        AdviceResult.Recommendation recommendation = assertInstanceOf(
                AdviceResult.Recommendation.class, result);
        assertInstanceOf(PlayAction.Pass.class, recommendation.action());
    }

    /** 验证全部候选动作原始评分都会保留到应用结果，支持解释建议。 */
    @Test
    void keepsAllCandidateScoresInTheApplicationResult() {
        ResNet2Response response = new ResNet2Response(
                "inference.v1",
                "request-1",
                "deal-1",
                "resnet2",
                ContractStatus.OK,
                List.of(20, 30),
                0.8,
                0.5,
                List.of(
                        new ResNet2ActionScore(List.of(20, 30), 0.8),
                        new ResNet2ActionScore(List.of(), 0.3)
                ),
                "resnet2-v1",
                18,
                ErrorCode.NONE,
                ""
        );

        AdviceResult.Recommendation recommendation = assertInstanceOf(
                AdviceResult.Recommendation.class, adapter.decode(query(), response));

        assertEquals(0.8, recommendation.actionValue());
        assertEquals(0.5, recommendation.actionMargin());
        assertEquals(2, recommendation.actionScores().size());
        assertInstanceOf(PlayAction.Pass.class,
                recommendation.actionScores().get(1).action());
    }

    /** 验证 Python 从合法候选中选出的动作能够转换为可信应用建议。 */
    @Test
    void acceptsTheActionSelectedFromPythonGeneratedCandidates() {
        AdviceResult result = adapter.decode(query(), responseWithAction(3));

        AdviceResult.Recommendation recommendation = assertInstanceOf(
                AdviceResult.Recommendation.class, result);
        assertInstanceOf(PlayAction.Play.class, recommendation.action());
    }

    /** 验证响应包含未知模型牌码时明确拒绝，禁止猜测领域牌面。 */
    @Test
    void rejectsUnknownModelCardCode() {
        AdviceResult result = adapter.decode(query(), responseWithAction(99));

        AdviceResult.Unavailable unavailable = assertInstanceOf(
                AdviceResult.Unavailable.class, result);
        assertEquals(AdviceFailure.INVALID_RESPONSE, unavailable.failure());
    }

    /** 验证即使模型声称成功，超过请求截止时间的响应仍会被判为无效。 */
    @Test
    void rejectsSuccessfulResponseThatExceededTheRequestDeadline() {
        AdviceResult result = adapter.decode(query(), responseWithActionAtLatency(1600));

        AdviceResult.Unavailable unavailable = assertInstanceOf(
                AdviceResult.Unavailable.class, result);
        assertEquals(AdviceFailure.MODEL_TIMEOUT, unavailable.failure());
    }

    /** 验证模型适配器对应用层只暴露 DecisionPort，不泄漏线协议 DTO。 */
    @Test
    void modelAdvisorKeepsWireTypesBehindTheApplicationPort() {
        ResNet2ModelAdvisor advisor = new ResNet2ModelAdvisor(
                adapter,
                request -> responseWithAction()
        );

        AdviceResult result = advisor.decide(query());

        assertInstanceOf(AdviceResult.Recommendation.class, result);
    }

    /** 验证传输层返回 null 属于无效响应，而不是伪装成模型暂不可用。 */
    @Test
    void nullTransportResponseIsAnInvalidResponseRatherThanModelUnavailability() {
        ResNet2ModelAdvisor advisor = new ResNet2ModelAdvisor(adapter, request -> null);

        AdviceResult.Unavailable result = assertInstanceOf(
                AdviceResult.Unavailable.class, advisor.decide(query()));

        assertEquals(AdviceFailure.INVALID_RESPONSE, result.failure());
    }

    /** 验证传输超时会保留 MODEL_TIMEOUT 语义，供编排器进入等待流程。 */
    @Test
    void transportTimeoutKeepsItsSemanticFailureReason() {
        ResNet2ModelAdvisor advisor = new ResNet2ModelAdvisor(adapter, request -> {
            throw new ModelTransportException(
                    ModelTransportException.Reason.TIMEOUT,
                    "底层连接细节",
                    null
            );
        });

        AdviceResult.Unavailable result = assertInstanceOf(
                AdviceResult.Unavailable.class, advisor.decide(query()));

        assertEquals(AdviceFailure.MODEL_TIMEOUT, result.failure());
        assertEquals("模型传输超时", result.detail());
    }

    private AdviceQuery query() {
        CardSet hand = cards(CardRank.TWO, CardRank.SMALL_JOKER, CardRank.BIG_JOKER);
        PlayRecord landlordMove = new PlayRecord(
                new Player(Seat.LANDLORD),
                new PlayAction.Play(cards(CardRank.TWO, CardRank.TWO))
        );
        GameSnapshot snapshot = new GameSnapshot(
                "deal-1",
                GamePhase.PLAYING,
                Seat.LANDLORD_DOWN,
                hand,
                cards(CardRank.FIVE, CardRank.SIX, CardRank.SEVEN),
                List.of(landlordMove)
        );
        return new AdviceQuery(
                "request-1",
                "deal-1",
                1500,
                snapshot
        );
    }

    private ResNet2Response responseWithAction(int... codes) {
        return responseWithActionAtLatency(18, codes);
    }

    private ResNet2Response responseWithActionAtLatency(long latencyMs, int... codes) {
        return new ResNet2Response(
                "inference.v1",
                "request-1",
                "deal-1",
                "resnet2",
                ContractStatus.OK,
                java.util.Arrays.stream(codes).boxed().toList(),
                0.8,
                0.0,
                List.of(new ResNet2ActionScore(
                        java.util.Arrays.stream(codes).boxed().toList(), 0.8)),
                "resnet2-v1",
                latencyMs,
                ErrorCode.NONE,
                ""
        );
    }

    private CardSet cards(CardRank... ranks) {
        return new CardSet(java.util.Arrays.stream(ranks).map(Card::new).toList());
    }
}
