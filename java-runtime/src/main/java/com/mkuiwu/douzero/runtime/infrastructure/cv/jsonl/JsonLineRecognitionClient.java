package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionCancel;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionMessageType;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionReady;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionResult;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 一个异步持久 Python CV 进程的 JSONL 客户端。
 *
 * <p>stdin 写入通过单锁保持整行原子性，stdout 只允许协议消息，stderr 单独泵到日志。
 * 多个任务可以并发在途，结果只能按 requestId 路由，绝不依赖返回顺序。EOF、协议污染、
 * 写入失败或进程退出只淘汰当前 worker 代次；下一次提交会启动新进程并重新校验 READY。</p>
 */
public final class JsonLineRecognitionClient implements AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(JsonLineRecognitionClient.class);

    /** 跨进程 JSON 序列化器。 */
    private final ObjectMapper objectMapper;

    /** 可替换的进程启动器。 */
    private final ProcessLauncher processLauncher;

    /** 不经 shell 解析的完整启动命令及参数。 */
    private final List<String> command;

    /** requestId 到最终结果 future 的乱序路由表。 */
    private final Map<String, PendingRequest> pending = new ConcurrentHashMap<>();

    /** 客户端是否已经永久关闭。 */
    private final AtomicBoolean closed = new AtomicBoolean();

    /** 当前可用或正在启动的 worker 代次；生命周期变更由同步方法保护。 */
    private volatile Worker worker;

    public JsonLineRecognitionClient(ObjectMapper objectMapper, ProcessLauncher processLauncher,
                                     List<String> command) {
        this.objectMapper = Objects.requireNonNull(objectMapper, "识别 JSON 序列化器不能为空");
        this.processLauncher = Objects.requireNonNull(processLauncher, "识别进程启动器不能为空");
        this.command = List.copyOf(Objects.requireNonNull(command, "识别进程命令不能为空"));
        if (this.command.isEmpty() || this.command.stream().anyMatch(value -> value == null
                || value.isBlank())) {
            throw new IllegalArgumentException("识别进程命令必须包含非空参数");
        }
    }

    /** 保证存在当前 worker；失效代次会被淘汰，新代次必须重新发送 READY。 */
    public synchronized CompletionStage<RecognitionReady> start() {
        if (closed.get()) {
            return CompletableFuture.failedFuture(
                    new RecognitionTransportException("识别客户端已经关闭"));
        }
        Worker current = worker;
        if (isHealthy(current)) {
            return current.ready;
        }
        if (current != null) {
            retire(current, current.failure.get() == null
                    ? new RecognitionTransportException("旧识别 worker 已失效")
                    : current.failure.get());
        }
        try {
            Process launched = processLauncher.launch(command);
            launched = Objects.requireNonNull(launched, "进程启动器不能返回 null");
            Worker started = new Worker(launched, new BufferedWriter(new OutputStreamWriter(
                    launched.getOutputStream(), StandardCharsets.UTF_8)));
            worker = started;
            startDaemon("douzero-cv-stdout", () -> pumpStdout(started));
            startDaemon("douzero-cv-stderr", () -> pumpStderr(started));
            startDaemon("douzero-cv-exit", () -> awaitExit(started));
            return started.ready;
        } catch (IOException | RuntimeException error) {
            return CompletableFuture.failedFuture(
                    new RecognitionTransportException("无法启动 Python CV 进程", error));
        }
    }

    /** 提交业务任务并返回按 requestId 路由的最终结果。 */
    public CompletionStage<RecognitionResult> submit(RecognitionSubmit request) {
        Objects.requireNonNull(request, "识别提交不能为空");
        Worker active;
        try {
            start();
            active = requireCurrentWorker();
        } catch (RuntimeException error) {
            return CompletableFuture.failedFuture(error);
        }
        CompletableFuture<RecognitionResult> completion = new CompletableFuture<>();
        PendingRequest route = new PendingRequest(request.taskType(), request.dealId(),
                request.generation(), active, completion);
        if (pending.putIfAbsent(request.requestId(), route) != null) {
            throw new IllegalArgumentException("识别 requestId 已经在途: " + request.requestId());
        }
        active.ready.whenComplete((ignored, failure) -> {
            if (failure != null) {
                failRoute(request.requestId(), route, transportFailure(
                        "识别 worker READY 失败", failure));
                return;
            }
            if (pending.get(request.requestId()) != route) {
                return;
            }
            if (!isHealthy(active) || worker != active) {
                failRoute(request.requestId(), route,
                        active.failure.get() == null
                                ? new RecognitionTransportException("识别 worker 在 READY 后失效")
                                : active.failure.get());
                return;
            }
            try {
                writeLine(active, request);
            } catch (RuntimeException error) {
                failRoute(request.requestId(), route, error);
            }
        });
        return completion;
    }

    /** 取消一个在途任务；重复调用只写一次 CANCEL，且本地 future 立即取消。 */
    public void cancel(RecognitionCancel request) {
        Objects.requireNonNull(request, "识别取消不能为空");
        PendingRequest route = pending.get(request.requestId());
        if (route == null) {
            return;
        }
        if (!pending.remove(request.requestId(), route)) {
            return;
        }
        route.completion().completeExceptionally(
                new CancellationException("识别任务已取消: " + request.requestId()));
        if (!closed.get() && isHealthy(route.worker()) && route.worker().ready.isDone()
                && !route.worker().ready.isCompletedExceptionally()) {
            writeLine(route.worker(), request);
        }
    }

    /** 返回 worker 的就绪声明，供启动编排校验能力清单。 */
    public CompletionStage<RecognitionReady> readiness() {
        return start();
    }

    @Override
    public synchronized void close() {
        if (!closed.compareAndSet(false, true)) {
            return;
        }
        RecognitionTransportException failure = new RecognitionTransportException(
                "识别客户端已经关闭");
        Worker current = worker;
        worker = null;
        if (current != null) {
            retire(current, failure);
        }
        pending.forEach((requestId, route) -> route.completion().completeExceptionally(failure));
        pending.clear();
    }

    private Worker requireCurrentWorker() {
        if (closed.get()) {
            throw new RecognitionTransportException("识别客户端已经关闭");
        }
        Worker current = worker;
        if (!isHealthy(current)) {
            throw current == null || current.failure.get() == null
                    ? new RecognitionTransportException("识别 worker 当前不可用")
                    : current.failure.get();
        }
        return current;
    }

    private void writeLine(Worker destination, Object message) {
        String line;
        try {
            line = objectMapper.writeValueAsString(message);
        } catch (JsonProcessingException error) {
            throw new RecognitionTransportException("无法编码识别协议消息", error);
        }
        synchronized (destination.writeLock) {
            try {
                if (closed.get() || !isHealthy(destination) || worker != destination) {
                    throw new IOException("识别进程 stdin 不可用");
                }
                destination.writer.write(line);
                destination.writer.newLine();
                destination.writer.flush();
                logOutbound(message);
            } catch (IOException error) {
                RecognitionTransportException failure = new RecognitionTransportException(
                        "写入识别进程失败", error);
                retire(destination, failure);
                throw failure;
            }
        }
    }

    private void pumpStdout(Worker source) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                source.process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                acceptProtocolLine(source, line);
            }
            if (!closed.get()) {
                retire(source, new RecognitionTransportException("识别进程 stdout 提前结束"));
            }
        } catch (IOException error) {
            if (!closed.get()) {
                retire(source, new RecognitionTransportException(
                        "读取识别进程 stdout 失败", error));
            }
        }
    }

    private void acceptProtocolLine(Worker source, String line) {
        try {
            JsonNode root = objectMapper.readTree(line);
            if (root == null || !root.isObject() || !root.hasNonNull("messageType")) {
                throw new IllegalArgumentException("stdout 行缺少 messageType");
            }
            RecognitionMessageType type = RecognitionMessageType.valueOf(
                    root.get("messageType").asText());
            if (type == RecognitionMessageType.READY) {
                RecognitionReady announcement = objectMapper.treeToValue(root, RecognitionReady.class);
                if (!RecognitionProtocolAdapter.CONTRACT_VERSION.equals(
                        announcement.contractVersion())
                        || !java.util.EnumSet.copyOf(announcement.taskTypes()).equals(
                        java.util.EnumSet.allOf(
                                com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType.class))) {
                    throw new IllegalArgumentException("worker READY 协议或能力清单不匹配");
                }
                if (source.ready.isDone()) {
                    throw new IllegalArgumentException("worker 重复发送 READY");
                }
                LOGGER.info("Python CV 已就绪 contractVersion={} taskTypes={}",
                        announcement.contractVersion(), announcement.taskTypes());
                LOGGER.debug("Python CV READY 明细 ready={}", announcement);
                if (!source.ready.complete(announcement)) {
                    throw new IllegalArgumentException("worker READY 状态并发失效");
                }
                return;
            }
            if (type != RecognitionMessageType.RESULT) {
                throw new IllegalArgumentException("stdout 出现非结果消息: " + type);
            }
            route(source, objectMapper.treeToValue(root, RecognitionResult.class));
        } catch (Exception error) {
            retire(source, new RecognitionTransportException("识别进程 stdout 协议污染", error));
        }
    }

    private void route(Worker source, RecognitionResult result) {
        PendingRequest route = pending.get(result.requestId());
        if (route == null || route.worker() != source
                || !pending.remove(result.requestId(), route)) {
            LOGGER.warn("忽略无在途任务的 Python CV 识别结果 taskType={} requestId={} "
                            + "dealId={} generation={} status={}",
                    result.taskType(), result.requestId(), result.dealId(),
                    result.generation(), result.status());
            LOGGER.debug("被忽略的 Python CV 识别响应明细 response={}", result);
            return;
        }
        if (route.taskType() != result.taskType()
                || !Objects.equals(route.dealId(), result.dealId())
                || !Objects.equals(route.generation(), result.generation())) {
            LOGGER.warn("Python CV 识别接口返回身份不匹配 expectedTaskType={} "
                            + "actualTaskType={} requestId={} expectedDealId={} actualDealId={} "
                            + "expectedGeneration={} actualGeneration={}",
                    route.taskType(), result.taskType(), result.requestId(), route.dealId(),
                    result.dealId(), route.generation(), result.generation());
            LOGGER.debug("身份不匹配的 Python CV 识别响应明细 response={}", result);
            route.completion().completeExceptionally(new RecognitionTransportException(
                    "识别结果任务身份不匹配: " + result.requestId()));
            return;
        }
        LOGGER.info("Python CV 识别接口返回 taskType={} requestId={} dealId={} "
                        + "generation={} status={} promptType={} handCount={} currentHandCount={} "
                        + "bottomCards={} localSeat={} landlordOpeningPlay={} actionsBySeat={} "
                        + "availableActions={} errorCode={}",
                result.taskType(), result.requestId(), result.dealId(), result.generation(),
                result.status(), result.promptType(), sizeOf(result.hand()),
                sizeOf(result.currentHand()), result.bottomCards(), result.localSeat(),
                result.landlordOpeningPlay(), result.actionsBySeat(), result.availableActions(),
                result.errorCode());
        LOGGER.debug("Python CV 识别响应明细 response={}", result);
        route.completion().complete(result);
    }

    private int sizeOf(List<?> values) {
        return values == null ? 0 : values.size();
    }

    /** 按 INFO 摘要、DEBUG 明细记录已经成功写入 Python CV 的协议消息。 */
    private void logOutbound(Object message) {
        if (message instanceof RecognitionSubmit request) {
            LOGGER.info("调用 Python CV 识别接口 taskType={} requestId={} dealId={} "
                            + "generation={} deadlineMs={}",
                    request.taskType(), request.requestId(), request.dealId(),
                    request.generation(), request.deadlineMs());
            LOGGER.debug("Python CV 识别请求明细 request={}", request);
        } else if (message instanceof RecognitionCancel cancel) {
            LOGGER.info("取消 Python CV 识别请求 taskType={} requestId={} dealId={} generation={}",
                    cancel.taskType(), cancel.requestId(), cancel.dealId(), cancel.generation());
            LOGGER.debug("Python CV 识别取消明细 cancel={}", cancel);
        }
    }

    private void pumpStderr(Worker source) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                source.process.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                LOGGER.debug("Python CV stderr: {}", line);
            }
        } catch (IOException error) {
            if (!closed.get()) {
                LOGGER.warn("读取 Python CV stderr 失败", error);
            }
        }
    }

    private void awaitExit(Worker source) {
        try {
            int exitCode = source.process.waitFor();
            if (!closed.get()) {
                retire(source, new RecognitionTransportException(
                        "Python CV 进程提前退出，exitCode=" + exitCode));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, new RecognitionTransportException(
                        "等待 Python CV 退出时被中断", error));
            }
        }
    }

    private synchronized void retire(Worker retired, RecognitionTransportException failure) {
        retired.failure.compareAndSet(null, failure);
        retired.ready.completeExceptionally(retired.failure.get());
        if (worker == retired) {
            worker = null;
        }
        pending.forEach((requestId, route) -> {
            if (route.worker() == retired && pending.remove(requestId, route)) {
                route.completion().completeExceptionally(retired.failure.get());
            }
        });
        try {
            retired.writer.close();
        } catch (IOException ignored) {
            // worker 已失效，关闭 stdin 只用于解除底层阻塞。
        }
        if (retired.process.isAlive()) {
            retired.process.destroy();
        }
    }

    private boolean isHealthy(Worker candidate) {
        return candidate != null && candidate.failure.get() == null
                && candidate.process.isAlive();
    }

    private void failRoute(String requestId, PendingRequest route, Throwable failure) {
        if (pending.remove(requestId, route)) {
            route.completion().completeExceptionally(failure);
        }
    }

    private RecognitionTransportException transportFailure(String message, Throwable failure) {
        Throwable cause = failure instanceof java.util.concurrent.CompletionException
                && failure.getCause() != null ? failure.getCause() : failure;
        return cause instanceof RecognitionTransportException transport
                ? transport : new RecognitionTransportException(message, cause);
    }

    private void startDaemon(String name, Runnable task) {
        Thread thread = new Thread(task, name);
        thread.setDaemon(true);
        thread.start();
    }

    /** 一个在途请求的预期身份和完成句柄。 */
    private record PendingRequest(
            com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType taskType,
            String dealId,
            Long generation,
            Worker worker,
            CompletableFuture<RecognitionResult> completion
    ) { }

    /** 单个识别进程代次拥有的 READY、IO、写锁和首个失败原因。 */
    private static final class Worker {
        /** 当前代次 Python 进程。 */
        private final Process process;

        /** 当前代次 stdin 写入器。 */
        private final BufferedWriter writer;

        /** 当前代次整行写入锁，不得跨 worker 复用。 */
        private final Object writeLock = new Object();

        /** 当前代次通过校验的 READY 声明。 */
        private final CompletableFuture<RecognitionReady> ready = new CompletableFuture<>();

        /** 当前代次的首个传输失败；非空即不可继续使用。 */
        private final AtomicReference<RecognitionTransportException> failure =
                new AtomicReference<>();

        private Worker(Process process, BufferedWriter writer) {
            this.process = process;
            this.writer = writer;
        }
    }
}
