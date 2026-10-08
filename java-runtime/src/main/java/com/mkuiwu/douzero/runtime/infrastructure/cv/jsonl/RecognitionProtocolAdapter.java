package com.mkuiwu.douzero.runtime.infrastructure.cv.jsonl;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailure;
import com.mkuiwu.douzero.runtime.application.model.RecognitionFailureCode;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.NewGameRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.PreplayRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.SettlementWatchPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionCancel;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionMessageType;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionResult;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionStatus;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionSubmit;
import com.mkuiwu.douzero.runtime.contract.recognition.v1.RecognitionTaskType;
import com.mkuiwu.douzero.runtime.domain.Card;
import com.mkuiwu.douzero.runtime.domain.CardRank;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayAction;
import com.mkuiwu.douzero.runtime.domain.PreplayStage;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/** recognition.v1 扁平线协议与四个现有 Java 业务端口之间的唯一语义映射点。 */
public final class RecognitionProtocolAdapter {
    /** 当前支持的 Python CV 线协议版本。 */
    public static final String CONTRACT_VERSION = "recognition.v1";

    /** 把无局态请求编码为新局任务。 */
    public RecognitionSubmit encode(NewGameRecognitionPort.Request request) {
        Objects.requireNonNull(request, "新局识别请求不能为空");
        return submit(RecognitionTaskType.NEW_GAME, request.requestId(), null, null,
                request.deadlineMs(), null, null, null, null);
    }

    /** 把牌局初始化请求编码为局内任务。 */
    public RecognitionSubmit encode(DealRecognitionPort.Request request) {
        Objects.requireNonNull(request, "牌局初始化请求不能为空");
        return submit(RecognitionTaskType.DEAL, request.identity(), request.deadlineMs(),
                null, null, null, null);
    }

    /** 把局前提示请求编码为只含提示入口边沿的局内任务。 */
    public RecognitionSubmit encode(PreplayRecognitionPort.Request request) {
        Objects.requireNonNull(request, "局前提示请求不能为空");
        String entryMode = switch (request.entryMode()) {
            case ACCEPT_CURRENT_STABLE_PROMPT -> "accept_current_stable_prompt";
            case REQUIRE_CHANGE_OR_EXIT_THEN_NEW -> "require_change_then_new";
        };
        return submit(RecognitionTaskType.PREPLAY_PROMPT, request.identity(), request.deadlineMs(),
                entryMode, null, null, null);
    }

    /** 把本方回合请求编码为只含语义座位和边沿约束的局内任务。 */
    public RecognitionSubmit encode(LocalTurnRecognitionPort.Request request) {
        Objects.requireNonNull(request, "本方回合请求不能为空");
        return submit(RecognitionTaskType.LOCAL_TURN, request.identity(), request.deadlineMs(),
                null, encodeSeat(request.localSeat()),
                request.requiredActors().stream().map(this::encodeSeat).sorted().toList(),
                switch (request.turnEntryMode()) {
                    case ACCEPT_CURRENT_STABLE_TURN -> "accept_current_stable_turn";
                    case REQUIRE_EXIT_THEN_REENTER -> "require_exit_then_reenter";
                });
    }

    /** 把建议后的当前回合结束探测编码为携带语义基线的局内任务。 */
    public RecognitionSubmit encode(TurnEndRecognitionPort.Request request) {
        Objects.requireNonNull(request, "回合结束识别请求不能为空");
        Map<String, List<String>> actions = new LinkedHashMap<>();
        request.baselineActionsBySeat().forEach((seat, action) -> actions.put(
                encodeSeat(seat), encodeAction(action)));
        return submit(RecognitionTaskType.TURN_END, request.identity(), request.deadlineMs(),
                null, encodeSeat(request.localSeat()), null, null,
                encodeCards(request.baselineHand()), actions);
    }

    /** 把整局结算旁路请求编码为局内任务。 */
    public RecognitionSubmit encode(SettlementWatchPort.Request request) {
        Objects.requireNonNull(request, "结算识别请求不能为空");
        return submit(RecognitionTaskType.SETTLEMENT, request.identity(), request.deadlineMs(),
                null, null, null, null);
    }

    /** 为提交消息生成具有同一身份的幂等取消消息。 */
    public RecognitionCancel cancel(RecognitionSubmit submit) {
        Objects.requireNonNull(submit, "待取消识别提交不能为空");
        return new RecognitionCancel(CONTRACT_VERSION, RecognitionMessageType.CANCEL,
                submit.taskType(), submit.requestId(), submit.dealId(), submit.generation());
    }

    /** 校验并解码新局最终结果。 */
    public NewGameRecognitionPort.Result decode(NewGameRecognitionPort.Request request,
                                                RecognitionResult result) {
        validate(result, RecognitionTaskType.NEW_GAME, request.requestId(), null, null);
        if (result.status() == RecognitionStatus.FAILED) {
            return new NewGameRecognitionPort.Failed(request.requestId(), failure(result));
        }
        return new NewGameRecognitionPort.Detected(request.requestId(),
                Objects.requireNonNull(result.observedAt(), "新局成功结果缺少观察时间"));
    }

    /** 校验并解码牌局初始化最终结果。 */
    public DealRecognitionPort.Result decode(DealRecognitionPort.Request request,
                                             RecognitionResult result) {
        GameTaskIdentity identity = request.identity();
        validate(result, RecognitionTaskType.DEAL, identity.requestId(), identity.dealId(),
                identity.generation());
        if (result.status() == RecognitionStatus.FAILED) {
            return new DealRecognitionPort.Failed(identity, failure(result));
        }
        return new DealRecognitionPort.Recognized(identity, decodeSeat(result.localSeat()),
                decodeCards(result.hand(), "牌局初始化手牌"),
                decodeCards(result.bottomCards(), "底牌"),
                decodeCards(result.landlordOpeningPlay(), "地主首手牌"),
                Objects.requireNonNull(result.observedAt(), "牌局初始化成功结果缺少观察时间"));
    }

    /** 校验并解码局前提示，把线协议动作立即恢复为领域枚举。 */
    public PreplayRecognitionPort.Result decode(PreplayRecognitionPort.Request request,
                                                RecognitionResult result) {
        GameTaskIdentity identity = request.identity();
        validate(result, RecognitionTaskType.PREPLAY_PROMPT, identity.requestId(),
                identity.dealId(), identity.generation());
        if (result.status() == RecognitionStatus.FAILED) {
            return new PreplayRecognitionPort.Failed(identity, failure(result));
        }
        PreplayStage stage = switch (Objects.requireNonNull(result.promptType(),
                "局前提示成功结果缺少提示类型")) {
            case "call" -> PreplayStage.CALL_LANDLORD;
            case "rob" -> PreplayStage.ROB_LANDLORD;
            case "double" -> PreplayStage.DOUBLE;
            default -> throw new IllegalArgumentException("未知局前提示类型: " + result.promptType());
        };
        java.util.Set<PreplayAction> actions = Objects.requireNonNull(result.availableActions(),
                        "局前提示成功结果缺少可用动作").stream()
                .map(this::decodePreplayAction)
                .collect(java.util.stream.Collectors.toUnmodifiableSet());
        return new PreplayRecognitionPort.Ready(identity, stage,
                decodeCards(result.hand(), "局前提示手牌"),
                decodeCards(result.bottomCards(), "局前提示底牌"), actions,
                Objects.requireNonNull(result.observedAt(), "局前提示成功结果缺少观察时间"));
    }

    /** 校验并解码本方回合最终结果，把空数组立即恢复为显式 Pass。 */
    public LocalTurnRecognitionPort.Result decode(LocalTurnRecognitionPort.Request request,
                                                  RecognitionResult result) {
        GameTaskIdentity identity = request.identity();
        validate(result, RecognitionTaskType.LOCAL_TURN, identity.requestId(), identity.dealId(),
                identity.generation());
        if (result.status() == RecognitionStatus.FAILED) {
            return new LocalTurnRecognitionPort.Failed(identity, failure(result));
        }
        Map<String, List<String>> wireActions = Objects.requireNonNull(result.actionsBySeat(),
                "本方回合成功结果缺少两侧动作");
        Map<Seat, PlayAction> actions = new LinkedHashMap<>();
        wireActions.forEach((seat, cards) -> {
            Seat decodedSeat = decodeSeat(seat);
            if (decodedSeat == request.localSeat()) {
                throw new IllegalArgumentException("本方回合两侧动作不能包含本方座位");
            }
            actions.put(decodedSeat, decodeAction(cards));
        });
        return new LocalTurnRecognitionPort.Ready(identity,
                decodeCards(result.currentHand(), "本方当前手牌"), actions,
                Objects.requireNonNull(result.observedAt(), "本方回合成功结果缺少观察时间"));
    }

    /** 校验并解码只确认当前本方回合已离开的最终结果。 */
    public TurnEndRecognitionPort.Result decode(TurnEndRecognitionPort.Request request,
                                                RecognitionResult result) {
        GameTaskIdentity identity = request.identity();
        validate(result, RecognitionTaskType.TURN_END, identity.requestId(), identity.dealId(),
                identity.generation());
        if (result.status() == RecognitionStatus.FAILED) {
            return new TurnEndRecognitionPort.Failed(identity, failure(result));
        }
        return new TurnEndRecognitionPort.Ended(identity,
                Objects.requireNonNull(result.observedAt(), "回合结束成功结果缺少观察时间"));
    }

    /** 校验并解码结算旁路最终结果。 */
    public SettlementWatchPort.Result decode(SettlementWatchPort.Request request,
                                             RecognitionResult result) {
        GameTaskIdentity identity = request.identity();
        validate(result, RecognitionTaskType.SETTLEMENT, identity.requestId(), identity.dealId(),
                identity.generation());
        if (result.status() == RecognitionStatus.FAILED) {
            return new SettlementWatchPort.Failed(identity, failure(result));
        }
        return new SettlementWatchPort.Detected(identity,
                Objects.requireNonNull(result.observedAt(), "结算成功结果缺少观察时间"));
    }

    private RecognitionSubmit submit(RecognitionTaskType type, GameTaskIdentity identity,
                                     long deadlineMs, String entryMode, String localSeat,
                                     List<String> actors, String turnEntryMode) {
        return submit(type, identity.requestId(), identity.dealId(), identity.generation(),
                deadlineMs, entryMode, localSeat, actors, turnEntryMode, null, null);
    }

    private RecognitionSubmit submit(RecognitionTaskType type, String requestId, String dealId,
                                     Long generation, long deadlineMs, String entryMode,
                                     String localSeat, List<String> actors, String turnEntryMode) {
        return submit(type, requestId, dealId, generation, deadlineMs, entryMode, localSeat,
                actors, turnEntryMode, null, null);
    }

    private RecognitionSubmit submit(RecognitionTaskType type, GameTaskIdentity identity,
                                     long deadlineMs, String entryMode, String localSeat,
                                     List<String> actors, String turnEntryMode,
                                     List<String> baselineHand,
                                     Map<String, List<String>> baselineActionsBySeat) {
        return submit(type, identity.requestId(), identity.dealId(), identity.generation(),
                deadlineMs, entryMode, localSeat, actors, turnEntryMode, baselineHand,
                baselineActionsBySeat);
    }

    private RecognitionSubmit submit(RecognitionTaskType type, String requestId, String dealId,
                                     Long generation, long deadlineMs, String entryMode,
                                     String localSeat, List<String> actors, String turnEntryMode,
                                     List<String> baselineHand,
                                     Map<String, List<String>> baselineActionsBySeat) {
        return new RecognitionSubmit(CONTRACT_VERSION, RecognitionMessageType.SUBMIT, type,
                requestId, dealId, generation, deadlineMs, entryMode, localSeat, actors,
                turnEntryMode, baselineHand, baselineActionsBySeat);
    }

    private void validate(RecognitionResult result, RecognitionTaskType taskType,
                          String requestId, String dealId, Long generation) {
        Objects.requireNonNull(result, "识别结果不能为空");
        if (!CONTRACT_VERSION.equals(result.contractVersion())) {
            throw new IllegalArgumentException("识别结果协议版本不匹配");
        }
        if (result.taskType() != taskType
                || !requestId.equals(result.requestId())
                || !Objects.equals(dealId, result.dealId())
                || !Objects.equals(generation, result.generation())) {
            throw new IllegalArgumentException("识别结果任务身份不匹配");
        }
    }

    private RecognitionFailure failure(RecognitionResult result) {
        try {
            RecognitionFailureCode code = RecognitionFailureCode.valueOf(
                    result.errorCode().toUpperCase(Locale.ROOT));
            return new RecognitionFailure(code, result.errorMessage());
        } catch (RuntimeException error) {
            return new RecognitionFailure(RecognitionFailureCode.INVALID_RESULT,
                    "Python CV 返回未知失败分类");
        }
    }

    private CardSet decodeCards(List<String> symbols, String fieldName) {
        Objects.requireNonNull(symbols, fieldName + "不能为空");
        CardSet cards;
        try {
            cards = new CardSet(symbols.stream()
                    .map(CardRank::fromSymbol)
                    .map(Card::new)
                    .toList());
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException(fieldName + "包含未知牌面", error);
        }
        Map<CardRank, Long> counts = cards.cards().stream().collect(
                java.util.stream.Collectors.groupingBy(Card::rank,
                        () -> new java.util.EnumMap<>(CardRank.class),
                        java.util.stream.Collectors.counting()));
        boolean exceedsDeck = counts.entrySet().stream().anyMatch(entry ->
                entry.getValue() > (entry.getKey() == CardRank.SMALL_JOKER
                        || entry.getKey() == CardRank.BIG_JOKER ? 1 : 4));
        if (exceedsDeck) {
            throw new IllegalArgumentException(fieldName + "超过单副牌点数容量");
        }
        return cards;
    }

    private PlayAction decodeAction(List<String> symbols) {
        CardSet cards = decodeCards(symbols, "玩家动作");
        return cards.isEmpty() ? new PlayAction.Pass() : new PlayAction.Play(cards);
    }

    private List<String> encodeCards(CardSet cards) {
        return Objects.requireNonNull(cards, "牌集合不能为空").cards().stream()
                .map(card -> card.rank().symbol())
                .toList();
    }

    private List<String> encodeAction(PlayAction action) {
        Objects.requireNonNull(action, "动作不能为空");
        if (action instanceof PlayAction.Pass) {
            return List.of();
        }
        if (action instanceof PlayAction.Play play) {
            return encodeCards(play.cards());
        }
        throw new IllegalArgumentException("未知动作类型: " + action.getClass().getName());
    }

    private String encodeSeat(Seat seat) {
        return switch (Objects.requireNonNull(seat, "座位不能为空")) {
            case LANDLORD -> "landlord";
            case LANDLORD_DOWN -> "landlord_down";
            case LANDLORD_UP -> "landlord_up";
        };
    }

    private Seat decodeSeat(String value) {
        return switch (Objects.requireNonNull(value, "识别座位不能为空")) {
            case "landlord" -> Seat.LANDLORD;
            case "landlord_down" -> Seat.LANDLORD_DOWN;
            case "landlord_up" -> Seat.LANDLORD_UP;
            default -> throw new IllegalArgumentException("未知识别座位: " + value);
        };
    }

    private PreplayAction decodePreplayAction(String value) {
        return switch (Objects.requireNonNull(value, "局前动作不能为空")) {
            case "call" -> PreplayAction.CALL;
            case "no_call" -> PreplayAction.NO_CALL;
            case "rob" -> PreplayAction.ROB;
            case "no_rob" -> PreplayAction.NO_ROB;
            case "double" -> PreplayAction.DOUBLE;
            case "super_double" -> PreplayAction.SUPER_DOUBLE;
            case "no_double" -> PreplayAction.NO_DOUBLE;
            default -> throw new IllegalArgumentException("未知局前动作: " + value);
        };
    }
}
