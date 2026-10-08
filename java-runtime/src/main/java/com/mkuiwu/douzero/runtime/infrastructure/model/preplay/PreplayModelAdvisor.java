package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.PreplayQuery;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.port.PreplayDecisionPort;

import java.util.Objects;

/** 通过独立持久串行 Python worker 实现局前模型决策端口。 */
public final class PreplayModelAdvisor implements PreplayDecisionPort {
    private final String modelId;
    private final PreplayProtocolAdapter protocol;
    private final JsonLinePreplayModelClient client;

    public PreplayModelAdvisor(String modelId, PreplayProtocolAdapter protocol,
                               JsonLinePreplayModelClient client) {
        if (modelId == null || modelId.isBlank()) {
            throw new IllegalArgumentException("局前模型标识不能为空");
        }
        this.modelId = modelId;
        this.protocol = Objects.requireNonNull(protocol, "局前模型协议适配器不能为空");
        this.client = Objects.requireNonNull(client, "局前模型客户端不能为空");
    }

    @Override
    public PreplayResult decide(PreplayQuery query) {
        try {
            return protocol.decode(query, client.exchange(protocol.encode(query)));
        } catch (PreplayModelTransportException error) {
            AdviceFailure failure = error.reason() == PreplayModelTransportException.Reason.TIMEOUT
                    ? AdviceFailure.MODEL_TIMEOUT : AdviceFailure.MODEL_UNAVAILABLE;
            return new PreplayResult.Unavailable(query.identity(), modelId, failure,
                    failure == AdviceFailure.MODEL_TIMEOUT ? "局前模型传输超时" : "局前模型传输失败");
        } catch (RuntimeException error) {
            return new PreplayResult.Unavailable(query.identity(), modelId,
                    AdviceFailure.MODEL_UNAVAILABLE, "局前模型传输失败");
        }
    }
}
