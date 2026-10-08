package com.mkuiwu.douzero.runtime.infrastructure.desktop;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DesktopWebSocketGatewayTest {
    private static final String TOKEN = "0123456789abcdef0123456789abcdef";

    private final ObjectMapper objectMapper = new ObjectMapper();
    private DesktopWebSocketGateway gateway;

    @AfterEach
    void closeGateway() {
        if (gateway != null) gateway.close();
    }

    /** 验证正确令牌只能从回环 desktop.v1 路径连接，并能实时收到 Java 权威阶段事件。 */
    @Test
    void authorizedLoopbackClientReceivesReadyAndPhaseEvent() throws Exception {
        gateway = gateway();
        Client client = connect(TOKEN);

        JsonNode ready = objectMapper.readTree(client.message());
        assertEquals("desktop.v1", ready.path("protocol").asText());
        assertEquals("READY", ready.path("type").asText());
        assertTrue(ready.path("orchestrationEnabled").asBoolean());

        gateway.onPhaseChanged(GamePhase.PREPLAY, "deal-ws-1");
        JsonNode phase = objectMapper.readTree(client.message());
        assertEquals("PHASE_CHANGED", phase.path("type").asText());
        assertEquals("PREPLAY", phase.path("phase").asText());
        assertEquals("deal-ws-1", phase.path("dealId").asText());
        client.close();
    }

    /** 验证 Electron 重连同一 Java 实例时会收到当前阶段回放，且保留原始业务序号用于去重。 */
    @Test
    void reconnectReplaysCurrentPhaseWithOriginalSequence() throws Exception {
        gateway = gateway();
        gateway.onPhaseChanged(GamePhase.PLAYING, "deal-replay-1");

        Client first = connect(TOKEN);
        first.message();
        JsonNode original = objectMapper.readTree(first.message());
        first.close();

        Client second = connect(TOKEN);
        second.message();
        JsonNode replay = objectMapper.readTree(second.message());
        assertEquals("PLAYING", replay.path("phase").asText());
        assertEquals(original.path("sequence").asLong(), replay.path("sequence").asLong());
        second.close();
    }

    /** 验证错误令牌即使来自本机也会按策略拒绝，且不会泄露 READY 或牌局事件。 */
    @Test
    void invalidTokenIsClosedWithoutPublishingReady() throws Exception {
        gateway = gateway();
        Client client = connect("fedcba9876543210fedcba9876543210");
        gateway.onPhaseChanged(GamePhase.PREPLAY, "secret-deal");

        assertEquals(1008, client.closed().get(3, TimeUnit.SECONDS));
        assertTrue(client.messages.isEmpty());
    }

    /** 验证客户端向只读协议发送文本命令时会被拒绝，不能扩展成自动操作入口。 */
    @Test
    void clientCommandIsRejectedByReadOnlyProtocol() throws Exception {
        gateway = gateway();
        Client client = connect(TOKEN);
        client.message();
        client.socket.sendText("{\"type\":\"PLAY\"}", true).join();

        assertEquals(1003, client.closed().get(3, TimeUnit.SECONDS));

        Client binaryClient = connect(TOKEN);
        binaryClient.message();
        binaryClient.socket.sendBinary(java.nio.ByteBuffer.wrap(new byte[]{1, 2, 3}), true).join();
        assertEquals(1003, binaryClient.closed().get(3, TimeUnit.SECONDS));
    }

    private DesktopWebSocketGateway gateway() {
        DesktopWebSocketGateway value = new DesktopWebSocketGateway(0, TOKEN, true, objectMapper);
        value.startAndAwait(Duration.ofSeconds(3));
        return value;
    }

    private Client connect(String token) {
        Client listener = new Client();
        listener.socket = HttpClient.newHttpClient()
                .newWebSocketBuilder()
                .header("Authorization", "Bearer " + token)
                .connectTimeout(Duration.ofSeconds(3))
                .buildAsync(URI.create("ws://127.0.0.1:" + gateway.boundPort() + "/desktop/v1"), listener)
                .join();
        return listener;
    }

    private static final class Client implements WebSocket.Listener {
        /** 每个完整文本帧对应一条 desktop.v1 JSON 事件。 */
        private final LinkedBlockingQueue<String> messages = new LinkedBlockingQueue<>();
        /** 连接关闭码用于验证鉴权和只读策略。 */
        private final CompletableFuture<Integer> closed = new CompletableFuture<>();
        /** JDK WebSocket 连接，仅由当前测试客户端拥有。 */
        private WebSocket socket;
        private final StringBuilder text = new StringBuilder();

        @Override
        public void onOpen(WebSocket webSocket) {
            webSocket.request(1);
        }

        @Override
        public java.util.concurrent.CompletionStage<?> onText(
                WebSocket webSocket,
                CharSequence data,
                boolean last
        ) {
            text.append(data);
            if (last) {
                messages.add(text.toString());
                text.setLength(0);
            }
            webSocket.request(1);
            return null;
        }

        @Override
        public java.util.concurrent.CompletionStage<?> onClose(
                WebSocket webSocket,
                int statusCode,
                String reason
        ) {
            closed.complete(statusCode);
            return null;
        }

        private String message() throws InterruptedException {
            String value = messages.poll(3, TimeUnit.SECONDS);
            assertNotNull(value, "截止时间内应收到 desktop.v1 事件");
            return value;
        }

        private CompletableFuture<Integer> closed() {
            return closed;
        }

        private void close() {
            socket.sendClose(WebSocket.NORMAL_CLOSURE, "test complete").join();
        }
    }
}
