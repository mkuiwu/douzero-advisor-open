package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

import ch.qos.logback.classic.Level;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceRequest;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.testsupport.LogCapture;
import org.junit.jupiter.api.Test;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PipedInputStream;
import java.io.PipedOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JsonLinePreplayModelClientTest {
    private static final String READY =
            "{\"contractVersion\":\"preplay-inference.v1\",\"messageType\":\"READY\"}";

    /** 验证首次交换前必须消费 READY，且 stderr 与 JSONL 响应通道隔离。 */
    @Test
    void consumesReadyBeforeFirstExchangeAndKeepsStderrSeparate() throws Exception {
        ModelProcess process = readyProcess();
        QueueLauncher launcher = new QueueLauncher(process);
        try (LogCapture logs = LogCapture.capture(JsonLinePreplayModelClient.class);
             JsonLinePreplayModelClient client = client(launcher)) {
            client.start(500);
            process.writeStderr("legacy diagnostic");
            process.writeStdout(response("r1"));

            var response = client.exchange(request("r1", 500));

            assertEquals("call", response.action());
            assertTrue(process.readStdin().contains("\"callPromptSeen\":true"));
            assertEquals(1, launcher.launchCount);
            assertTrue(logs.contains(Level.INFO,
                    "调用 Python 局前模型接口 requestId=r1 dealId=d1"));
            assertTrue(logs.contains(Level.INFO,
                    "Python 局前模型接口返回 requestId=r1 dealId=d1"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python 局前模型请求明细 request=PreplayInferenceRequest"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python 局前模型响应明细 response=PreplayInferenceResponse"));
        }
    }

    /** 验证局前模型超时会销毁当前代，下一次交换通过新的 READY 恢复。 */
    @Test
    void timeoutDestroysGenerationAndNextExchangeRestartsThroughReady() throws Exception {
        ModelProcess slow = readyProcess();
        ModelProcess recovered = readyProcess();
        recovered.writeStdout(response("r2"));
        QueueLauncher launcher = new QueueLauncher(slow, recovered);
        try (JsonLinePreplayModelClient client = client(launcher)) {
            PreplayModelTransportException timeout = assertThrows(
                    PreplayModelTransportException.class,
                    () -> client.exchange(request("r1", 30)));

            assertEquals(PreplayModelTransportException.Reason.TIMEOUT, timeout.reason());
            assertFalse(slow.isAlive());
            assertEquals("r2", client.exchange(request("r2", 500)).requestId());
            assertTrue(recovered.readStdin().contains("\"requestId\":\"r2\""));
            assertEquals(2, launcher.launchCount);
        }
    }

    /** 验证 stdout EOF 会明确失败当前交换，后续请求使用全新的响应队列。 */
    @Test
    void stdoutEofFailsCurrentExchangeAndNextExchangeUsesFreshQueue() throws Exception {
        ModelProcess ended = readyProcess();
        ModelProcess recovered = readyProcess();
        recovered.writeStdout(response("r2"));
        QueueLauncher launcher = new QueueLauncher(ended, recovered);
        try (JsonLinePreplayModelClient client = client(launcher)) {
            ExecutorService responder = Executors.newSingleThreadExecutor();
            Future<?> closeOutput = responder.submit(() -> {
                try {
                    ended.readStdin();
                    ended.endStdout();
                } catch (IOException error) {
                    throw new IllegalStateException(error);
                }
            });

            PreplayModelTransportException eof = assertThrows(
                    PreplayModelTransportException.class,
                    () -> client.exchange(request("r1", 500)));
            closeOutput.get(1, TimeUnit.SECONDS);
            responder.shutdownNow();

            assertEquals(PreplayModelTransportException.Reason.UNAVAILABLE, eof.reason());
            assertEquals("r2", client.exchange(request("r2", 500)).requestId());
            assertEquals(2, launcher.launchCount);
        }
    }

    /** 验证多个局前交换保持一问一答串行，避免响应串单。 */
    @Test
    void concurrentExchangesRemainRequestResponseSerialized() throws Exception {
        ModelProcess process = readyProcess();
        QueueLauncher launcher = new QueueLauncher(process);
        ObjectMapper mapper = new ObjectMapper();
        List<String> received = new ArrayList<>();
        ExecutorService responder = Executors.newSingleThreadExecutor();
        Future<?> responses = responder.submit(() -> {
            try {
                for (int index = 0; index < 2; index++) {
                    JsonNode request = mapper.readTree(process.readStdin());
                    String requestId = request.get("requestId").asText();
                    received.add(requestId);
                    process.writeStdout(response(requestId));
                }
            } catch (IOException error) {
                throw new IllegalStateException(error);
            }
        });
        try (JsonLinePreplayModelClient client = client(launcher)) {
            ExecutorService callers = Executors.newFixedThreadPool(2);
            Future<String> first = callers.submit(() ->
                    client.exchange(request("r1", 500)).requestId());
            Future<String> second = callers.submit(() ->
                    client.exchange(request("r2", 500)).requestId());

            assertEquals("r1", first.get(1, TimeUnit.SECONDS));
            assertEquals("r2", second.get(1, TimeUnit.SECONDS));
            responses.get(1, TimeUnit.SECONDS);
            callers.shutdownNow();
            responder.shutdownNow();
            assertEquals(2, received.size());
            assertEquals(1, launcher.launchCount);
        }
    }

    /** 验证关闭会唤醒在途交换，并永久禁止 worker 再次启动。 */
    @Test
    void closeWakesInflightExchangeAndPermanentlyForbidsRestart() throws Exception {
        ModelProcess process = readyProcess();
        QueueLauncher launcher = new QueueLauncher(process);
        JsonLinePreplayModelClient client = client(launcher);
        ExecutorService callers = Executors.newSingleThreadExecutor();
        Future<String> exchange = callers.submit(() ->
                client.exchange(request("r1", 5_000)).requestId());
        process.readStdin();

        client.close();

        assertThrows(java.util.concurrent.ExecutionException.class,
                () -> exchange.get(1, TimeUnit.SECONDS));
        assertThrows(PreplayModelTransportException.class,
                () -> client.exchange(request("r2", 500)));
        assertEquals(1, launcher.launchCount);
        assertFalse(process.isAlive());
        callers.shutdownNow();
    }

    private JsonLinePreplayModelClient client(ProcessLauncher launcher) {
        return new JsonLinePreplayModelClient(
                new ObjectMapper(), launcher, List.of("python", "preplay.py"));
    }

    private PreplayInferenceRequest request(String requestId, long deadlineMs) {
        return new PreplayInferenceRequest(
                "preplay-inference.v1", requestId, "d1", 1, "bid-v1", deadlineMs,
                "call", seventeenCards(), List.of(), List.of("call", "no_call"),
                true, false);
    }

    private static ModelProcess readyProcess() throws IOException {
        ModelProcess process = new ModelProcess();
        process.writeStdout(READY);
        return process;
    }

    private static String response(String requestId) {
        return "{\"contractVersion\":\"preplay-inference.v1\","
                + "\"requestId\":\"" + requestId + "\",\"dealId\":\"d1\",\"generation\":1,"
                + "\"modelId\":\"bid-v1\",\"status\":\"OK\",\"action\":\"call\","
                + "\"score\":0.4,\"threshold\":0.2,\"decisionReason\":\"score_vs_threshold\","
                + "\"modelVersion\":\"m1\",\"latencyMs\":3,"
                + "\"errorCode\":null,\"errorMessage\":null}";
    }

    private List<String> seventeenCards() {
        return List.of("D", "X", "2", "2", "A", "A", "K", "K", "Q", "Q",
                "J", "J", "10", "9", "8", "7", "3");
    }

    /** 按配置顺序交付新 worker，用于验证代次重启次数。 */
    private static final class QueueLauncher implements ProcessLauncher {
        /** 尚未启动的测试 worker。 */
        private final Queue<ModelProcess> processes = new ArrayDeque<>();

        /** 实际启动次数；关闭后的请求不得增加。 */
        private int launchCount;

        private QueueLauncher(ModelProcess... processes) {
            this.processes.addAll(List.of(processes));
        }

        @Override
        public synchronized Process launch(List<String> command) {
            launchCount++;
            ModelProcess process = processes.poll();
            if (process == null) {
                throw new IllegalStateException("没有可用测试 worker");
            }
            return process;
        }
    }

    /** 可由测试线程控制 READY、响应、EOF 和退出的模型进程。 */
    private static final class ModelProcess extends Process {
        private final PipedInputStream stdinReader = new PipedInputStream();
        private final PipedOutputStream javaStdin;
        private final PipedInputStream javaStdout = new PipedInputStream();
        private final PipedOutputStream stdoutWriter;
        private final PipedInputStream javaStderr = new PipedInputStream();
        private final PipedOutputStream stderrWriter;
        private final BufferedReader stdinLines;
        private final CountDownLatch exited = new CountDownLatch(1);
        private volatile boolean alive = true;

        private ModelProcess() throws IOException {
            javaStdin = new PipedOutputStream(stdinReader);
            stdoutWriter = new PipedOutputStream(javaStdout);
            stderrWriter = new PipedOutputStream(javaStderr);
            stdinLines = new BufferedReader(new InputStreamReader(
                    stdinReader, StandardCharsets.UTF_8));
        }

        private synchronized void writeStdout(String line) throws IOException {
            stdoutWriter.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            stdoutWriter.flush();
        }

        private synchronized void writeStderr(String line) throws IOException {
            stderrWriter.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            stderrWriter.flush();
        }

        private String readStdin() throws IOException {
            return stdinLines.readLine();
        }

        private synchronized void endStdout() throws IOException {
            stdoutWriter.close();
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
            return 0;
        }

        @Override
        public int exitValue() {
            if (alive) {
                throw new IllegalThreadStateException("进程仍在运行");
            }
            return 0;
        }

        @Override
        public synchronized void destroy() {
            if (!alive) {
                return;
            }
            alive = false;
            try {
                javaStdin.close();
                stdoutWriter.close();
                stderrWriter.close();
            } catch (IOException ignored) {
                // 测试进程关闭阶段只需释放管道。
            }
            exited.countDown();
        }

        @Override
        public boolean isAlive() {
            return alive;
        }
    }
}
