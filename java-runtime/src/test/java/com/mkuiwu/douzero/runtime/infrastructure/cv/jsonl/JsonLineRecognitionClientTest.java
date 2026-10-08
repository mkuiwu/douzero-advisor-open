package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import ch.qos.logback.classic.Level;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionMessageType;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionReady;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionResult;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionStatus;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType;
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
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JsonLineRecognitionClientTest {
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());

    /** 验证乱序响应仍按 requestId 投递，stderr 诊断不会污染 stdout 协议。 */
    @Test
    void routesOutOfOrderResultsByRequestIdAndKeepsStderrOutOfProtocol() throws Exception {
        FakeProcess process = new FakeProcess();
        try (LogCapture logs = LogCapture.capture(JsonLineRecognitionClient.class);
             JsonLineRecognitionClient client = client(process)) {
            client.start();
            process.writeStdout(mapper.writeValueAsString(new RecognitionReady(
                    RecognitionProtocolAdapter.CONTRACT_VERSION, RecognitionMessageType.READY,
                    List.of(RecognitionTaskType.values()))));
            client.readiness().toCompletableFuture().get(1, TimeUnit.SECONDS);

            var first = client.submit(newGame("first"));
            var second = client.submit(newGame("second"));
            assertTrue(process.readStdin().contains("\"requestId\":\"first\""));
            assertTrue(process.readStdin().contains("\"requestId\":\"second\""));

            process.writeStderr("not-json diagnostic");
            process.writeStdout(mapper.writeValueAsString(newGameResult("second")));
            process.writeStdout(mapper.writeValueAsString(newGameResult("first")));

            assertEquals("first", first.toCompletableFuture().get(1, TimeUnit.SECONDS).requestId());
            assertEquals("second", second.toCompletableFuture().get(1, TimeUnit.SECONDS).requestId());
            assertTrue(logs.contains(Level.INFO,
                    "调用 Python CV 识别接口 taskType=NEW_GAME requestId=first"));
            assertTrue(logs.contains(Level.INFO,
                    "Python CV 识别接口返回 taskType=NEW_GAME requestId=first"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python CV 识别请求明细 request=RecognitionSubmit"));
            assertTrue(logs.contains(Level.DEBUG,
                    "Python CV 识别响应明细 response=RecognitionResult"));
        }
    }

    /** 验证取消操作幂等，并会在本地立即结束对应在途任务。 */
    @Test
    void cancelIsIdempotentAndLocallyCompletesTask() throws Exception {
        FakeProcess process = new FakeProcess();
        try (JsonLineRecognitionClient client = client(process)) {
            client.start();
            ready(process);
            RecognitionSubmit submit = newGame("cancel-me");
            var completion = client.submit(submit);
            process.readStdin();
            var cancel = new RecognitionProtocolAdapter().cancel(submit);
            client.cancel(cancel);
            client.cancel(cancel);
            String line = process.readStdin();
            assertTrue(line.contains("\"messageType\":\"CANCEL\""));
            Thread.sleep(30);
            assertEquals(0, process.stdinAvailable());
            assertThrows(Exception.class,
                    () -> completion.toCompletableFuture().get(1, TimeUnit.SECONDS));
        }
    }

    /** 验证 stdout 脏数据或 EOF 会使在途任务明确失败，绝不会被解释为业务结果。 */
    @Test
    void stdoutPollutionAndEofFailPendingInsteadOfBecomingBusinessData() throws Exception {
        FakeProcess polluted = new FakeProcess();
        try (JsonLineRecognitionClient client = client(polluted)) {
            client.start();
            ready(polluted);
            var completion = client.submit(newGame("polluted"));
            polluted.readStdin();
            polluted.writeStdout("this is a log line");
            assertThrows(Exception.class,
                    () -> completion.toCompletableFuture().get(1, TimeUnit.SECONDS));
        }

        FakeProcess eof = new FakeProcess();
        try (JsonLineRecognitionClient client = client(eof)) {
            client.start();
            ready(eof);
            var completion = client.submit(newGame("eof"));
            eof.readStdin();
            eof.closeStdout();
            assertThrows(Exception.class,
                    () -> completion.toCompletableFuture().get(1, TimeUnit.SECONDS));
        }
    }

    /** 验证 worker READY 契约版本错误时，在任何业务提交前就拒绝启动。 */
    @Test
    void rejectsReadyWithWrongContractBeforeAnyBusinessSubmission() throws Exception {
        FakeProcess process = new FakeProcess();
        try (JsonLineRecognitionClient client = client(process)) {
            var ready = client.start();
            process.writeStdout(mapper.writeValueAsString(new RecognitionReady(
                    "recognition.v0", RecognitionMessageType.READY,
                    List.of(RecognitionTaskType.values()))));
            assertThrows(Exception.class,
                    () -> ready.toCompletableFuture().get(1, TimeUnit.SECONDS));
        }
    }

    /** 验证协议污染后会淘汰旧进程，下一次提交必须等待新一代 READY。 */
    @Test
    void restartsAfterProtocolPollutionAndRequiresFreshReadyBeforeSubmitting() throws Exception {
        FakeProcess first = new FakeProcess();
        FakeProcess second = new FakeProcess();
        List<FakeProcess> processes = List.of(first, second);
        AtomicInteger launches = new AtomicInteger();
        try (JsonLineRecognitionClient client = new JsonLineRecognitionClient(
                mapper, command -> processes.get(launches.getAndIncrement()),
                List.of("python", "worker.py"))) {
            client.start();
            ready(first);
            var failed = client.submit(newGame("first-generation"));
            assertTrue(first.readStdin().contains("\"requestId\":\"first-generation\""));
            first.writeStdout("polluted stdout");
            assertThrows(Exception.class,
                    () -> failed.toCompletableFuture().get(1, TimeUnit.SECONDS));

            var recovered = client.submit(newGame("second-generation"));
            assertEquals(2, launches.get());
            assertEquals(0, second.stdinAvailable());
            ready(second);
            assertTrue(second.readStdin().contains("\"requestId\":\"second-generation\""));
            second.writeStdout(mapper.writeValueAsString(
                    newGameResult("second-generation")));
            assertEquals("second-generation", recovered.toCompletableFuture()
                    .get(1, TimeUnit.SECONDS).requestId());
        }
    }

    /** 验证业务 deadline 仅由 Python 返回，Java 客户端不会抢先取消在途识别任务。 */
    @Test
    void pythonReportedDeadlineCompletesRequestWithoutClientCancellation() throws Exception {
        FakeProcess process = new FakeProcess();
        try (JsonLineRecognitionClient client = client(process)) {
            client.start();
            ready(process);
            var waiting = client.submit(newGame("python-deadline", 100));
            assertTrue(process.readStdin().contains("\"requestId\":\"python-deadline\""));
            Thread.sleep(250);
            assertFalse(waiting.toCompletableFuture().isDone());
            assertEquals(0, process.stdinAvailable());
            process.writeStdout(mapper.writeValueAsString(newGameFailure("python-deadline")));
            RecognitionResult result = waiting.toCompletableFuture().get(1, TimeUnit.SECONDS);
            assertEquals(RecognitionStatus.FAILED, result.status());
            assertEquals("DEADLINE_EXCEEDED", result.errorCode());
            assertEquals(0, process.stdinAvailable());
        }
    }

    /** 验证后续本方回合等待再进入时，传输层不得按观察截止取消任务。 */
    @Test
    void localTurnReentryDoesNotUseObservationDeadlineAsTransportTimeout() throws Exception {
        FakeProcess process = new FakeProcess();
        try (JsonLineRecognitionClient client = client(process)) {
            client.start();
            ready(process);
            var waiting = client.submit(localTurn(
                    "wait-reenter", "require_exit_then_reenter", 100));
            assertTrue(process.readStdin().contains("\"requestId\":\"wait-reenter\""));
            Thread.sleep(250);
            assertFalse(waiting.toCompletableFuture().isDone());
            process.writeStdout(mapper.writeValueAsString(localTurnResult("wait-reenter")));
            assertEquals("wait-reenter", waiting.toCompletableFuture()
                    .get(1, TimeUnit.SECONDS).requestId());
        }
    }

    /** 验证 stdout EOF 和进程退出都会淘汰当前代，并允许下一代重新就绪。 */
    @Test
    void eofAndProcessExitEachRetireTheirGenerationAndRecover() throws Exception {
        FakeProcess eof = new FakeProcess();
        FakeProcess exited = new FakeProcess();
        FakeProcess recovered = new FakeProcess();
        List<FakeProcess> processes = List.of(eof, exited, recovered);
        AtomicInteger launches = new AtomicInteger();
        try (JsonLineRecognitionClient client = new JsonLineRecognitionClient(
                mapper, command -> processes.get(launches.getAndIncrement()),
                List.of("python", "worker.py"))) {
            client.start();
            ready(eof);
            var eofFailure = client.submit(newGame("eof-generation"));
            eof.readStdin();
            eof.closeStdout();
            assertThrows(Exception.class,
                    () -> eofFailure.toCompletableFuture().get(1, TimeUnit.SECONDS));

            var exitFailure = client.submit(newGame("exit-generation"));
            ready(exited);
            exited.readStdin();
            exited.exit(17);
            assertThrows(Exception.class,
                    () -> exitFailure.toCompletableFuture().get(1, TimeUnit.SECONDS));

            var success = client.submit(newGame("recovered-generation"));
            ready(recovered);
            recovered.readStdin();
            recovered.writeStdout(mapper.writeValueAsString(
                    newGameResult("recovered-generation")));
            assertEquals("recovered-generation", success.toCompletableFuture()
                    .get(1, TimeUnit.SECONDS).requestId());
            assertEquals(3, launches.get());
        }
    }

    /** 验证并发提交共享同一 worker 代，乱序返回仍精确关联各自请求。 */
    @Test
    void concurrentSubmitsShareOneGenerationAndStillRouteOutOfOrder() throws Exception {
        FakeProcess process = new FakeProcess();
        AtomicInteger launches = new AtomicInteger();
        ExecutorService submitters = Executors.newFixedThreadPool(4);
        try (JsonLineRecognitionClient client = new JsonLineRecognitionClient(
                mapper, command -> {
                    launches.incrementAndGet();
                    return process;
                }, List.of("python", "worker.py"))) {
            client.start();
            ready(process);
            List<Future<CompletionStage<RecognitionResult>>> calls = new ArrayList<>();
            for (int index = 0; index < 8; index++) {
                String requestId = "concurrent-" + index;
                calls.add(submitters.submit(() -> client.submit(newGame(requestId, 5_000))));
            }
            List<CompletionStage<RecognitionResult>> completionList = new ArrayList<>();
            for (Future<CompletionStage<RecognitionResult>> call : calls) {
                completionList.add(call.get(1, TimeUnit.SECONDS));
            }
            Map<String, CompletionStage<RecognitionResult>> completions =
                    new java.util.HashMap<>();
            List<String> submittedIds = new ArrayList<>();
            for (int index = 0; index < 8; index++) {
                RecognitionSubmit submit = mapper.readValue(
                        process.readStdin(), RecognitionSubmit.class);
                submittedIds.add(submit.requestId());
                completions.put(submit.requestId(), completionList.get(
                        Integer.parseInt(submit.requestId().substring(
                                submit.requestId().lastIndexOf('-') + 1))));
            }
            Collections.reverse(submittedIds);
            for (String requestId : submittedIds) {
                process.writeStdout(mapper.writeValueAsString(newGameResult(requestId)));
            }
            for (String requestId : submittedIds) {
                assertEquals(requestId, completions.get(requestId).toCompletableFuture()
                        .get(1, TimeUnit.SECONDS).requestId());
            }
            assertEquals(1, launches.get());
        } finally {
            submitters.shutdownNow();
        }
    }

    /** 验证客户端永久关闭后释放在途任务，并禁止任何隐式重启。 */
    @Test
    void closePermanentlyPreventsRestart() throws Exception {
        FakeProcess process = new FakeProcess();
        AtomicInteger launches = new AtomicInteger();
        JsonLineRecognitionClient client = new JsonLineRecognitionClient(
                mapper, command -> {
                    launches.incrementAndGet();
                    return process;
                }, List.of("python", "worker.py"));
        client.start();
        ready(process);
        var pending = client.submit(newGame("closed-pending"));
        process.readStdin();
        client.close();

        assertThrows(Exception.class,
                () -> pending.toCompletableFuture().get(1, TimeUnit.SECONDS));
        assertThrows(Exception.class, () -> client.start().toCompletableFuture().join());
        assertThrows(Exception.class,
                () -> client.submit(newGame("after-close")).toCompletableFuture().join());
        assertEquals(1, launches.get());
    }

    private JsonLineRecognitionClient client(FakeProcess process) {
        return new JsonLineRecognitionClient(mapper, command -> process, List.of("python", "worker.py"));
    }

    private void ready(FakeProcess process) throws IOException {
        process.writeStdout(mapper.writeValueAsString(new RecognitionReady(
                RecognitionProtocolAdapter.CONTRACT_VERSION, RecognitionMessageType.READY,
                List.of(RecognitionTaskType.values()))));
    }

    private RecognitionSubmit newGame(String requestId) {
        return newGame(requestId, 1000);
    }

    private RecognitionSubmit newGame(String requestId, long deadlineMs) {
        return new RecognitionSubmit(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.SUBMIT, RecognitionTaskType.NEW_GAME,
                requestId, null, null, deadlineMs, null, null, null, null, null, null);
    }

    private RecognitionSubmit localTurn(String requestId, String turnEntryMode, long deadlineMs) {
        return new RecognitionSubmit(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.SUBMIT, RecognitionTaskType.LOCAL_TURN,
                requestId, "deal-1", 1L, deadlineMs, null, "landlord", List.of(), turnEntryMode,
                null, null);
    }

    private RecognitionResult localTurnResult(String requestId) {
        return new RecognitionResult(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.RESULT, RecognitionTaskType.LOCAL_TURN,
                requestId, "deal-1", 1L, RecognitionStatus.OK,
                Instant.parse("2026-08-26T08:00:00Z"), null, null, List.of("3"), null,
                null, null, Map.of(), null, null, null);
    }

    private RecognitionResult newGameResult(String requestId) {
        return new RecognitionResult(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.RESULT, RecognitionTaskType.NEW_GAME,
                requestId, null, null, RecognitionStatus.OK,
                Instant.parse("2026-08-26T08:00:00Z"), null, null, null, null,
                null, null, null, null, null, null);
    }

    private RecognitionResult newGameFailure(String requestId) {
        return new RecognitionResult(RecognitionProtocolAdapter.CONTRACT_VERSION,
                RecognitionMessageType.RESULT, RecognitionTaskType.NEW_GAME,
                requestId, null, null, RecognitionStatus.FAILED,
                null, null, null, null, null, null, null, null, null,
                "DEADLINE_EXCEEDED", "recognition task exceeded deadlineMs");
    }

    /** 用双向管道模拟 Python 子进程，测试线程可独立控制 stdout、stderr 和 EOF。 */
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

        private int stdinAvailable() throws IOException {
            return stdinReader.available();
        }

        private void writeStdout(String line) throws IOException {
            stdoutWriter.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            stdoutWriter.flush();
        }

        private void writeStderr(String line) throws IOException {
            stderrWriter.write((line + "\n").getBytes(StandardCharsets.UTF_8));
            stderrWriter.flush();
        }

        private void closeStdout() throws IOException {
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
