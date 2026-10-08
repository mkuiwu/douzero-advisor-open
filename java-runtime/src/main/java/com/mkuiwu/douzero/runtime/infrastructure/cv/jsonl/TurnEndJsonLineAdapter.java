package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;

import java.util.Objects;

/** 通过持久 Python CV worker 实现建议后本方回合结束探测端口。 */
public final class TurnEndJsonLineAdapter implements TurnEndRecognitionPort {
    private final JsonLineRecognitionClient client;
    private final RecognitionProtocolAdapter protocol;

    public TurnEndJsonLineAdapter(JsonLineRecognitionClient client,
                                  RecognitionProtocolAdapter protocol) {
        this.client = Objects.requireNonNull(client, "识别客户端不能为空");
        this.protocol = Objects.requireNonNull(protocol, "识别协议适配器不能为空");
    }

    @Override
    public RecognitionJob<Result> waitUntilEnded(Request request) {
        RecognitionSubmit submit = protocol.encode(request);
        return new JsonLineRecognitionJob<>(submit.requestId(),
                client.submit(submit).thenApply(result -> protocol.decode(request, result)),
                client, protocol.cancel(submit));
    }
}
