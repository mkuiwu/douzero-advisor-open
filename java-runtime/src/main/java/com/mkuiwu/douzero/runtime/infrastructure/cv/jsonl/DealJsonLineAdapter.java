package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;

import java.util.Objects;

/** 通过持久 Python CV worker 实现牌局初始化识别端口。 */
public final class DealJsonLineAdapter implements DealRecognitionPort {
    private final JsonLineRecognitionClient client;
    private final RecognitionProtocolAdapter protocol;

    public DealJsonLineAdapter(JsonLineRecognitionClient client,
                               RecognitionProtocolAdapter protocol) {
        this.client = Objects.requireNonNull(client, "识别客户端不能为空");
        this.protocol = Objects.requireNonNull(protocol, "识别协议适配器不能为空");
    }

    @Override
    public RecognitionJob<Result> recognizeDeal(Request request) {
        RecognitionSubmit submit = protocol.encode(request);
        return new JsonLineRecognitionJob<>(submit.requestId(),
                client.submit(submit).thenApply(result -> protocol.decode(request, result)),
                client, protocol.cancel(submit));
    }
}
