package com.mkuiwu.douzero.runtime.infrastructure.execution;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.mkuiwu.douzero.runtime.application.model.ExecutionFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.CardPlayExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayButtonExecutionPort;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.Seat;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import org.junit.jupiter.api.Test;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * {@link JsonLineExecutionClient} 协议路由和结果映射测试。
 *
 * <p>使用 FakeProcess 模拟 Python worker，验证 READY 握手、OK/FAILED/UNCERTAIN
 * 三种状态的路由与映射、autoSubmitEcho 回显和局前按钮任务字段。</p>
 */
class JsonLineExecutionClientTest {

    private static final ObjectMapper MAPPER =
            new ObjectMapper().registerModule(new JavaTimeModule());
    private static final CardSet HAND = new CardSet(List.of(
            new Card(CardRank.THREE),
            new Card(CardRank.FIVE)
    ));

    @Test
    void routesPlayOkResultWithAutoSubmitEcho() throws Exception {
        // 场景：Python worker 返回 CARD_PLAY OK + autoSubmitEcho=true。
        // 预期：路由为 Executed，verifiedAt 和 autoSubmitEcho 正确解析。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            var job = client.execute(playRequest("play-ok", true));
            process.readStdin();
            process.writeStdout(MAPPER.writeValueAsString(Map.of(
                    "contractVersion", "execution.v1",
                    "messageType", "RESULT",
                    "taskType", "CARD_PLAY",
                    "requestId", "play-ok",
                    "dealId", "deal-001",
                    "generation", 3,
                    "status", "OK",
                    "autoSubmitEcho", true,
                    "verifiedAt", "2025-06-20T10:00:00Z"
            )));

            var result = job.completion().toCompletableFuture().get(1, TimeUnit.SECONDS);
            var executed = assertInstanceOf(CardPlayExecutionPort.Executed.class, result);
            assertEquals("play-ok", executed.identity().requestId());
            assertEquals(true, executed.autoSubmitEcho());
            assertEquals(Instant.parse("2025-06-20T10:00:00Z"), executed.verifiedAt());
        }
    }

    @Test
    void routesPlayFailedResult() throws Exception {
        // 场景：CARD_PLAY FAILED + HAND_MISMATCH。预期：路由为 ExecutionRejected。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            var job = client.execute(playRequest("play-fail", true));
            process.readStdin();
            process.writeStdout(MAPPER.writeValueAsString(Map.of(
                    "contractVersion", "execution.v1",
                    "messageType", "RESULT",
                    "taskType", "CARD_PLAY",
                    "requestId", "play-fail",
                    "dealId", "deal-001",
                    "generation", 3,
                    "status", "FAILED",
                    "errorCode", "HAND_MISMATCH",
                    "errorMessage", "hand differs"
            )));

            var result = job.completion().toCompletableFuture().get(1, TimeUnit.SECONDS);
            var rejected = assertInstanceOf(
                    CardPlayExecutionPort.ExecutionRejected.class, result);
            assertEquals(ExecutionFailure.HAND_MISMATCH, rejected.failure());
            assertEquals("hand differs", rejected.detail());
        }
    }

    @Test
    void routesPlayUncertainResult() throws Exception {
        // 场景：CARD_PLAY UNCERTAIN（fail-closed）。预期：路由为 ExecutionUncertain。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            var job = client.execute(passRequest("play-uncertain", true));
            process.readStdin();
            process.writeStdout(MAPPER.writeValueAsString(Map.of(
                    "contractVersion", "execution.v1",
                    "messageType", "RESULT",
                    "taskType", "CARD_PLAY",
                    "requestId", "play-uncertain",
                    "dealId", "deal-001",
                    "generation", 3,
                    "status", "UNCERTAIN",
                    "detail", "button_clicked_but_no_state_change_observed"
            )));

            var result = job.completion().toCompletableFuture().get(1, TimeUnit.SECONDS);
            var uncertain = assertInstanceOf(
                    CardPlayExecutionPort.ExecutionUncertain.class, result);
            assertEquals("button_clicked_but_no_state_change_observed", uncertain.detail());
        }
    }

    @Test
    void routesUnknownErrorCodeToInternalError() throws Exception {
        // 场景：Python worker 返回未知 errorCode。预期：降级映射为 INTERNAL_ERROR。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            var job = client.execute(passRequest("play-unknown-error", true));
            process.readStdin();
            process.writeStdout(MAPPER.writeValueAsString(Map.of(
                    "contractVersion", "execution.v1",
                    "messageType", "RESULT",
                    "taskType", "CARD_PLAY",
                    "requestId", "play-unknown-error",
                    "dealId", "deal-001",
                    "generation", 3,
                    "status", "FAILED",
                    "errorCode", "SOMETHING_NEW",
                    "errorMessage", "unknown error"
            )));

            var result = job.completion().toCompletableFuture().get(1, TimeUnit.SECONDS);
            var rejected = assertInstanceOf(
                    CardPlayExecutionPort.ExecutionRejected.class, result);
            assertEquals(ExecutionFailure.INTERNAL_ERROR, rejected.failure());
        }
    }

    @Test
    void cancelSendsCancelMessage() throws Exception {
        // 场景：取消在途任务。预期：向 Python 发送 CANCEL 消息，本地任务异常完成。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            var job = client.execute(passRequest("cancel-me", true));
            String stdinLine = process.readStdin();
            assertTrue(stdinLine.contains("\"requestId\":\"cancel-me\""));

            job.cancel();

            // CANCEL 消息应已发送。
            String cancelLine = process.readStdin();
            assertTrue(cancelLine.contains("\"messageType\":\"CANCEL\""));
            assertTrue(cancelLine.contains("\"requestId\":\"cancel-me\""));

            // 任务应异常完成（取消）。
            assertThrows(java.util.concurrent.CancellationException.class,
                    () -> job.completion().toCompletableFuture().get(100, TimeUnit.MILLISECONDS));
        }
    }

    @Test
    void stdinContainsProtocolVersionAndAutoSubmit() throws Exception {
        // 场景：提交 PLAY 任务后读取 stdin。预期：包含协议版本、CARD_PLAY、autoSubmit 和 recommendedCards。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            client.execute(playRequest("protocol-check", false));
            String line = process.readStdin();
            assertTrue(line.contains("\"contractVersion\":\"execution.v1\""));
            assertTrue(line.contains("\"taskType\":\"CARD_PLAY\""));
            assertTrue(line.contains("\"dealId\":\"deal-001\""));
            assertTrue(line.contains("\"generation\":3"));
            assertTrue(line.contains("\"actionType\":\"PLAY\""));
            assertTrue(line.contains("\"autoSubmit\":false"));
            assertTrue(line.contains("\"recommendedCards\":[\"5\"]"));
        }
    }

    @Test
    void passOmitRecommendedCards() throws Exception {
        // 场景：提交 PASS 任务后读取 stdin。预期：不包含 recommendedCards 字段。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            client.execute(passRequest("pass-check", true));
            String line = process.readStdin();
            assertTrue(line.contains("\"actionType\":\"PASS\""));
            assertTrue(line.contains("\"autoSubmit\":true"));
            // PASS 不应携带 recommendedCards。
            assertTrue(!line.contains("recommendedCards"));
        }
    }

    @Test
    void passCarriesConfiguredDelayButPlayDoesNot() throws Exception {
        // 场景：固定自动不出配置为 1.5 秒。预期：仅自动 PASS 传递 clickDelayMs，PLAY 保持无延迟字段。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process, 1500)) {
            ready(process);

            client.execute(passRequest("pass-delay", true));
            String passLine = process.readStdin();
            assertTrue(passLine.contains("\"clickDelayMs\":1500"));

            client.execute(playRequest("play-no-delay", true));
            String playLine = process.readStdin();
            assertTrue(!playLine.contains("clickDelayMs"));
        }
    }

    @Test
    void routesPreplayButtonResultAndUsesDedicatedWireTask() throws Exception {
        // 场景：提交叫地主正向局前任务。预期：使用 PREPLAY_BUTTON 字段并正确路由 OK 结果。
        FakeProcess process = new FakeProcess();
        try (JsonLineExecutionClient client = client(process)) {
            ready(process);

            GameTaskIdentity identity = new GameTaskIdentity("preplay-call", "deal-001", 3);
            var job = client.execute(new PreplayButtonExecutionPort.ExecutionRequest(
                    identity, PreplayStage.CALL_LANDLORD, PreplayAction.CALL, 10000));
            String line = process.readStdin();
            assertTrue(line.contains("\"taskType\":\"PREPLAY_BUTTON\""));
            assertTrue(line.contains("\"stage\":\"call\""));
            assertTrue(line.contains("\"action\":\"call\""));

            process.writeStdout(MAPPER.writeValueAsString(Map.of(
                    "contractVersion", "execution.v1",
                    "messageType", "RESULT",
                    "taskType", "PREPLAY_BUTTON",
                    "requestId", "preplay-call",
                    "dealId", "deal-001",
                    "generation", 3,
                    "status", "OK",
                    "verifiedAt", "2025-06-20T10:00:00Z"
            )));
            var result = job.completion().toCompletableFuture().get(1, TimeUnit.SECONDS);
            var executed = assertInstanceOf(PreplayButtonExecutionPort.Executed.class, result);
            assertEquals("preplay-call", executed.identity().requestId());
        }
    }

    // ===== 测试辅助 =====

    private JsonLineExecutionClient client(FakeProcess process) {
        return client(process, 0);
    }

    private JsonLineExecutionClient client(FakeProcess process, long autoPassDelayMs) {
        return new JsonLineExecutionClient(MAPPER, command -> process,
                List.of("python", "-m", "douzero_advisor.execution_service"), autoPassDelayMs);
    }

    private void ready(FakeProcess process) throws Exception {
        process.writeStdout(MAPPER.writeValueAsString(Map.of(
                "contractVersion", "execution.v1",
                "messageType", "READY",
                "taskTypes", List.of("CARD_PLAY")
        )));
    }

    private CardPlayExecutionPort.ExecutionRequest playRequest(
            String requestId, boolean autoSubmit) {
        GameTaskIdentity identity = new GameTaskIdentity(requestId, "deal-001", 3);
        CardSet recommended = new CardSet(List.of(new Card(CardRank.FIVE)));
        return new CardPlayExecutionPort.ExecutionRequest(
                identity, Seat.LANDLORD, HAND, recommended,
                CardPlayExecutionPort.ActionType.PLAY, autoSubmit, 10000);
    }

    private CardPlayExecutionPort.ExecutionRequest passRequest(
            String requestId, boolean autoSubmit) {
        GameTaskIdentity identity = new GameTaskIdentity(requestId, "deal-001", 3);
        return new CardPlayExecutionPort.ExecutionRequest(
                identity, Seat.LANDLORD, HAND, CardSet.empty(),
                CardPlayExecutionPort.ActionType.PASS, autoSubmit, 10000);
    }

    // ===== FakeProcess =====

    private static final class FakeProcess extends Process {
        private final PipedInputStream stdinReader = new PipedInputStream();
        private final PipedOutputStream javaStdin;
        private final PipedInputStream javaStdout = new PipedInputStream();
        private final PipedOutputStream stdoutWriter;
        private final PipedInputStream javaStderr = new PipedInputStream();
        private final PipedOutputStream stderrWriter;
        private final BufferedReader stdinLines;
        private final CountDownLatch exited = new CountDownLatch(1);
        private volatile boolean alive = true;
        private volatile int exitCode;

        private FakeProcess() throws IOException {
            javaStdin = new PipedOutputStream(stdinReader);
            stdoutWriter = new PipedOutputStream(javaStdout);
            stderrWriter = new PipedOutputStream(javaStderr);
            stdinLines = new BufferedReader(new InputStreamReader(stdinReader, StandardCharsets.UTF_8));
        }

        private String readStdin() throws IOException {
            return stdinLines.readLine();
        }

        private void writeStdout(String line) throws IOException {
            stdoutWriter.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            stdoutWriter.flush();
        }

        @Override
        public OutputStream getOutputStream() {
            return javaStdin;
        }

        @Override
        public InputStream getInputStream() {
            return javaStdout;
        }

        @Override
        public InputStream getErrorStream() {
            return javaStderr;
        }

        @Override
        public int waitFor() throws InterruptedException {
            exited.await();
            return exitCode;
        }

        @Override
        public int exitValue() {
            if (alive) {
                throw new IllegalThreadStateException("进程仍在运行");
            }
            return exitCode;
        }

        @Override
        public void destroy() {
            exit(0);
        }

        private void exit(int code) {
            if (!alive) {
                return;
            }
            alive = false;
            exitCode = code;
            exited.countDown();
            try {
                stdoutWriter.close();
                stderrWriter.close();
                javaStdin.close();
            } catch (IOException ignored) {
                // 关闭异常不影响测试。
            }
        }
    }
}
