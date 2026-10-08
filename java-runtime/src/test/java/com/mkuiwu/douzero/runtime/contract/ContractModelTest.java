package com.mkuiwu.douzero.runtime.contract;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.common.ContractStatus;
import com.mkuiwu.douzero.runtime.contract.common.ErrorCode;
import com.mkuiwu.douzero.runtime.contract.model.douzero.DouZeroActionScore;
import com.mkuiwu.douzero.runtime.contract.model.douzero.DouZeroRequest;
import com.mkuiwu.douzero.runtime.contract.model.douzero.DouZeroResponse;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2ActionScore;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ContractModelTest {
    private final ObjectMapper mapper = new ObjectMapper();

    /** 验证每个模型在线协议中恰好拥有一个明确请求 DTO 和一个明确响应 DTO。 */
    @Test
    void eachModelHasOneExplicitRequestAndResponseDto() throws Exception {
        DouZeroRequest douZeroRequest = new DouZeroRequest(
                "inference.v1", "request-1", "deal-1", "original", 1500,
                "landlord", List.of(3), List.of(4, 5, 6), List.of());
        ResNet2Request resNet2Request = new ResNet2Request(
                "inference.v1", "request-2", "deal-1", "resnet2", 1500,
                "landlord", List.of(3), List.of(4, 5, 6), List.of());
        DouZeroResponse douZeroResponse = new DouZeroResponse(
                "inference.v1", "request-1", "deal-1", "original",
                ContractStatus.OK, List.of(), 0.8, 0.0,
                List.of(new DouZeroActionScore(List.of(), 0.8)),
                "original-v1", 10, ErrorCode.NONE, "");
        ResNet2Response resNet2Response = new ResNet2Response(
                "inference.v1", "request-2", "deal-1", "resnet2",
                ContractStatus.OK, List.of(), 0.7, 0.0,
                List.of(new ResNet2ActionScore(List.of(), 0.7)),
                "resnet2-v1", 10, ErrorCode.NONE, "");

        assertEquals("original", mapper.readValue(
                mapper.writeValueAsBytes(douZeroRequest), DouZeroRequest.class).modelId());
        assertEquals("resnet2", mapper.readValue(
                mapper.writeValueAsBytes(resNet2Request), ResNet2Request.class).modelId());
        assertEquals(List.of(), douZeroResponse.action());
        assertEquals(List.of(), resNet2Response.action());
        assertEquals(0.7, resNet2Response.actionValue());
        assertEquals(1, resNet2Response.actionScores().size());
        assertFalse(mapper.writeValueAsString(resNet2Response).contains("result"));
    }

    /** 验证失败响应不会携带动作或虚构模型版本，避免被误当成可信建议。 */
    @Test
    void failedResponseHasNoActionOrInventedModelVersion() {
        ResNet2Response response = new ResNet2Response(
                "inference.v1", "request-1", "deal-1", "resnet2",
                ContractStatus.ERROR, null, null, null, null, null, 10,
                ErrorCode.MODEL_UNAVAILABLE, "模型尚未加载");

        assertNull(response.action());
        assertNull(response.actionScores());
        assertNull(response.modelVersion());
    }

    /** 验证等待响应必须给出结构化错误码，使 Java 能稳定决定恢复路径。 */
    @Test
    void waitResponseRequiresAnErrorCode() {
        assertThrows(IllegalArgumentException.class, () -> new ResNet2Response(
                "inference.v1", "request-1", "deal-1", "resnet2",
                ContractStatus.WAIT, null, null, null, null, null, 10,
                ErrorCode.NONE, "等待"));
    }
}
