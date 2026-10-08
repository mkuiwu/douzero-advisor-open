package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceRequest;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceResponse;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/** 局前语义决策请求与 preplay-inference.v1 扁平协议之间的唯一映射点。 */
public final class PreplayProtocolAdapter {
    /** 当前支持的局前模型协议版本。 */
    public static final String CONTRACT_VERSION = "preplay-inference.v1";

    /** 配置锁定的模型标识。 */
    private final String modelId;

    public PreplayProtocolAdapter(String modelId) {
        if (modelId == null || modelId.isBlank()) {
            throw new IllegalArgumentException("局前模型标识不能为空");
        }
        this.modelId = modelId;
    }

    /** 把已确认局前快照编码为模型唯一 Request DTO。 */
    public PreplayInferenceRequest encode(PreplayQuery query) {
        Objects.requireNonNull(query, "局前决策请求不能为空");
        return new PreplayInferenceRequest(CONTRACT_VERSION,
                query.identity().requestId(), query.identity().dealId(),
                query.identity().generation(), modelId, query.deadlineMs(),
                encodeStage(query.snapshot().stage()), encodeCards(query.snapshot().hand()),
                encodeCards(query.snapshot().bottomCards()),
                query.snapshot().availableActions().stream()
                        .map(this::encodeAction).sorted().toList(),
                query.snapshot().callPromptSeen(), query.snapshot().robPromptSeen());
    }

    /** 校验响应身份、阶段和可用动作后恢复为只读业务建议。 */
    public PreplayResult decode(PreplayQuery query, PreplayInferenceResponse response) {
        Objects.requireNonNull(query, "局前决策请求不能为空");
        if (response == null) {
            return unavailable(query.identity(), AdviceFailure.INVALID_RESPONSE, "局前模型响应不能为空");
        }
        PreplayResult identityFailure = validateIdentity(query, response);
        if (identityFailure != null) {
            return identityFailure;
        }
        if (!"OK".equals(response.status())) {
            return unavailable(query.identity(), mapFailure(response.errorCode()),
                    response.errorMessage());
        }
        if (response.latencyMs() > query.deadlineMs()) {
            return unavailable(query.identity(), AdviceFailure.MODEL_TIMEOUT,
                    "局前模型响应超过请求截止时间");
        }
        try {
            PreplayAction action = decodeAction(response.action());
            if (!query.snapshot().availableActions().contains(action)
                    || action.stage() != query.snapshot().stage()) {
                return unavailable(query.identity(), AdviceFailure.ILLEGAL_ACTION,
                        "局前模型动作不属于当前可用动作");
            }
            Map<String, String> metadata = new LinkedHashMap<>();
            metadata.put("modelVersion", response.modelVersion());
            metadata.put("latencyMs", Long.toString(response.latencyMs()));
            metadata.put("decisionReason", response.decisionReason());
            return new PreplayResult.Recommendation(query.identity(), modelId, action,
                    response.score(), response.threshold(), metadata);
        } catch (IllegalArgumentException error) {
            return unavailable(query.identity(), AdviceFailure.INVALID_RESPONSE,
                    error.getMessage());
        }
    }

    private PreplayResult validateIdentity(PreplayQuery query,
                                           PreplayInferenceResponse response) {
        GameTaskIdentity identity = query.identity();
        if (!CONTRACT_VERSION.equals(response.contractVersion())) {
            return unavailable(identity, AdviceFailure.UNSUPPORTED_CONTRACT,
                    "局前模型响应协议版本不匹配");
        }
        if (!modelId.equals(response.modelId())) {
            return unavailable(identity, AdviceFailure.UNSUPPORTED_MODEL,
                    "局前模型响应标识不匹配");
        }
        if (!identity.requestId().equals(response.requestId())
                || !identity.dealId().equals(response.dealId())
                || identity.generation() != response.generation()) {
            return unavailable(identity, AdviceFailure.INVALID_RESPONSE,
                    "局前模型响应任务身份不匹配");
        }
        return null;
    }

    private AdviceFailure mapFailure(String code) {
        if (code == null) {
            return AdviceFailure.INVALID_RESPONSE;
        }
        try {
            return AdviceFailure.valueOf(code.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException error) {
            return AdviceFailure.INVALID_RESPONSE;
        }
    }

    private PreplayResult.Unavailable unavailable(GameTaskIdentity identity,
                                                  AdviceFailure failure, String detail) {
        String safeDetail = detail == null || detail.isBlank() ? failure.name() : detail;
        return new PreplayResult.Unavailable(identity, modelId, failure, safeDetail);
    }

    private List<String> encodeCards(CardSet cards) {
        return cards.cards().stream().map(card -> card.rank().symbol()).toList();
    }

    private String encodeStage(PreplayStage stage) {
        return switch (stage) {
            case CALL_LANDLORD -> "call";
            case ROB_LANDLORD -> "rob";
            case DOUBLE -> "double";
        };
    }

    private String encodeAction(PreplayAction action) {
        return action.name().toLowerCase(Locale.ROOT);
    }

    private PreplayAction decodeAction(String action) {
        try {
            return PreplayAction.valueOf(Objects.requireNonNull(action,
                    "局前模型动作不能为空").toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("未知局前模型动作: " + action, error);
        }
    }
}
