package com.mkuiwu.douzero.runtime.infrastructure.desktop;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mkuiwu.douzero.runtime.application.model.ActionScore;
import com.mkuiwu.douzero.runtime.application.model.AdviceResult;
import com.mkuiwu.douzero.runtime.application.model.FinishReason;
import com.mkuiwu.douzero.runtime.application.model.PreplayResult;
import com.mkuiwu.douzero.runtime.application.model.PreplaySnapshot;
import com.mkuiwu.douzero.runtime.application.port.GameRuntimeObserver;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopAction;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopActionScore;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopEventType;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopGameFinishedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopHistoryItem;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPhaseChangedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPlayAdviceEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPlayTurnStartedEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopPreplayAdviceEvent;
import com.mkuiwu.douzero.runtime.contract.desktop.v1.DesktopReadyEvent;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PlayRecord;
import org.java_websocket.WebSocket;
import org.java_websocket.framing.CloseFrame;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.server.WebSocketServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 仅绑定 127.0.0.1 的 desktop.v1 WebSocket 网关；只发布 Java 权威事件，不接受控制命令。
 */
public final class DesktopWebSocketGateway extends WebSocketServer
        implements GameRuntimeObserver, AutoCloseable {
    /** Electron 鉴权头使用的固定 Bearer 前缀。 */
    public static final String AUTHORIZATION_PREFIX = "Bearer ";
    /** Java 与 Electron 当前共同支持的桌面协议版本。 */
    public static final String PROTOCOL = "desktop.v1";

    private static final Logger LOGGER = LoggerFactory.getLogger(DesktopWebSocketGateway.class);

    private final ObjectMapper objectMapper;
    private final byte[] expectedAuthorization;
    private final boolean orchestrationEnabled;
    private final String serverInstanceId = UUID.randomUUID().toString();
    private final AtomicLong sequence = new AtomicLong();
    private final CountDownLatch started = new CountDownLatch(1);
    private final java.util.Set<WebSocket> authorizedConnections = ConcurrentHashMap.newKeySet();
    private final Object replayLock = new Object();

    private DesktopPhaseChangedEvent latestPhase;
    private DesktopPreplayAdviceEvent latestPreplayAdvice;
    private DesktopPlayTurnStartedEvent latestPlayTurnStarted;
    private DesktopPlayAdviceEvent latestPlayAdvice;
    private DesktopGameFinishedEvent latestGameFinished;

    /**
     * @param port 本机监听端口；测试可传零让操作系统分配空闲端口
     * @param token 当前联合启动唯一的随机鉴权令牌，不得写入日志
     * @param orchestrationEnabled Java 牌局状态机是否已显式启用
     * @param objectMapper desktop.v1 JSON 序列化器
     */
    public DesktopWebSocketGateway(
            int port,
            String token,
            boolean orchestrationEnabled,
            ObjectMapper objectMapper
    ) {
        super(new InetSocketAddress(loopback(), port));
        if (port < 0 || port > 65535) {
            throw new IllegalArgumentException("桌面 WebSocket 端口必须在 0 到 65535 之间");
        }
        if (token == null || token.length() < 32) {
            throw new IllegalArgumentException("桌面 WebSocket 启动令牌至少需要三十二个字符");
        }
        this.expectedAuthorization = (AUTHORIZATION_PREFIX + token)
                .getBytes(StandardCharsets.UTF_8);
        this.orchestrationEnabled = orchestrationEnabled;
        this.objectMapper = Objects.requireNonNull(objectMapper, "桌面 JSON 序列化器不能为空");
        setReuseAddr(true);
        setConnectionLostTimeout(15);
    }

    /** 启动网关并等待端口完成绑定；超时或启动失败时明确阻止应用继续启动。 */
    public void startAndAwait(Duration timeout) {
        Objects.requireNonNull(timeout, "桌面 WebSocket 启动超时不能为空");
        if (timeout.isZero() || timeout.isNegative()) {
            throw new IllegalArgumentException("桌面 WebSocket 启动超时必须为正数");
        }
        super.start();
        try {
            if (!started.await(timeout.toMillis(), TimeUnit.MILLISECONDS)) {
                throw new IllegalStateException("桌面 WebSocket 未在截止时间内开始监听");
            }
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("等待桌面 WebSocket 启动时被中断", exception);
        }
    }

    /** 返回实际绑定端口；传入零自动选端口时用于测试和受控集成验证。 */
    public int boundPort() {
        return getPort();
    }

    @Override
    public void onOpen(WebSocket connection, ClientHandshake handshake) {
        if (!"/desktop/v1".equals(handshake.getResourceDescriptor())) {
            connection.close(CloseFrame.POLICY_VALIDATION, "unsupported resource");
            return;
        }
        if (!isAuthorized(connection, handshake)) {
            connection.close(CloseFrame.POLICY_VALIDATION, "unauthorized");
            return;
        }
        authorizedConnections.add(connection);
        send(connection, new DesktopReadyEvent(
                PROTOCOL,
                DesktopEventType.READY,
                0,
                Instant.now().toString(),
                serverInstanceId,
                orchestrationEnabled));
        replayCurrentState(connection);
    }

    @Override
    public void onClose(WebSocket connection, int code, String reason, boolean remote) {
        authorizedConnections.remove(connection);
        LOGGER.debug("桌面 WebSocket 连接关闭 code={} remote={}", code, remote);
    }

    @Override
    public void onMessage(WebSocket connection, String message) {
        connection.close(CloseFrame.REFUSE, "desktop.v1 is read-only");
    }

    @Override
    public void onMessage(WebSocket connection, ByteBuffer message) {
        connection.close(CloseFrame.REFUSE, "desktop.v1 is read-only");
    }

    @Override
    public void onError(WebSocket connection, Exception exception) {
        LOGGER.warn("桌面 WebSocket 发生非敏感传输错误 type={}",
                exception.getClass().getSimpleName());
    }

    @Override
    public void onStart() {
        started.countDown();
        LOGGER.info("桌面 WebSocket 已监听 host=127.0.0.1 port={} protocol={}",
                boundPort(), PROTOCOL);
    }

    @Override
    public void onPhaseChanged(GamePhase phase, String dealId) {
        Objects.requireNonNull(phase, "桌面阶段不能为空");
        DesktopPhaseChangedEvent event = new DesktopPhaseChangedEvent(
                PROTOCOL,
                DesktopEventType.PHASE_CHANGED,
                nextSequence(),
                Instant.now().toString(),
                serverInstanceId,
                phase.name(),
                dealId == null ? "" : dealId);
        synchronized (replayLock) {
            latestPhase = event;
            latestPlayTurnStarted = null;
            latestPlayAdvice = null;
            if (phase == GamePhase.WAIT_NEW_GAME) {
                latestPreplayAdvice = null;
            }
        }
        broadcast(event);
    }

    @Override
    public void onPreplayAdvice(
            PreplaySnapshot snapshot,
            PreplayResult.Recommendation recommendation
    ) {
        Objects.requireNonNull(snapshot, "桌面局前快照不能为空");
        Objects.requireNonNull(recommendation, "桌面局前建议不能为空");
        DesktopPreplayAdviceEvent event = new DesktopPreplayAdviceEvent(
                PROTOCOL,
                DesktopEventType.PREPLAY_ADVICE,
                nextSequence(),
                Instant.now().toString(),
                serverInstanceId,
                snapshot.dealId(),
                snapshot.generation(),
                snapshot.stage().name(),
                cards(snapshot.hand()),
                cards(snapshot.bottomCards()),
                snapshot.availableActions().stream().map(Enum::name).sorted().toList(),
                snapshot.callPromptSeen(),
                snapshot.robPromptSeen(),
                recommendation.modelId(),
                recommendation.action().name(),
                recommendation.score(),
                recommendation.threshold());
        synchronized (replayLock) {
            latestPreplayAdvice = event;
        }
        broadcast(event);
    }

    @Override
    public void onPlayAdvice(GameSnapshot snapshot, AdviceResult result) {
        Objects.requireNonNull(snapshot, "桌面正式牌局快照不能为空");
        Objects.requireNonNull(result, "桌面正式建议结果不能为空");

        String resultKind;
        String modelId;
        DesktopAction recommendedAction;
        Double actionValue;
        Double actionMargin;
        List<DesktopActionScore> actionScores;
        String failure;
        String detail;
        if (result instanceof AdviceResult.Recommendation recommendation) {
            resultKind = "RECOMMENDATION";
            modelId = recommendation.modelId();
            recommendedAction = action(recommendation.action());
            actionValue = recommendation.actionValue();
            actionMargin = recommendation.actionMargin();
            actionScores = recommendation.actionScores().stream().map(this::score).toList();
            failure = "";
            detail = "";
        } else {
            AdviceResult.Unavailable unavailable = (AdviceResult.Unavailable) result;
            resultKind = "UNAVAILABLE";
            modelId = unavailable.modelId();
            recommendedAction = null;
            actionValue = null;
            actionMargin = null;
            actionScores = List.of();
            failure = unavailable.failure().name();
            detail = unavailable.detail();
        }
        DesktopPlayAdviceEvent event = new DesktopPlayAdviceEvent(
                PROTOCOL,
                DesktopEventType.PLAY_ADVICE,
                nextSequence(),
                Instant.now().toString(),
                serverInstanceId,
                snapshot.dealId(),
                snapshot.phase().name(),
                snapshot.localSeat().name(),
                cards(snapshot.hand()),
                cards(snapshot.bottomCards()),
                snapshot.currentSeat().name(),
                cards(snapshot.lastMove()),
                snapshot.history().stream().map(this::historyItem).toList(),
                resultKind,
                modelId,
                recommendedAction,
                actionValue,
                actionMargin,
                actionScores,
                failure,
                detail);
        synchronized (replayLock) {
            latestPlayTurnStarted = null;
            latestPlayAdvice = event;
        }
        broadcast(event);
    }

    @Override
    public void onPlayTurnStarted(GameSnapshot snapshot) {
        Objects.requireNonNull(snapshot, "桌面本方回合快照不能为空");
        DesktopPlayTurnStartedEvent event = new DesktopPlayTurnStartedEvent(
                PROTOCOL,
                DesktopEventType.PLAY_TURN_STARTED,
                nextSequence(),
                Instant.now().toString(),
                serverInstanceId,
                snapshot.dealId(),
                snapshot.phase().name(),
                snapshot.localSeat().name(),
                snapshot.currentSeat().name());
        synchronized (replayLock) {
            latestPlayTurnStarted = event;
            latestPlayAdvice = null;
        }
        broadcast(event);
    }

    @Override
    public void onGameFinished(String dealId, FinishReason reason) {
        if (dealId == null || dealId.isBlank()) {
            throw new IllegalArgumentException("桌面收口牌局标识不能为空");
        }
        Objects.requireNonNull(reason, "桌面收口原因不能为空");
        DesktopGameFinishedEvent event = new DesktopGameFinishedEvent(
                PROTOCOL,
                DesktopEventType.GAME_FINISHED,
                nextSequence(),
                Instant.now().toString(),
                serverInstanceId,
                dealId,
                reason.name());
        synchronized (replayLock) {
            latestGameFinished = event;
        }
        broadcast(event);
    }

    @Override
    public void close() {
        try {
            stop(1000);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            LOGGER.warn("关闭桌面 WebSocket 时被中断");
        }
    }

    private boolean isAuthorized(WebSocket connection, ClientHandshake handshake) {
        if (connection.getRemoteSocketAddress() == null
                || !connection.getRemoteSocketAddress().getAddress().isLoopbackAddress()) {
            return false;
        }
        byte[] actual = handshake.getFieldValue("Authorization").getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(expectedAuthorization, actual);
    }

    private void replayCurrentState(WebSocket connection) {
        List<DesktopEvent> replay = new ArrayList<>();
        synchronized (replayLock) {
            if (latestPhase != null) replay.add(latestPhase);
            if (latestPreplayAdvice != null) replay.add(latestPreplayAdvice);
            if (latestPlayTurnStarted != null) replay.add(latestPlayTurnStarted);
            if (latestPlayAdvice != null) replay.add(latestPlayAdvice);
            if (latestGameFinished != null) replay.add(latestGameFinished);
        }
        replay.stream()
                .sorted(Comparator.comparingLong(DesktopEvent::sequence))
                .forEach(event -> send(connection, event));
    }

    private void broadcast(DesktopEvent event) {
        String json = json(event);
        getConnections().stream()
                .filter(connection -> connection.isOpen()
                        && authorizedConnections.contains(connection))
                .forEach(connection -> connection.send(json));
    }

    private void send(WebSocket connection, DesktopEvent event) {
        connection.send(json(event));
    }

    private String json(DesktopEvent event) {
        try {
            return objectMapper.writeValueAsString(event);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("desktop.v1 事件序列化失败", exception);
        }
    }

    private long nextSequence() {
        return sequence.incrementAndGet();
    }

    private DesktopActionScore score(ActionScore score) {
        return new DesktopActionScore(action(score.action()), score.value());
    }

    private DesktopHistoryItem historyItem(PlayRecord record) {
        return new DesktopHistoryItem(record.player().seat().name(), action(record.action()));
    }

    private DesktopAction action(PlayAction value) {
        if (value instanceof PlayAction.Pass) {
            return new DesktopAction(true, List.of());
        }
        return new DesktopAction(false, cards(((PlayAction.Play) value).cards()));
    }

    private List<String> cards(CardSet cardSet) {
        return cardSet.cards().stream().map(Card::toString).toList();
    }

    private static InetAddress loopback() {
        try {
            return InetAddress.getByName("127.0.0.1");
        } catch (java.net.UnknownHostException exception) {
            throw new IllegalStateException("无法解析固定回环地址 127.0.0.1", exception);
        }
    }
}
