package com.mkuiwu.douzero.runtime.infrastructure.execution;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.application.model.ExecutionFailure;
import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.CardPlayExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayButtonExecutionPort;
import com.mkuiwu.douzero.runtime.application.port.RecognitionJob;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * execution.v1 协议的 JSONL 客户端，直接实现 {@link CardPlayExecutionPort}。
 *
 * <p>管理一个持久 Python execution worker 进程，stdin 写入通过单锁保持整行原子性，
 * stdout 按 requestId 乱序路由。客户端是 fail-closed 的：任何不确定结果都向上传递，
 * 由 Java 状态机决定是否重新识别。</p>
 */
public final class JsonLineExecutionClient implements CardPlayExecutionPort, PreplayButtonExecutionPort, AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(JsonLineExecutionClient.class);
    private static final String CONTRACT_VERSION = "execution.v1";

    private final ObjectMapper objectMapper;
    private final ProcessLauncher processLauncher;
    private final List<String> command;
    /** PASS 自动点击在 Python Worker 内等待的时长，单位毫秒；普通出牌不会携带该字段。 */
    private final long autoPassDelayMs;
    private final Map<String, PendingRequest<?>> pending = new ConcurrentHashMap<>();
    private final AtomicBoolean closed = new AtomicBoolean();
    private volatile Worker worker;

    public JsonLineExecutionClient(
            ObjectMapper objectMapper,
            ProcessLauncher processLauncher,
            List<String> command
    ) {
        this(objectMapper, processLauncher, command, 0);
    }

    public JsonLineExecutionClient(
            ObjectMapper objectMapper,
            ProcessLauncher processLauncher,
            List<String> command,
            long autoPassDelayMs
    ) {
        this.objectMapper = Objects.requireNonNull(objectMapper, "执行 JSON 序列化器不能为空");
        this.processLauncher = Objects.requireNonNull(processLauncher, "执行进程启动器不能为空");
        this.command = List.copyOf(Objects.requireNonNull(command, "执行进程命令不能为空"));
        if (this.command.isEmpty() || this.command.stream().anyMatch(v -> v == null || v.isBlank())) {
            throw new IllegalArgumentException("执行进程命令必须包含非空参数");
        }
        if (autoPassDelayMs < 0) {
            throw new IllegalArgumentException("自动不出等待时间不能为负数");
        }
        this.autoPassDelayMs = autoPassDelayMs;
    }

    @Override
    public RecognitionJob<CardPlayExecutionPort.ExecutionResult> execute(
            CardPlayExecutionPort.ExecutionRequest request
    ) {
        Objects.requireNonNull(request, "执行请求不能为空");
        Worker active = ensureWorker();
        String requestId = request.identity().requestId();
        CompletableFuture<CardPlayExecutionPort.ExecutionResult> completion = new CompletableFuture<>();
        PendingRequest<CardPlayExecutionPort.ExecutionResult> route = new PendingRequest<>(
                requestId, request.identity().dealId(), request.identity().generation(),
                "CARD_PLAY", active, completion
        );
        if (pending.putIfAbsent(requestId, route) != null) {
            throw new IllegalArgumentException("执行 requestId 已经在途: " + requestId);
        }
        active.ready.whenComplete((ignored, failure) -> {
            if (failure != null) {
                failRoute(requestId, route, transportFailure("执行 worker READY 失败", failure));
                return;
            }
            try {
                Map<String, Object> message = buildExecuteMessage(request);
                writeLine(active, message);
            } catch (RuntimeException error) {
                failRoute(requestId, route, error);
            }
        });
        return new ExecutionJob<>(requestId, completion, () -> cancelRequest(requestId));
    }

    @Override
    public RecognitionJob<PreplayButtonExecutionPort.ExecutionResult> execute(
            PreplayButtonExecutionPort.ExecutionRequest request
    ) {
        Objects.requireNonNull(request, "局前执行请求不能为空");
        Worker active = ensureWorker();
        String requestId = request.identity().requestId();
        CompletableFuture<PreplayButtonExecutionPort.ExecutionResult> completion =
                new CompletableFuture<>();
        PendingRequest<PreplayButtonExecutionPort.ExecutionResult> route = new PendingRequest<>(
                requestId, request.identity().dealId(), request.identity().generation(),
                "PREPLAY_BUTTON", active, completion
        );
        if (pending.putIfAbsent(requestId, route) != null) {
            throw new IllegalArgumentException("执行 requestId 已经在途: " + requestId);
        }
        active.ready.whenComplete((ignored, failure) -> {
            if (failure != null) {
                failRoute(requestId, route, transportFailure("执行 worker READY 失败", failure));
                return;
            }
            try {
                writeLine(active, buildPreplayExecuteMessage(request));
            } catch (RuntimeException error) {
                failRoute(requestId, route, error);
            }
        });
        return new ExecutionJob<>(requestId, completion, () -> cancelRequest(requestId));
    }

    @Override
    public synchronized void close() {
        if (!closed.compareAndSet(false, true)) {
            return;
        }
        ExecutionTransportException failure = new ExecutionTransportException("执行客户端已经关闭");
        Worker current = worker;
        worker = null;
        if (current != null) {
            retire(current, failure);
        }
        pending.forEach((id, route) -> route.completion().completeExceptionally(failure));
        pending.clear();
    }

    // ===== 协议消息构造 =====

    private Map<String, Object> buildExecuteMessage(CardPlayExecutionPort.ExecutionRequest request) {
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("contractVersion", CONTRACT_VERSION);
        message.put("messageType", "SUBMIT");
        message.put("taskType", "CARD_PLAY");
        message.put("requestId", request.identity().requestId());
        message.put("dealId", request.identity().dealId());
        message.put("generation", request.identity().generation());
        message.put("deadlineMs", request.deadlineMs());
        message.put("localSeat", request.localSeat().name().toLowerCase());
        message.put("authoritativeHand", cardSetToWire(request.authoritativeHand()));
        message.put("actionType", request.actionType().name());
        message.put("autoSubmit", request.autoSubmit());
        if (request.actionType() == CardPlayExecutionPort.ActionType.PASS
                && request.autoSubmit() && autoPassDelayMs > 0) {
            message.put("clickDelayMs", autoPassDelayMs);
        }
        if (request.actionType() == CardPlayExecutionPort.ActionType.PLAY) {
            message.put("recommendedCards", cardSetToWire(request.recommendedCards()));
        }
        return message;
    }

    private Map<String, Object> buildPreplayExecuteMessage(
            PreplayButtonExecutionPort.ExecutionRequest request
    ) {
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("contractVersion", CONTRACT_VERSION);
        message.put("messageType", "SUBMIT");
        message.put("taskType", "PREPLAY_BUTTON");
        message.put("requestId", request.identity().requestId());
        message.put("dealId", request.identity().dealId());
        message.put("generation", request.identity().generation());
        message.put("deadlineMs", request.deadlineMs());
        message.put("stage", switch (request.stage()) {
            case CALL_LANDLORD -> "call";
            case ROB_LANDLORD -> "rob";
            case DOUBLE -> "double";
        });
        message.put("action", request.action().name().toLowerCase());
        return message;
    }

    private List<String> cardSetToWire(CardSet cardSet) {
        List<String> result = new ArrayList<>(cardSet.size());
        for (Card card : cardSet.cards()) {
            result.add(card.rank().symbol());
        }
        return result;
    }

    // ===== 进程管理 =====

    private synchronized Worker ensureWorker() {
        if (closed.get()) {
            throw new ExecutionTransportException("执行客户端已经关闭");
        }
        Worker current = worker;
        if (isHealthy(current)) {
            return current;
        }
        if (current != null) {
            retire(current, current.failure.get() == null
                    ? new ExecutionTransportException("旧执行 worker 已失效")
                    : current.failure.get());
        }
        try {
            Process launched = processLauncher.launch(command);
            launched = Objects.requireNonNull(launched, "进程启动器不能返回 null");
            Worker started = new Worker(launched, new BufferedWriter(new OutputStreamWriter(
                    launched.getOutputStream(), StandardCharsets.UTF_8)));
            worker = started;
            startDaemon("douzero-exec-stdout", () -> pumpStdout(started));
            startDaemon("douzero-exec-stderr", () -> pumpStderr(started));
            startDaemon("douzero-exec-exit", () -> awaitExit(started));
            // 同步等待 READY（简化版，避免引入更多异步路由）。
            try {
                started.ready.toCompletableFuture().get(5, java.util.concurrent.TimeUnit.SECONDS);
            } catch (Exception error) {
                retire(started, transportFailure("执行 worker READY 超时", error));
                throw started.failure.get() != null ? started.failure.get()
                        : new ExecutionTransportException("执行 worker READY 超时", error);
            }
            return started;
        } catch (IOException | RuntimeException error) {
            throw new ExecutionTransportException("无法启动 Python 执行进程", error);
        }
    }

    private void writeLine(Worker destination, Object message) {
        String line;
        try {
            line = objectMapper.writeValueAsString(message);
        } catch (JsonProcessingException error) {
            throw new ExecutionTransportException("无法编码执行协议消息", error);
        }
        synchronized (destination.writeLock) {
            try {
                if (closed.get() || !isHealthy(destination) || worker != destination) {
                    throw new IOException("执行进程 stdin 不可用");
                }
                destination.writer.write(line);
                destination.writer.newLine();
                destination.writer.flush();
            } catch (IOException error) {
                ExecutionTransportException failure = new ExecutionTransportException(
                        "写入执行进程失败", error);
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
                retire(source, new ExecutionTransportException("执行进程 stdout 提前结束"));
            }
        } catch (IOException error) {
            if (!closed.get()) {
                retire(source, new ExecutionTransportException("读取执行进程 stdout 失败", error));
            }
        }
    }

    private void acceptProtocolLine(Worker source, String line) {
        try {
            JsonNode root = objectMapper.readTree(line);
            if (root == null || !root.isObject() || !root.hasNonNull("messageType")) {
                throw new IllegalArgumentException("stdout 行缺少 messageType");
            }
            String type = root.get("messageType").asText();
            if ("READY".equals(type)) {
                String version = root.path("contractVersion").asText();
                if (!CONTRACT_VERSION.equals(version)) {
                    throw new IllegalArgumentException("worker READY 协议版本不匹配: " + version);
                }
                if (source.ready.isDone()) {
                    throw new IllegalArgumentException("worker 重复发送 READY");
                }
                LOGGER.info("Python 执行 Worker 已就绪 contractVersion={}", version);
                source.ready.complete(null);
                return;
            }
            if (!"RESULT".equals(type)) {
                throw new IllegalArgumentException("stdout 出现非结果消息: " + type);
            }
            routeResult(source, root);
        } catch (Exception error) {
            retire(source, new ExecutionTransportException("执行进程 stdout 协议污染", error));
        }
    }

    private void routeResult(Worker source, JsonNode root) {
        String requestId = root.path("requestId").asText();
        PendingRequest<?> route = pending.get(requestId);
        if (route == null || route.worker() != source || !pending.remove(requestId, route)) {
            LOGGER.warn("忽略无在途任务的执行结果 requestId={}", requestId);
            return;
        }
        String status = root.path("status").asText();
        try {
            if ("OK".equals(status)) {
                boolean autoSubmitEcho = root.path("autoSubmitEcho").asBoolean(false);
                Instant verifiedAt = Instant.parse(root.path("verifiedAt").asText());
                if ("CARD_PLAY".equals(route.taskType())) {
                    @SuppressWarnings("unchecked")
                    PendingRequest<CardPlayExecutionPort.ExecutionResult> cardRoute =
                            (PendingRequest<CardPlayExecutionPort.ExecutionResult>) route;
                    cardRoute.completion().complete(new CardPlayExecutionPort.Executed(
                            route.identity(), autoSubmitEcho, verifiedAt));
                } else {
                    @SuppressWarnings("unchecked")
                    PendingRequest<PreplayButtonExecutionPort.ExecutionResult> preplayRoute =
                            (PendingRequest<PreplayButtonExecutionPort.ExecutionResult>) route;
                    preplayRoute.completion().complete(new PreplayButtonExecutionPort.Executed(
                            route.identity(), verifiedAt));
                }
            } else if ("FAILED".equals(status)) {
                String errorCode = root.path("errorCode").asText();
                String errorMessage = root.path("errorMessage").asText();
                ExecutionFailure failure = parseFailure(errorCode);
                if ("CARD_PLAY".equals(route.taskType())) {
                    @SuppressWarnings("unchecked")
                    PendingRequest<CardPlayExecutionPort.ExecutionResult> cardRoute =
                            (PendingRequest<CardPlayExecutionPort.ExecutionResult>) route;
                    cardRoute.completion().complete(new CardPlayExecutionPort.ExecutionRejected(
                            route.identity(), failure, errorMessage));
                } else {
                    @SuppressWarnings("unchecked")
                    PendingRequest<PreplayButtonExecutionPort.ExecutionResult> preplayRoute =
                            (PendingRequest<PreplayButtonExecutionPort.ExecutionResult>) route;
                    preplayRoute.completion().complete(new PreplayButtonExecutionPort.ExecutionRejected(
                            route.identity(), failure, errorMessage));
                }
            } else if ("UNCERTAIN".equals(status)) {
                String detail = root.path("detail").asText();
                if ("CARD_PLAY".equals(route.taskType())) {
                    @SuppressWarnings("unchecked")
                    PendingRequest<CardPlayExecutionPort.ExecutionResult> cardRoute =
                            (PendingRequest<CardPlayExecutionPort.ExecutionResult>) route;
                    cardRoute.completion().complete(new CardPlayExecutionPort.ExecutionUncertain(
                            route.identity(), detail));
                } else {
                    @SuppressWarnings("unchecked")
                    PendingRequest<PreplayButtonExecutionPort.ExecutionResult> preplayRoute =
                            (PendingRequest<PreplayButtonExecutionPort.ExecutionResult>) route;
                    preplayRoute.completion().complete(new PreplayButtonExecutionPort.ExecutionUncertain(
                            route.identity(), detail));
                }
            } else {
                throw new IllegalArgumentException("未知执行结果状态: " + status);
            }
        } catch (Exception error) {
            route.completion().completeExceptionally(new ExecutionTransportException(
                    "解析执行结果失败", error));
        }
    }

    private ExecutionFailure parseFailure(String errorCode) {
        try {
            return ExecutionFailure.valueOf(errorCode);
        } catch (IllegalArgumentException error) {
            return ExecutionFailure.INTERNAL_ERROR;
        }
    }

    private void cancelRequest(String requestId) {
        PendingRequest<?> route = pending.remove(requestId);
        if (route != null) {
            route.completion().completeExceptionally(new java.util.concurrent.CancellationException(
                    "执行任务已取消: " + requestId));
        }
        // 向 Python 发送 CANCEL（如果 worker 还活着）
        Worker active = worker;
        if (active != null && isHealthy(active) && active.ready.isDone()) {
            try {
                Map<String, Object> cancel = new LinkedHashMap<>();
                cancel.put("contractVersion", CONTRACT_VERSION);
                cancel.put("messageType", "CANCEL");
                cancel.put("taskType", route != null ? route.taskType() : "CARD_PLAY");
                cancel.put("requestId", requestId);
                writeLine(active, cancel);
            } catch (RuntimeException ignored) {
                // CANCEL 发送失败不影响本地取消。
            }
        }
    }

    private void pumpStderr(Worker source) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                source.process.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                LOGGER.debug("Python execution stderr: {}", line);
            }
        } catch (IOException error) {
            if (!closed.get()) {
                LOGGER.warn("读取 Python execution stderr 失败", error);
            }
        }
    }

    private void awaitExit(Worker source) {
        try {
            int exitCode = source.process.waitFor();
            if (!closed.get()) {
                retire(source, new ExecutionTransportException(
                        "Python 执行进程提前退出，exitCode=" + exitCode));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, new ExecutionTransportException("等待 Python 执行进程退出时被中断", error));
            }
        }
    }

    private synchronized void retire(Worker retired, ExecutionTransportException failure) {
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
        return candidate != null && candidate.failure.get() == null && candidate.process.isAlive();
    }

    private void failRoute(String requestId, PendingRequest<?> route, Throwable failure) {
        if (pending.remove(requestId, route)) {
            route.completion().completeExceptionally(failure);
        }
    }

    private ExecutionTransportException transportFailure(String message, Throwable failure) {
        Throwable cause = failure instanceof java.util.concurrent.CompletionException
                && failure.getCause() != null ? failure.getCause() : failure;
        return cause instanceof ExecutionTransportException transport
                ? transport : new ExecutionTransportException(message, cause);
    }

    private void startDaemon(String name, Runnable task) {
        Thread thread = new Thread(task, name);
        thread.setDaemon(true);
        thread.start();
    }

    /** 一个在途请求的预期身份和完成句柄。 */
    private record PendingRequest<R>(
            String requestId,
            String dealId,
            Long generation,
            String taskType,
            Worker worker,
            CompletableFuture<R> completion
    ) {
        GameTaskIdentity identity() {
            return new GameTaskIdentity(requestId, dealId, generation);
        }

    }

    /** 单个执行进程代次拥有的 READY、IO、写锁和首个失败原因。 */
    private static final class Worker {
        private final Process process;
        private final BufferedWriter writer;
        private final Object writeLock = new Object();
        private final CompletableFuture<Void> ready = new CompletableFuture<>();
        private final AtomicReference<ExecutionTransportException> failure =
                new AtomicReference<>();

        private Worker(Process process, BufferedWriter writer) {
            this.process = process;
            this.writer = writer;
        }
    }

    /** 把 CompletableFuture 包装成 RecognitionJob 供编排器使用。 */
    private record ExecutionJob<R>(
            String requestId,
            CompletableFuture<R> future,
            Runnable cancelAction
    ) implements RecognitionJob<R> {
        @Override
        public CompletionStage<R> completion() {
            return future;
        }

        @Override
        public void cancel() {
            cancelAction.run();
        }
    }
}
