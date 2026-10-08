package com.mkuiwu.douzero.runtime.infrastructure.model.resnet2;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Request;
import com.mkuiwu.douzero.runtime.contract.model.resnet2.ResNet2Response;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransport;
import com.mkuiwu.douzero.runtime.infrastructure.model.ModelTransportException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.locks.ReentrantLock;

/**
 * ResNet2 的持久 JSONL 传输；每个 worker 代次独享 IO 队列并严格串行执行请求。
 *
 * <p>worker 必须先在 stdout 输出一行 inference.v1 READY，之后每个请求只输出一行
 * {@link ResNet2Response}。stderr 仅作为诊断日志读取。超时、EOF、身份错配或任何 stdout
 * 污染都会销毁当前代次，下一请求重新启动并校验 READY；close 后永久禁止重启。</p>
 */
public final class JsonLineResNet2ModelTransport
        implements ModelTransport<ResNet2Request, ResNet2Response>, AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(
            JsonLineResNet2ModelTransport.class);

    /** 跨进程 JSON 序列化器。 */
    private final ObjectMapper objectMapper;

    /** 可由测试替换的无 shell 进程启动边界。 */
    private final ProcessLauncher launcher;

    /** 不经 shell 解析的 Python worker 命令和参数。 */
    private final List<String> command;

    /** 单次启动或推理允许占用的最长时间，单位为毫秒。 */
    private final long timeoutMs;

    /** 保证 stdin 写入与对应 stdout 读取不会被并发请求交叉。 */
    private final ReentrantLock exchangeLock = new ReentrantLock();

    /** 客户端是否已经进入不可逆关闭边界。 */
    private final AtomicBoolean closed = new AtomicBoolean();

    /** 当前可用或正在启动的 worker 代次。 */
    private volatile Worker worker;

    public JsonLineResNet2ModelTransport(
            ObjectMapper objectMapper,
            ProcessLauncher launcher,
            List<String> command,
            long timeoutMs
    ) {
        this.objectMapper = Objects.requireNonNull(objectMapper, "模型 JSON 序列化器不能为空");
        this.launcher = Objects.requireNonNull(launcher, "模型进程启动器不能为空");
        this.command = List.copyOf(Objects.requireNonNull(command, "模型 worker 命令不能为空"));
        if (this.command.isEmpty() || this.command.stream().anyMatch(
                value -> value == null || value.isBlank())) {
            throw new IllegalArgumentException("模型 worker 命令必须包含非空参数");
        }
        if (timeoutMs <= 0) {
            throw new IllegalArgumentException("模型超时必须为正数");
        }
        this.timeoutMs = timeoutMs;
    }

    /** 启动 worker 并在配置超时内完成 READY 握手；已就绪时保持现有代次。 */
    public void start() {
        start(timeoutMs);
    }

    private synchronized void start(long startupTimeoutMs) {
        requireOpen();
        Worker current = worker;
        if (isHealthy(current)) {
            return;
        }
        if (current != null) {
            retire(current, current.failure.get() == null
                    ? unavailable("旧 ResNet2 worker 已失效", null)
                    : current.failure.get());
        }

        Worker started;
        try {
            Process process = Objects.requireNonNull(
                    launcher.launch(command), "进程启动器不能返回 null");
            started = new Worker(process, new BufferedWriter(new OutputStreamWriter(
                    process.getOutputStream(), StandardCharsets.UTF_8)));
            worker = started;
            startDaemon("douzero-resnet2-stdout", () -> pumpStdout(started));
            startDaemon("douzero-resnet2-stderr", () -> pumpStderr(started));
            startDaemon("douzero-resnet2-exit", () -> awaitExit(started));
        } catch (IOException | RuntimeException error) {
            throw unavailable("无法启动 Python ResNet2 worker", error);
        }

        try {
            consumeReady(started, startupTimeoutMs);
            if (!isHealthy(started) || worker != started) {
                throw started.failure.get() == null
                        ? unavailable("ResNet2 worker 在 READY 后立即失效", null)
                        : started.failure.get();
            }
        } catch (RuntimeException error) {
            ModelTransportException transport = error instanceof ModelTransportException value
                    ? value : unavailable("ResNet2 READY 协议污染", error);
            retire(started, transport);
            throw transport;
        }
    }

    /**
     * 发送扁平 ResNet2Request 并在请求 deadline 与配置超时的较小值内读取唯一响应。
     */
    @Override
    public ResNet2Response exchange(ResNet2Request request) {
        Objects.requireNonNull(request, "ResNet2 请求不能为空");
        exchangeLock.lock();
        try {
            requireOpen();
            long effectiveTimeoutMs = Math.min(timeoutMs, request.deadlineMs());
            long startedAt = System.nanoTime();
            start(effectiveTimeoutMs);
            Worker active = requireHealthyWorker();
            long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt);
            long remainingMs = effectiveTimeoutMs - elapsedMs;
            if (remainingMs <= 0) {
                ModelTransportException timeout = timeout(
                        "恢复 ResNet2 worker 后已超过请求截止时间");
                retire(active, timeout);
                throw timeout;
            }
            writeRequest(active, request);
            String line = awaitResponse(active, remainingMs);
            ResNet2Response response = decodeAndValidate(active, request, line);
            LOGGER.info("Python 正式出牌模型接口返回 requestId={} dealId={} modelId={} "
                            + "status={} action={} latencyMs={} errorCode={}",
                    response.requestId(), response.dealId(), response.modelId(),
                    response.status(), response.action(), response.latencyMs(),
                    response.errorCode());
            LOGGER.debug("Python 正式出牌模型响应明细 response={}", response);
            return response;
        } catch (RuntimeException error) {
            LOGGER.warn("Python 正式出牌模型接口调用失败 requestId={} dealId={} modelId={} "
                            + "reason={}",
                    request.requestId(), request.dealId(), request.modelId(), error.getMessage());
            LOGGER.debug("Python 正式出牌模型接口异常明细 requestId={}",
                    request.requestId(), error);
            throw error;
        } finally {
            exchangeLock.unlock();
        }
    }

    /** 永久关闭当前 worker，并通过 EOF 队列项唤醒正在等待响应的调用。 */
    @Override
    public synchronized void close() {
        if (!closed.compareAndSet(false, true)) {
            return;
        }
        Worker current = worker;
        worker = null;
        if (current != null) {
            current.failure.compareAndSet(null, unavailable("ResNet2 传输已经关闭", null));
            destroy(current);
            current.responses.offer(WorkerOutput.eof());
        }
    }

    private void consumeReady(Worker started, long startupTimeoutMs) {
        WorkerOutput output = poll(started, startupTimeoutMs, "等待 ResNet2 READY 时被中断");
        if (output == null) {
            throw timeout("ResNet2 worker 未在截止时间内发送 READY");
        }
        if (output.end()) {
            throw started.failure.get() == null
                    ? unavailable("ResNet2 worker 在 READY 前结束 stdout", null)
                    : started.failure.get();
        }
        try {
            JsonNode ready = objectMapper.readTree(output.line());
            if (ready == null || !ready.isObject()
                    || !ResNet2ProtocolAdapter.CONTRACT_VERSION.equals(
                    ready.path("contractVersion").asText())
                    || !"READY".equals(ready.path("messageType").asText())
                    || !ResNet2ProtocolAdapter.MODEL_ID.equals(
                    ready.path("modelId").asText())) {
                throw new IllegalArgumentException("READY 版本、消息类型或模型能力不匹配");
            }
            LOGGER.info("Python 正式出牌模型已就绪 contractVersion={} modelId={}",
                    ready.path("contractVersion").asText(), ready.path("modelId").asText());
            LOGGER.debug("Python 正式出牌模型 READY 明细 ready={}", ready);
        } catch (Exception error) {
            throw unavailable("ResNet2 READY 协议污染", error);
        }
    }

    private void writeRequest(Worker active, ResNet2Request request) {
        try {
            active.writer.write(objectMapper.writeValueAsString(request));
            active.writer.newLine();
            active.writer.flush();
            LOGGER.info("调用 Python 正式出牌模型接口 requestId={} dealId={} modelId={} "
                            + "position={} handCount={} historyCount={} deadlineMs={}",
                    request.requestId(), request.dealId(), request.modelId(), request.position(),
                    request.hand().size(), request.actionHistory().size(), request.deadlineMs());
            LOGGER.debug("Python 正式出牌模型请求明细 request={}", request);
        } catch (JsonProcessingException error) {
            throw unavailable("无法编码 ResNet2 请求", error);
        } catch (IOException error) {
            ModelTransportException failure = unavailable("写入 ResNet2 worker 失败", error);
            retire(active, failure);
            throw failure;
        }
    }

    private String awaitResponse(Worker active, long deadlineMs) {
        WorkerOutput output = poll(active, deadlineMs, "等待 ResNet2 响应时被中断");
        if (output == null) {
            ModelTransportException timeout = timeout("ResNet2 响应超时");
            retire(active, timeout);
            throw timeout;
        }
        if (output.end()) {
            ModelTransportException ended = active.failure.get();
            if (ended == null) {
                ended = unavailable("ResNet2 worker stdout 提前结束", null);
            }
            retire(active, ended);
            throw ended;
        }
        return output.line();
    }

    private ResNet2Response decodeAndValidate(
            Worker active,
            ResNet2Request request,
            String line
    ) {
        ResNet2Response response;
        try {
            response = objectMapper.readValue(line, ResNet2Response.class);
        } catch (JsonProcessingException | RuntimeException error) {
            ModelTransportException pollution = unavailable(
                    "ResNet2 stdout 协议污染", error);
            retire(active, pollution);
            throw pollution;
        }
        if (!request.contractVersion().equals(response.contractVersion())
                || !request.requestId().equals(response.requestId())
                || !request.dealId().equals(response.dealId())
                || !request.modelId().equals(response.modelId())) {
            ModelTransportException mismatch = new ModelTransportException(
                    ModelTransportException.Reason.INVALID_RESPONSE,
                    "ResNet2 响应身份与当前请求不匹配", null);
            retire(active, mismatch);
            throw mismatch;
        }
        return response;
    }

    private WorkerOutput poll(Worker active, long waitMs, String interruptedMessage) {
        try {
            return active.responses.poll(waitMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            ModelTransportException interrupted = unavailable(interruptedMessage, error);
            retire(active, interrupted);
            throw interrupted;
        }
    }

    private void pumpStdout(Worker source) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                source.process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                source.responses.put(WorkerOutput.line(line));
            }
            if (!closed.get()) {
                retire(source, unavailable("ResNet2 worker stdout 提前结束", null));
            }
        } catch (IOException error) {
            if (!closed.get()) {
                retire(source, unavailable("读取 ResNet2 worker stdout 失败", error));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, unavailable("ResNet2 stdout reader 被中断", error));
            }
        } finally {
            source.responses.offer(WorkerOutput.eof());
        }
    }

    private void pumpStderr(Worker source) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                source.process.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                LOGGER.debug("Python 正式出牌模型 stderr: {}", line);
            }
        } catch (IOException error) {
            if (!closed.get()) {
                LOGGER.warn("读取 Python ResNet2 worker stderr 失败", error);
            }
        }
    }

    private void awaitExit(Worker source) {
        try {
            int exitCode = source.process.waitFor();
            if (!closed.get()) {
                retire(source, unavailable(
                        "Python ResNet2 worker 提前退出，exitCode=" + exitCode, null));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, unavailable("等待 Python ResNet2 worker 退出时被中断", error));
            }
        }
    }

    private synchronized void retire(Worker retired, ModelTransportException reason) {
        retired.failure.compareAndSet(null, reason);
        if (worker == retired) {
            worker = null;
        }
        destroy(retired);
        retired.responses.offer(WorkerOutput.eof());
    }

    private void destroy(Worker retired) {
        try {
            retired.writer.close();
        } catch (IOException ignored) {
            // worker 已失效，关闭 stdin 只用于解除底层阻塞。
        }
        if (retired.process.isAlive()) {
            retired.process.destroy();
        }
    }

    private Worker requireHealthyWorker() {
        Worker current = worker;
        if (!isHealthy(current)) {
            throw current == null || current.failure.get() == null
                    ? unavailable("ResNet2 worker 当前不可用", null)
                    : current.failure.get();
        }
        return current;
    }

    private boolean isHealthy(Worker candidate) {
        return candidate != null && candidate.failure.get() == null
                && candidate.process.isAlive();
    }

    private void requireOpen() {
        if (closed.get()) {
            throw unavailable("ResNet2 传输已经关闭", null);
        }
    }

    private ModelTransportException timeout(String message) {
        return new ModelTransportException(
                ModelTransportException.Reason.TIMEOUT, message, null);
    }

    private ModelTransportException unavailable(String message, Throwable cause) {
        return new ModelTransportException(
                ModelTransportException.Reason.UNAVAILABLE, message, cause);
    }

    private void startDaemon(String name, Runnable task) {
        Thread thread = new Thread(task, name);
        thread.setDaemon(true);
        thread.start();
    }

    /** 单个进程代次拥有的 IO、响应队列和首个失败原因。 */
    private static final class Worker {
        /** 当前代次 Python 进程。 */
        private final Process process;

        /** 当前代次 stdin writer。 */
        private final BufferedWriter writer;

        /** 当前代次 stdout 单行消息；旧代次输出不能进入新代次队列。 */
        private final BlockingQueue<WorkerOutput> responses = new LinkedBlockingQueue<>();

        /** 当前代次首个稳定失败原因。 */
        private final AtomicReference<ModelTransportException> failure = new AtomicReference<>();

        private Worker(Process process, BufferedWriter writer) {
            this.process = process;
            this.writer = writer;
        }
    }

    /**
     * worker stdout 队列项。
     *
     * @param line 一条完整 JSON；EOF 时为空
     * @param end 是否表示当前 worker stdout 已结束
     */
    private record WorkerOutput(String line, boolean end) {
        private static WorkerOutput line(String line) {
            return new WorkerOutput(Objects.requireNonNull(line, "worker 输出行不能为空"), false);
        }

        private static WorkerOutput eof() {
            return new WorkerOutput(null, true);
        }
    }
}
