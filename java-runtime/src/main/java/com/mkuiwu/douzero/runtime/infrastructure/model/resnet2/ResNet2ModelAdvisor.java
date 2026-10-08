package com.mkuiwu.douzero.runtime.infrastructure.model.resnet2;

import com.mkuiwu.douzero.runtime.application.model.AdviceFailure;
import com.mkuiwu.douzero.runtime.application.model.AdviceQuery;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.port.DecisionPort;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransportException;

import java.util.Objects;

/** 通过可替换传输调用 ResNet2，并把线协议完全隔离在基础设施层。 */
public final class ResNet2ModelAdvisor implements DecisionPort {
    /** ResNet2 线协议编解码器。 */
    private final ResNet2ProtocolAdapter protocolAdapter;

    /** HTTP、进程管道或测试 Fake 提供的模型传输实现。 */
    private final ModelTransport<ResNet2Request, ResNet2Response> transport;

    public ResNet2ModelAdvisor(
            ResNet2ProtocolAdapter protocolAdapter,
            ModelTransport<ResNet2Request, ResNet2Response> transport
    ) {
        this.protocolAdapter = Objects.requireNonNull(protocolAdapter, "ResNet2 协议适配器不能为空");
        this.transport = Objects.requireNonNull(transport, "模型传输不能为空");
    }

    @Override
    public AdviceResult decide(AdviceQuery query) {
        ResNet2Request request = protocolAdapter.encode(query);
        ResNet2Response response;
        try {
            response = transport.exchange(request);
        } catch (ModelTransportException error) {
            AdviceFailure failure = switch (error.reason()) {
                case TIMEOUT -> AdviceFailure.MODEL_TIMEOUT;
                case INVALID_RESPONSE -> AdviceFailure.INVALID_RESPONSE;
                case UNAVAILABLE -> AdviceFailure.MODEL_UNAVAILABLE;
            };
            return new AdviceResult.Unavailable(
                    ResNet2ProtocolAdapter.MODEL_ID,
                    failure,
                    switch (failure) {
                        case MODEL_TIMEOUT -> "模型传输超时";
                        case INVALID_RESPONSE -> "模型响应身份不匹配";
                        default -> "模型传输失败";
                    }
            );
        } catch (RuntimeException error) {
            return new AdviceResult.Unavailable(
                    ResNet2ProtocolAdapter.MODEL_ID,
                    AdviceFailure.MODEL_UNAVAILABLE,
                    "模型传输失败"
            );
        }
        return protocolAdapter.decode(query, response);
    }
}
