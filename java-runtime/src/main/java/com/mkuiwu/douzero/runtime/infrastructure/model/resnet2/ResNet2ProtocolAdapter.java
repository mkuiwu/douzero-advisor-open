package com.mkuiwu.douzero.runtime.infrastructure.model.resnet2;

import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.ActionScore;
import com.mkuiwu.douzero.runtime.contract.common.ContractStatus;
import com.mkuiwu.douzero.runtime.contract.common.ErrorCode;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.infrastructure.model.douzero.DouZeroCardCodec;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** ResNet2 的请求和响应适配器；模型整数编码只在这里进入和离开 Java。 */
public final class ResNet2ProtocolAdapter {
    /** 当前支持的跨进程协议版本。 */
    public static final String CONTRACT_VERSION = "inference.v1";

    /** 当前适配器对应的模型标识。 */
    public static final String MODEL_ID = "resnet2";

    /** DouZero 稀疏整数编码器。 */
    private final DouZeroCardCodec cardCodec;

    public ResNet2ProtocolAdapter(DouZeroCardCodec cardCodec) {
        this.cardCodec = Objects.requireNonNull(cardCodec, "DouZero 牌面编码器不能为空");
    }

    /** 把内部语义请求转换为唯一的 ResNet2Request。 */
    public ResNet2Request encode(AdviceQuery query) {
        Objects.requireNonNull(query, "建议请求不能为空");
        return new ResNet2Request(
                CONTRACT_VERSION,
                query.requestId(),
                query.dealId(),
                MODEL_ID,
                query.deadlineMs(),
                encodeSeat(query.snapshot().localSeat()),
                cardCodec.encode(query.snapshot().hand()),
                cardCodec.encode(query.snapshot().bottomCards()),
                query.snapshot().history().stream()
                        .map(record -> cardCodec.encodeAction(record.action()))
                        .toList()
        );
    }

    /** 校验并解码明确类型的 ResNet2Response。 */
    public AdviceResult decode(AdviceQuery query, ResNet2Response response) {
        Objects.requireNonNull(query, "建议请求不能为空");
        if (response == null) {
            return unavailable(AdviceFailure.INVALID_RESPONSE, "模型响应不能为空");
        }
        AdviceResult identityFailure = validateIdentity(query, response);
        if (identityFailure != null) {
            return identityFailure;
        }
        if (response.status() != ContractStatus.OK) {
            return unavailable(mapFailure(response.errorCode()), response.errorMessage());
        }
        if (response.latencyMs() > query.deadlineMs()) {
            return unavailable(AdviceFailure.MODEL_TIMEOUT, "模型响应超过请求截止时间");
        }
        try {
            PlayAction action = cardCodec.decodeAction(response.action());
            List<ActionScore> actionScores = response.actionScores().stream()
                    .map(candidate -> new ActionScore(
                            cardCodec.decodeAction(candidate.action()), candidate.value()))
                    .toList();
            Map<String, String> metadata = new LinkedHashMap<>();
            metadata.put("modelVersion", response.modelVersion());
            metadata.put("latencyMs", Long.toString(response.latencyMs()));
            return new AdviceResult.Recommendation(
                    MODEL_ID,
                    action,
                    response.actionValue(),
                    response.actionMargin(),
                    actionScores,
                    metadata
            );
        } catch (IllegalArgumentException error) {
            return unavailable(AdviceFailure.INVALID_RESPONSE, error.getMessage());
        }
    }

    private AdviceResult validateIdentity(AdviceQuery query, ResNet2Response response) {
        if (!CONTRACT_VERSION.equals(response.contractVersion())) {
            return unavailable(AdviceFailure.UNSUPPORTED_CONTRACT, "模型响应协议版本不匹配");
        }
        if (!MODEL_ID.equals(response.modelId())) {
            return unavailable(AdviceFailure.UNSUPPORTED_MODEL, "模型响应标识不匹配");
        }
        if (!query.requestId().equals(response.requestId())
                || !query.dealId().equals(response.dealId())) {
            return unavailable(AdviceFailure.INVALID_RESPONSE, "模型响应请求或牌局身份不匹配");
        }
        return null;
    }

    private AdviceFailure mapFailure(ErrorCode code) {
        return switch (code) {
            case MODEL_UNAVAILABLE -> AdviceFailure.MODEL_UNAVAILABLE;
            case MODEL_TIMEOUT -> AdviceFailure.MODEL_TIMEOUT;
            case UNSUPPORTED_MODEL -> AdviceFailure.UNSUPPORTED_MODEL;
            case UNSUPPORTED_CONTRACT -> AdviceFailure.UNSUPPORTED_CONTRACT;
            case ILLEGAL_ACTION -> AdviceFailure.ILLEGAL_ACTION;
            case NO_RECOMMENDATION -> AdviceFailure.NO_RECOMMENDATION;
            default -> AdviceFailure.INVALID_RESPONSE;
        };
    }

    private AdviceResult.Unavailable unavailable(AdviceFailure failure, String detail) {
        String safeDetail = detail == null || detail.isBlank() ? failure.name() : detail;
        return new AdviceResult.Unavailable(MODEL_ID, failure, safeDetail);
    }

    private String encodeSeat(Seat seat) {
        return switch (Objects.requireNonNull(seat, "模型座位不能为空")) {
            case LANDLORD -> "landlord";
            case LANDLORD_DOWN -> "landlord_down";
            case LANDLORD_UP -> "landlord_up";
        };
    }
}
