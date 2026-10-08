package com.mkuiwu.douzero.runtime.infrastructure.model.preplay;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceReady;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceRequest;
import com.mkuiwu.douzero.runtime.contract.model.preplay.PreplayInferenceResponse;
import com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl.ProcessLauncher;
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
 * 一个持久、严格串行且可按 worker 代次恢复的 Python 局前模型 JSONL 客户端。
 *
 * <p>每个 worker 独享响应队列和后台读取线程。超时、EOF、协议污染或进程退出只淘汰当前
 * worker；下一请求会启动新进程并重新校验 READY。客户端 close 是永久边界，关闭后不得重启。</p>
 */
public final class JsonLinePreplayModelClient implements AutoCloseable {
    private static final Logger LOGGER = LoggerFactory.getLogger(JsonLinePreplayModelClient.class);

    /** 跨进程 JSON 序列化器。 */
    private final ObjectMapper objectMapper;

    /** 可替换进程启动器。 */
    private final ProcessLauncher launcher;

    /** 不经 shell 解析的 worker 命令及参数。 */
    private final List<String> command;

    /** 保证任何时刻只有一个请求占用 stdin/stdout 协议。 */
    private final ReentrantLock exchangeLock = new ReentrantLock();

    /** 客户端是否已经进入不可逆关闭边界。 */
    private final AtomicBoolean closed = new AtomicBoolean();

    /** 当前可用或正在启动的 worker 代次；生命周期变更由 synchronized 方法保护。 */
    private volatile Worker worker;

    public JsonLinePreplayModelClient(
            ObjectMapper objectMapper,
            ProcessLauncher launcher,
            List<String> command
    ) {
        this.objectMapper = Objects.requireNonNull(objectMapper, "局前模型 JSON 序列化器不能为空");
        this.launcher = Objects.requireNonNull(launcher, "局前模型进程启动器不能为空");
        this.command = List.copyOf(Objects.requireNonNull(command, "局前模型命令不能为空"));
        if (this.command.isEmpty() || this.command.stream().anyMatch(value -> value == null
                || value.isBlank())) {
            throw new IllegalArgumentException("局前模型命令必须包含非空参数");
        }
    }

    /**
     * 保证存在一个已经发送并通过校验 READY 的 worker；失效代次会先被完整淘汰。
     *
     * @param startupTimeoutMs 等待新 worker READY 的最长时间，单位为毫秒
     */
    public synchronized void start(long startupTimeoutMs) {
        requirePositive(startupTimeoutMs, "局前模型启动超时必须为正数");
        requireOpen();
        Worker current = worker;
        if (isHealthy(current)) {
            return;
        }
        if (current != null) {
            retire(current, current.failure.get() == null
                    ? unavailable("旧局前模型 worker 已失效", null)
                    : current.failure.get());
        }

        Worker started;
        try {
            Process process = Objects.requireNonNull(
                    launcher.launch(command), "进程启动器不能返回 null");
            started = new Worker(process, new BufferedWriter(new OutputStreamWriter(
                    process.getOutputStream(), StandardCharsets.UTF_8)));
            worker = started;
            startDaemon("douzero-preplay-stdout", () -> pumpStdout(started));
            startDaemon("douzero-preplay-stderr", () -> pumpStderr(started));
            startDaemon("douzero-preplay-exit", () -> awaitExit(started));
        } catch (IOException | RuntimeException error) {
            throw unavailable("无法启动 Python 局前模型进程", error);
        }

        try {
            consumeReady(started, startupTimeoutMs);
            if (!isHealthy(started) || worker != started) {
                throw started.failure.get() == null
                        ? unavailable("局前模型在 READY 后立即失效", null)
                        : started.failure.get();
            }
        } catch (RuntimeException error) {
            PreplayModelTransportException transport = error instanceof PreplayModelTransportException value
                    ? value : unavailable("局前模型 READY 协议污染", error);
            retire(started, transport);
            throw transport;
        }
    }

    /** 严格串行写入请求并在自身 deadline 内读取唯一响应；必要时先恢复 worker。 */
    public PreplayInferenceResponse exchange(PreplayInferenceRequest request) {
        Objects.requireNonNull(request, "局前模型请求不能为空");
        exchangeLock.lock();
        try {
            requireOpen();
            long startedAt = System.nanoTime();
            start(request.deadlineMs());
            Worker active = requireHealthyWorker();
            long elapsedMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - startedAt);
            long remainingMs = request.deadlineMs() - elapsedMs;
            if (remainingMs <= 0) {
                PreplayModelTransportException timeout = new PreplayModelTransportException(
                        PreplayModelTransportException.Reason.TIMEOUT,
                        "局前模型恢复 worker 后已超过请求截止时间");
                retire(active, timeout);
                throw timeout;
            }
            writeRequest(active, request);
            String line = awaitResponse(active, remainingMs);
            try {
                PreplayInferenceResponse response = objectMapper.readValue(
                        line, PreplayInferenceResponse.class);
                LOGGER.info("Python 局前模型接口返回 requestId={} dealId={} generation={} "
                                + "modelId={} status={} action={} latencyMs={} errorCode={}",
                        response.requestId(), response.dealId(), response.generation(),
                        response.modelId(), response.status(), response.action(),
                        response.latencyMs(), response.errorCode());
                LOGGER.debug("Python 局前模型响应明细 response={}", response);
                return response;
            } catch (JsonProcessingException error) {
                PreplayModelTransportException pollution = unavailable(
                        "局前模型 stdout 协议污染", error);
                retire(active, pollution);
                throw pollution;
            }
        } catch (RuntimeException error) {
            LOGGER.warn("Python 局前模型接口调用失败 requestId={} dealId={} generation={} "
                            + "modelId={} reason={}",
                    request.requestId(), request.dealId(), request.generation(),
                    request.modelId(), error.getMessage());
            LOGGER.debug("Python 局前模型接口异常明细 requestId={}",
                    request.requestId(), error);
            throw error;
        } finally {
            exchangeLock.unlock();
        }
    }

    /** 永久关闭客户端并唤醒正在等待当前 worker 响应的调用；后续请求不得重启。 */
    @Override
    public synchronized void close() {
        if (!closed.compareAndSet(false, true)) {
            return;
        }
        Worker current = worker;
        worker = null;
        if (current != null) {
            current.failure.compareAndSet(null, unavailable("局前模型客户端已经关闭", null));
            destroy(current);
            current.responses.offer(WorkerOutput.eof());
        }
    }

    private void consumeReady(Worker started, long startupTimeoutMs) {
        WorkerOutput output = poll(started, startupTimeoutMs, "等待局前模型 READY 时被中断");
        if (output == null || output.end()) {
            throw started.failure.get() == null
                    ? unavailable("局前模型未在截止时间内发送 READY", null)
                    : started.failure.get();
        }
        try {
            PreplayInferenceReady ready = objectMapper.readValue(
                    output.line(), PreplayInferenceReady.class);
            if (!PreplayProtocolAdapter.CONTRACT_VERSION.equals(ready.contractVersion())) {
                throw new IllegalArgumentException("局前模型 READY 协议版本不匹配");
            }
            LOGGER.info("Python 局前模型已就绪 contractVersion={}", ready.contractVersion());
            LOGGER.debug("Python 局前模型 READY 明细 ready={}", ready);
        } catch (Exception error) {
            throw unavailable("局前模型 READY 协议污染", error);
        }
    }

    private void writeRequest(Worker active, PreplayInferenceRequest request) {
        try {
            active.writer.write(objectMapper.writeValueAsString(request));
            active.writer.newLine();
            active.writer.flush();
            LOGGER.info("调用 Python 局前模型接口 requestId={} dealId={} generation={} "
                            + "modelId={} stage={} handCount={} availableActions={} deadlineMs={}",
                    request.requestId(), request.dealId(), request.generation(),
                    request.modelId(), request.stage(), request.hand().size(),
                    request.availableActions(), request.deadlineMs());
            LOGGER.debug("Python 局前模型请求明细 request={}", request);
        } catch (JsonProcessingException error) {
            throw unavailable("无法编码局前模型请求", error);
        } catch (IOException error) {
            PreplayModelTransportException writeFailure = unavailable(
                    "写入局前模型进程失败", error);
            retire(active, writeFailure);
            throw writeFailure;
        }
    }

    private String awaitResponse(Worker active, long deadlineMs) {
        WorkerOutput output = poll(active, deadlineMs, "等待局前模型响应时被中断");
        if (output == null) {
            PreplayModelTransportException timeout = new PreplayModelTransportException(
                    PreplayModelTransportException.Reason.TIMEOUT, "局前模型响应超时");
            retire(active, timeout);
            throw timeout;
        }
        if (output.end()) {
            PreplayModelTransportException ended = active.failure.get();
            if (ended == null) {
                ended = unavailable("局前模型 stdout 提前结束", null);
            }
            retire(active, ended);
            throw ended;
        }
        return output.line();
    }

    private WorkerOutput poll(Worker active, long timeoutMs, String interruptedMessage) {
        try {
            return active.responses.poll(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            PreplayModelTransportException interrupted = unavailable(interruptedMessage, error);
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
                retire(source, unavailable("局前模型 stdout 提前结束", null));
            }
        } catch (IOException error) {
            if (!closed.get()) {
                retire(source, unavailable("读取局前模型 stdout 失败", error));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, unavailable("局前模型 stdout reader 被中断", error));
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
                LOGGER.debug("Python 局前模型 stderr: {}", line);
            }
        } catch (IOException error) {
            if (!closed.get()) {
                LOGGER.warn("读取 Python 局前模型 stderr 失败", error);
            }
        }
    }

    private void awaitExit(Worker source) {
        try {
            int exitCode = source.process.waitFor();
            if (!closed.get()) {
                retire(source, unavailable(
                        "Python 局前模型提前退出，exitCode=" + exitCode, null));
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            if (!closed.get()) {
                retire(source, unavailable("等待 Python 局前模型退出时被中断", error));
            }
        }
    }

    private synchronized void retire(
            Worker retired,
            PreplayModelTransportException reason
    ) {
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
                    ? unavailable("局前模型 worker 当前不可用", null)
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
            throw unavailable("局前模型客户端已经关闭", null);
        }
    }

    private static void requirePositive(long value, String message) {
        if (value <= 0) {
            throw new IllegalArgumentException(message);
        }
    }

    private PreplayModelTransportException unavailable(String message, Throwable cause) {
        return new PreplayModelTransportException(
                PreplayModelTransportException.Reason.UNAVAILABLE, message, cause);
    }

    private void startDaemon(String name, Runnable task) {
        Thread thread = new Thread(task, name);
        thread.setDaemon(true);
        thread.start();
    }

    /** 单个进程代次拥有的 IO、响应队列和失败状态，绝不跨重启复用。 */
    private static final class Worker {
        /** 当前代次 Python 进程。 */
        private final Process process;

        /** 当前代次 stdin writer。 */
        private final BufferedWriter writer;

        /** 当前代次 stdout 单行输出；旧代输出不能进入新代队列。 */
        private final BlockingQueue<WorkerOutput> responses = new LinkedBlockingQueue<>();

        /** 当前代次首个稳定失败原因。 */
        private final AtomicReference<PreplayModelTransportException> failure =
                new AtomicReference<>();

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
