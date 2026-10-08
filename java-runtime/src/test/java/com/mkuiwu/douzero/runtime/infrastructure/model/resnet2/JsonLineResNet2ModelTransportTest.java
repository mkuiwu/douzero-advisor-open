package com.mkuiwu.douzero.runtime.infrastructure.model.resnet2;

import ch.qos.logback.classic.Level;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransportException;
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

class JsonLineResNet2ModelTransportTest {
    private static final String READY = "{\"contractVersion\":\"inference.v1\","
            + "\"messageType\":\"READY\",\"modelId\":\"resnet2\"}";

    /** 验证 ResNet2 worker 先完成 READY，再接收扁平请求，stderr 保持协议外。 */
    @Test
    void consumesReadySendsFlatRequestAndKeepsStderrSeparate() throws Exception {
        ModelProcess process = readyProcess();
        process.writeStdout(response("r1", "d1"));
        QueueLauncher launcher = new QueueLauncher(process);
        try (LogCapture logs = LogCapture.capture(JsonLineResNet2ModelTransport.class);
             JsonLineResNet2ModelTransport transport = transport(launcher)) {
            transport.start();
            process.writeStderr("model diagnostic");

            var response = transport.exchange(request("r1", "d1", 500));

            JsonNode sent = new ObjectMapper().readTree(process.readStdin());
            assertEquals("r1", response.requestId());
            assertEquals("r1", sent.get("requestId").asText());
            assertFalse(sent.has("request"), "线协议请求必须保持扁平 DTO");
            assertEquals(1, launcher.launchCount);
            assertTrue(logs.contains(Level.INFO,
                    "调用 Python 正式出牌模型接口 requestId=r1 dealId=d1"));
            assertTrue(logs.contains(Level.INFO,
                    "Python 正式出牌模型接口返回 requestId=r1 dealId=d1"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python 正式出牌模型请求明细 request=ResNet2Request"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python 正式出牌模型响应明细 response=ResNet2Response"));
        }
    }

    /** 验证响应身份与请求不一致时销毁 worker，防止后续响应继续串单。 */
    @Test
    void mismatchedIdentityDestroysWorker() throws Exception {
        ModelProcess process = readyProcess();
        process.writeStdout(response("stale", "d1"));
        try (JsonLineResNet2ModelTransport transport = transport(
                new QueueLauncher(process))) {
            ModelTransportException mismatch = assertThrows(
                    ModelTransportException.class,
                    () -> transport.exchange(request("r1", "d1", 500)));

            assertEquals(ModelTransportException.Reason.INVALID_RESPONSE, mismatch.reason());
            assertTrue(mismatch.getMessage().contains("身份"));
            assertFalse(process.isAlive());
        }
    }

    /** 验证模型调用超时会淘汰当前代，下一次交换通过新 READY 恢复。 */
    @Test
    void timeoutDestroysGenerationAndNextExchangeRestartsThroughReady() throws Exception {
        ModelProcess slow = readyProcess();
        ModelProcess recovered = readyProcess();
        recovered.writeStdout(response("r2", "d1"));
        QueueLauncher launcher = new QueueLauncher(slow, recovered);
        try (JsonLineResNet2ModelTransport transport = transport(launcher)) {
            ModelTransportException timeout = assertThrows(
                    ModelTransportException.class,
                    () -> transport.exchange(request("r1", "d1", 30)));

            assertEquals(ModelTransportException.Reason.TIMEOUT, timeout.reason());
            assertFalse(slow.isAlive());
            assertEquals("r2", transport.exchange(request("r2", "d1", 500)).requestId());
            assertEquals(2, launcher.launchCount);
        }
    }

    /** 验证 stdout EOF 会失败当前交换，下一次请求使用全新 worker 代。 */
    @Test
    void stdoutEofFailsCurrentExchangeAndNextExchangeUsesFreshGeneration() throws Exception {
        ModelProcess ended = readyProcess();
        ModelProcess recovered = readyProcess();
        recovered.writeStdout(response("r2", "d1"));
        QueueLauncher launcher = new QueueLauncher(ended, recovered);
        try (JsonLineResNet2ModelTransport transport = transport(launcher)) {
            ExecutorService responder = Executors.newSingleThreadExecutor();
            Future<?> closeOutput = responder.submit(() -> {
                try {
                    ended.readStdin();
                    ended.endStdout();
                } catch (IOException error) {
                    throw new IllegalStateException(error);
                }
            });

            ModelTransportException eof = assertThrows(
                    ModelTransportException.class,
                    () -> transport.exchange(request("r1", "d1", 500)));
            closeOutput.get(1, TimeUnit.SECONDS);
            responder.shutdownNow();

            assertEquals(ModelTransportException.Reason.UNAVAILABLE, eof.reason());
            assertEquals("r2", transport.exchange(request("r2", "d1", 500)).requestId());
            assertEquals(2, launcher.launchCount);
        }
    }

    /** 验证 stdout 出现非协议内容时明确失败并淘汰受污染的 worker。 */
    @Test
    void stdoutPollutionFailsExplicitlyAndRetiresGeneration() throws Exception {
        ModelProcess process = readyProcess();
        process.writeStdout("third party debug output");
        try (JsonLineResNet2ModelTransport transport = transport(
                new QueueLauncher(process))) {
            ModelTransportException pollution = assertThrows(
                    ModelTransportException.class,
                    () -> transport.exchange(request("r1", "d1", 500)));

            assertTrue(pollution.getMessage().contains("stdout 协议污染"));
            assertFalse(process.isAlive());
        }
    }

    /** 验证并发模型调用在单一 JSONL worker 上保持请求响应串行对应。 */
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
                    process.writeStdout(response(requestId, "d1"));
                }
            } catch (IOException error) {
                throw new IllegalStateException(error);
            }
        });
        try (JsonLineResNet2ModelTransport transport = transport(launcher)) {
            ExecutorService callers = Executors.newFixedThreadPool(2);
            Future<String> first = callers.submit(() ->
                    transport.exchange(request("r1", "d1", 500)).requestId());
            Future<String> second = callers.submit(() ->
                    transport.exchange(request("r2", "d1", 500)).requestId());

            assertEquals("r1", first.get(1, TimeUnit.SECONDS));
            assertEquals("r2", second.get(1, TimeUnit.SECONDS));
            responses.get(1, TimeUnit.SECONDS);
            callers.shutdownNow();
            responder.shutdownNow();
            assertEquals(List.of("r1", "r2"), received);
            assertEquals(1, launcher.launchCount);
        }
    }

    /** 验证关闭会唤醒在途模型调用，并永久禁止传输层重启。 */
    @Test
    void closeWakesInflightExchangeAndPermanentlyForbidsRestart() throws Exception {
        ModelProcess process = readyProcess();
        QueueLauncher launcher = new QueueLauncher(process);
        JsonLineResNet2ModelTransport transport = transport(launcher);
        ExecutorService callers = Executors.newSingleThreadExecutor();
        Future<String> exchange = callers.submit(() ->
                transport.exchange(request("r1", "d1", 5_000)).requestId());
        process.readStdin();

        transport.close();

        assertThrows(java.util.concurrent.ExecutionException.class,
                () -> exchange.get(1, TimeUnit.SECONDS));
        assertThrows(ModelTransportException.class,
                () -> transport.exchange(request("r2", "d1", 500)));
        assertEquals(1, launcher.launchCount);
        assertFalse(process.isAlive());
        callers.shutdownNow();
    }

    private JsonLineResNet2ModelTransport transport(ProcessLauncher launcher) {
        return new JsonLineResNet2ModelTransport(
                new ObjectMapper(), launcher, List.of("python", "resnet2_worker.py"), 500);
    }

    private ResNet2Request request(String requestId, String dealId, long deadlineMs) {
        return new ResNet2Request(
                "inference.v1", requestId, dealId, "resnet2", deadlineMs,
                "landlord", List.of(3), List.of(4, 5, 6), List.of());
    }

    private static ModelProcess readyProcess() throws IOException {
        ModelProcess process = new ModelProcess();
        process.writeStdout(READY);
        return process;
    }

    private static String response(String requestId, String dealId) {
        return "{\"contractVersion\":\"inference.v1\","
                + "\"requestId\":\"" + requestId + "\",\"dealId\":\"" + dealId + "\","
                + "\"modelId\":\"resnet2\",\"status\":\"OK\",\"action\":[],"
                + "\"actionValue\":0.8,\"actionMargin\":0.0,"
                + "\"actionScores\":[{\"action\":[],\"value\":0.8}],"
                + "\"modelVersion\":\"m1\",\"latencyMs\":3,"
                + "\"errorCode\":\"NONE\",\"errorMessage\":\"\"}";
    }

    /** 按配置顺序交付新 worker，用于验证代次重启。 */
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
