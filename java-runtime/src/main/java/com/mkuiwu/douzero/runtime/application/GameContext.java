package com.mkuiwu.douzero.runtime.application;

import com.mkuiwu.douzero.runtime.application.model.GameTaskIdentity;
import com.mkuiwu.douzero.runtime.application.port.DealRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.LocalTurnRecognitionPort;
import com.mkuiwu.douzero.runtime.application.port.TurnEndRecognitionPort;
import com.mkuiwu.douzero.runtime.domain.CardSet;
import com.mkuiwu.douzero.runtime.domain.GamePhase;
import com.mkuiwu.douzero.runtime.domain.GameSnapshot;
import com.mkuiwu.douzero.runtime.domain.PlayAction;
import com.mkuiwu.douzero.runtime.domain.PlayRecord;
import com.mkuiwu.douzero.runtime.domain.Player;
import com.mkuiwu.douzero.runtime.domain.Seat;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 单局 Java 权威业务状态；只接受语义识别结果，不保存画面、ROI 或模型表示。
 *
 * <p>所有可变状态都只在同步方法内更新。本方手牌差和对手动作先在副本上完整校验，
 * 成功后才一次提交，避免识别缺失时产生半条历史。</p>
 */
public final class GameContext {
    /** 当前牌局标识，一局内不可变化。 */
    private final String dealId;

    /** 当前上下文代次，用于隔离上一局或已替换上下文的迟到结果。 */
    private final long generation;

    /** 本方在地主坐标系中的固定座位。 */
    private final Seat localSeat;

    /** 已确认的三张底牌。 */
    private final CardSet bottomCards;

    /** 已签发的本方回合请求标识；无论成功、失败或被替代，局内都不得复用。 */
    private final Set<String> issuedRequestIds = new HashSet<>();

    /** 已成功归并的本方回合请求标识，用于保证重复回调不重复写历史。 */
    private final Set<String> appliedRequestIds = new HashSet<>();

    /** 上一次成功生成建议时确认的本方手牌，也是下一次推导本方动作的基线。 */
    private CardSet lastAdviceHand;

    /** 上一次成功生成建议时两侧已稳定读取的动作，用于后续回合结束探测的画面基线。 */
    private Map<Seat, PlayAction> lastAdviceActions = Map.of();

    /** 从地主首个动作开始按固定座位顺序保存的完整动作历史。 */
    private List<PlayRecord> history;

    /** 是否已经成功归并过至少一次本方回合快照。 */
    private boolean hasAdviceSnapshot;

    /** 当前唯一有效的本方回合任务标识；新任务会使旧任务结果失效。 */
    private String activeRequestId;

    /** 当前任务要求 Python 必须稳定读取的对手座位；未点名的一侧当作还没出过，不参与失败。 */
    private Set<Seat> activeRequiredActors = Set.of();

    /** 当前唯一有效的回合结束探测任务标识；确认旧回合离开后才能请求下一次本方回合。 */
    private String activeTurnEndRequestId;

    /** 上下文是否已进入不可恢复的统一收口。 */
    private boolean closed;

    private GameContext(DealRecognitionPort.Recognized deal) {
        GameTaskIdentity identity = deal.identity();
        this.dealId = identity.dealId();
        this.generation = identity.generation();
        this.localSeat = deal.localSeat();
        this.bottomCards = deal.bottomCards();
        this.lastAdviceHand = deal.hand();

        this.history = initialHistoryFromDeal(deal);
    }

    /** 从完整牌局初始化事实建立唯一权威上下文。 */
    public static GameContext from(DealRecognitionPort.Recognized deal) {
        return new GameContext(Objects.requireNonNull(deal, "牌局初始化结果不能为空"));
    }

    /**
     * 创建当前唯一有效的本方回合识别请求，并由 Java 状态推导必须读取的对手座位。
     *
     * <p>Deal 已经完成“谁先出牌即地主”的角色定位，并把农民视角下的地主首手写入初始历史。
     * 因此首次请求只点名初始历史之后、轮到本方之前尚未入史的对手；不会重读已经确认的
     * 地主首手。后续请求先由本方手牌差归并本方动作，再点名两名对手，按座位环迭代。</p>
     *
     * @param requestId 单次本方回合任务标识；一旦签发就不能在局内复用
     * @param deadlineMs Python CV 交付完整快照的最长时间，单位为毫秒
     * @return 不携带历史、上次手牌或视觉实现字段的语义识别请求
     */
    public synchronized LocalTurnRecognitionPort.Request prepareLocalTurn(
            String requestId,
            long deadlineMs
    ) {
        requireOpen();
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("本方回合任务标识不能为空");
        }
        if (issuedRequestIds.contains(requestId)) {
            throw new IllegalArgumentException("已签发的本方回合任务标识不能复用");
        }
        Set<Seat> requiredActors = requiredActorsForNextSnapshot();
        LocalTurnRecognitionPort.Request request = new LocalTurnRecognitionPort.Request(
                new GameTaskIdentity(requestId, dealId, generation),
                localSeat,
                requiredActors,
                LocalTurnRecognitionPort.TurnEntryMode.ACCEPT_CURRENT_STABLE_TURN,
                deadlineMs
        );
        issuedRequestIds.add(requestId);
        activeRequestId = requestId;
        activeRequiredActors = requiredActors;
        return request;
    }

    /**
     * 创建建议后当前本方回合的结束探测请求；该请求只验证边界，不写入动作历史。
     *
     * @param requestId 单次回合结束任务标识；一旦签发就不能在局内复用
     * @param deadlineMs Python CV 等待当前本方回合离开的最长时间，单位为毫秒
     * @return 携带上一次建议语义快照的回合结束探测请求
     */
    public synchronized TurnEndRecognitionPort.Request prepareTurnEnd(
            String requestId,
            long deadlineMs
    ) {
        requireOpen();
        if (!hasAdviceSnapshot || activeRequestId != null) {
            throw failure(FailureReason.INVALID_TURN_TRANSITION, "当前没有可结束的已建议本方回合");
        }
        if (requestId == null || requestId.isBlank()) {
            throw new IllegalArgumentException("回合结束任务标识不能为空");
        }
        if (issuedRequestIds.contains(requestId)) {
            throw new IllegalArgumentException("已签发的回合结束任务标识不能复用");
        }
        if (activeTurnEndRequestId != null) {
            throw failure(FailureReason.INVALID_TURN_TRANSITION, "当前本方回合已经在等待结束确认");
        }
        TurnEndRecognitionPort.Request request = new TurnEndRecognitionPort.Request(
                new GameTaskIdentity(requestId, dealId, generation), localSeat,
                lastAdviceHand, lastAdviceActions, deadlineMs);
        issuedRequestIds.add(requestId);
        activeTurnEndRequestId = requestId;
        return request;
    }

    /**
     * 接收当前本方回合已经离开的边界事实；不修改手牌或历史。
     *
     * @param ended Python CV 返回的当前回合结束确认
     */
    public synchronized void completeTurnEnd(TurnEndRecognitionPort.Ended ended) {
        requireOpen();
        Objects.requireNonNull(ended, "回合结束结果不能为空");
        validateDealIdentity(ended.identity());
        if (!ended.identity().requestId().equals(activeTurnEndRequestId)) {
            throw failure(FailureReason.STALE_REQUEST, "回合结束结果不是当前有效任务");
        }
        activeTurnEndRequestId = null;
    }

    /**
     * 原子归并一个本方回合快照；重复成功结果只返回当前状态，不再次写入历史。
     *
     * @param ready Python CV 交付的当前手牌和按语义座位索引的两侧动作
     * @return 本次是否实际提交以及提交后的模型可用快照
     */
    public synchronized ReconciliationResult reconcile(LocalTurnRecognitionPort.Ready ready) {
        requireOpen();
        Objects.requireNonNull(ready, "本方回合结果不能为空");
        validateDealIdentity(ready.identity());
        if (appliedRequestIds.contains(ready.identity().requestId())) {
            return new ReconciliationResult(false, snapshot());
        }
        if (!ready.identity().requestId().equals(activeRequestId)) {
            throw failure(FailureReason.STALE_REQUEST, "本方回合结果不是当前有效任务");
        }
        if (ready.actionsBySeat().containsKey(localSeat)) {
            throw failure(FailureReason.UNEXPECTED_ACTION, "两侧动作结果不能包含本方座位");
        }
        if (!ready.actionsBySeat().keySet().containsAll(activeRequiredActors)) {
            throw failure(FailureReason.MISSING_REQUIRED_ACTION, "缺少 Java 当前要求的对手动作");
        }

        List<PlayRecord> candidateHistory = new ArrayList<>(history);
        if (hasAdviceSnapshot) {
            requireExpectedSeat(candidateHistory, localSeat);
            CardSet removedCards = removeCards(lastAdviceHand, ready.currentHand());
            PlayAction localAction = removedCards.isEmpty()
                    ? new PlayAction.Pass()
                    : new PlayAction.Play(removedCards);
            append(candidateHistory, localSeat, localAction);
        } else {
            CardSet unexpectedChange = removeCards(lastAdviceHand, ready.currentHand());
            if (!unexpectedChange.isEmpty() || ready.currentHand().size() != lastAdviceHand.size()) {
                throw failure(FailureReason.INVALID_HAND_TRANSITION,
                        "首次建议前本方手牌不能发生变化");
            }
        }

        int consumedOpponents = 0;
        while (expectedSeat(candidateHistory) != localSeat) {
            if (consumedOpponents == 2) {
                throw failure(FailureReason.INVALID_SEAT_ORDER, "对手动作超过两个仍未轮到本方");
            }
            Seat expected = expectedSeat(candidateHistory);
            PlayAction action = ready.actionsBySeat().get(expected);
            if (action == null) {
                throw failure(FailureReason.MISSING_REQUIRED_ACTION,
                        "缺少期望座位的对手动作: " + expected);
            }
            append(candidateHistory, expected, action);
            consumedOpponents++;
        }

        validateHistory(candidateHistory);
        history = List.copyOf(candidateHistory);
        lastAdviceHand = ready.currentHand();
        lastAdviceActions = ready.actionsBySeat();
        hasAdviceSnapshot = true;
        appliedRequestIds.add(ready.identity().requestId());
        activeRequestId = null;
        activeRequiredActors = Set.of();
        return new ReconciliationResult(true, snapshot());
    }

    /** 返回当前 Java 权威状态的不可变语义快照。 */
    public synchronized GameSnapshot snapshot() {
        return new GameSnapshot(
                dealId,
                GamePhase.PLAYING,
                localSeat,
                lastAdviceHand,
                bottomCards,
                history
        );
    }

    /** 幂等关闭当前局；关闭后不再签发任务或接受任何识别结果。 */
    public synchronized void close() {
        if (closed) {
            return;
        }
        closed = true;
        activeRequestId = null;
        activeRequiredActors = Set.of();
        activeTurnEndRequestId = null;
    }

    /** 返回当前局是否已被幂等收口。 */
    public synchronized boolean isClosed() {
        return closed;
    }

    private Set<Seat> requiredActorsForNextSnapshot() {
        /*
         * 首次请求以 Deal 已建立的历史游标为准：农民的地主首手已经入史，不能再要求
         * Python 重读同一结果区。后续请求则先由本方手牌差补上本方动作，故从本方下家开始。
         */
        Seat expected = hasAdviceSnapshot ? localSeat.next() : expectedSeat(history);
        return actorsBeforeLocalSeat(expected);
    }

    /**
     * 把 Deal 的首轮定位事实转换为唯一的开局历史。
     *
     * <p>本家为农民时，Python 已通过地主首手所在的左右结果区确定相对座位，且该首手是
     * 后续所有回合推导的第一个权威动作；本家为地主时尚未发生公开动作。</p>
     */
    private static List<PlayRecord> initialHistoryFromDeal(DealRecognitionPort.Recognized deal) {
        if (deal.localSeat() == Seat.LANDLORD) {
            return List.of();
        }
        return List.of(new PlayRecord(
                new Player(Seat.LANDLORD),
                new PlayAction.Play(deal.landlordOpeningPlay())
        ));
    }

    /** 从已知的下一行动座位开始，收集轮到本方前尚未确认的对手座位。 */
    private Set<Seat> actorsBeforeLocalSeat(Seat expected) {
        LinkedHashSet<Seat> required = new LinkedHashSet<>();
        while (expected != localSeat) {
            required.add(expected);
            expected = expected.next();
        }
        return Set.copyOf(required);
    }

    private void validateDealIdentity(GameTaskIdentity identity) {
        if (!dealId.equals(identity.dealId()) || generation != identity.generation()) {
            throw failure(FailureReason.STALE_IDENTITY, "本方回合结果不属于当前牌局代次");
        }
    }

    private CardSet removeCards(CardSet previous, CardSet current) {
        try {
            return previous.removedTo(current);
        } catch (IllegalArgumentException error) {
            throw failure(FailureReason.INVALID_HAND_TRANSITION, error.getMessage());
        }
    }

    private void requireExpectedSeat(List<PlayRecord> records, Seat seat) {
        if (expectedSeat(records) != seat) {
            throw failure(FailureReason.INVALID_SEAT_ORDER, "动作历史尚未轮到本方");
        }
    }

    private void append(List<PlayRecord> records, Seat seat, PlayAction action) {
        if (expectedSeat(records) != seat) {
            throw failure(FailureReason.INVALID_SEAT_ORDER, "动作座位与 Java 期望顺序不一致");
        }
        if (action instanceof PlayAction.Pass && !hasActiveLead(records)) {
            throw failure(FailureReason.PASS_WITHOUT_ACTIVE_LEAD, "当前墩没有有效领出，任何座位都不能不出");
        }
        records.add(new PlayRecord(new Player(seat), action));
    }

    private void validateHistory(List<PlayRecord> records) {
        Seat expected = Seat.LANDLORD;
        for (PlayRecord record : records) {
            if (record.player().seat() != expected) {
                throw failure(FailureReason.INVALID_SEAT_ORDER, "动作历史座位不连续");
            }
            expected = expected.next();
        }
        if (expected != localSeat) {
            throw failure(FailureReason.INVALID_SEAT_ORDER, "归并后尚未回到本方座位");
        }
    }

    private Seat expectedSeat(List<PlayRecord> records) {
        return Seat.values()[records.size() % Seat.values().length];
    }

    private boolean hasActiveLead(List<PlayRecord> records) {
        int trailingPasses = 0;
        for (int index = records.size() - 1; index >= 0; index--) {
            if (records.get(index).action() instanceof PlayAction.Pass) {
                trailingPasses++;
            } else {
                break;
            }
        }
        if (trailingPasses >= 2) {
            return false;
        }
        for (int index = records.size() - 1; index >= 0; index--) {
            if (records.get(index).action() instanceof PlayAction.Play) {
                return true;
            }
        }
        return false;
    }

    private void requireOpen() {
        if (closed) {
            throw failure(FailureReason.CONTEXT_CLOSED, "牌局上下文已关闭");
        }
    }

    private ReconciliationException failure(FailureReason reason, String detail) {
        return new ReconciliationException(reason, detail);
    }

    /**
     * 一次归并的提交结果。
     *
     * @param applied true 表示本次首次成功提交；false 表示相同 requestId 的幂等重复结果
     * @param snapshot 提交后可用于 DecisionPort 的完整权威快照
     */
    public record ReconciliationResult(boolean applied, GameSnapshot snapshot) {
        public ReconciliationResult {
            Objects.requireNonNull(snapshot, "归并结果快照不能为空");
        }
    }

    /** 归并失败的稳定业务分类；失败时上下文状态保持不变。 */
    public enum FailureReason {
        /** 当前牌局已收口，不再签发或接受任务。 */
        CONTEXT_CLOSED,
        /** 结果的牌局标识或代次已经失效。 */
        STALE_IDENTITY,
        /** 结果不是当前唯一有效的本方回合任务。 */
        STALE_REQUEST,
        /** 当前手牌不是上次确认手牌的合法多重子集。 */
        INVALID_HAND_TRANSITION,
        /** 回合快照与回合结束边界任务的调用顺序不合法。 */
        INVALID_TURN_TRANSITION,
        /** 必须稳定交付的期望座位动作缺失。 */
        MISSING_REQUIRED_ACTION,
        /** 识别结果携带了不应出现的本方动作。 */
        UNEXPECTED_ACTION,
        /** 当前墩没有待跟的有效领出，却观察到不出。 */
        PASS_WITHOUT_ACTIVE_LEAD,
        /** 动作历史没有按地主、地主下家、地主上家连续推进。 */
        INVALID_SEAT_ORDER
    }

    /** 携带稳定失败分类的归并异常。 */
    public static final class ReconciliationException extends IllegalStateException {
        /** 可供编排层映射等待原因的稳定失败分类。 */
        private final FailureReason reason;

        private ReconciliationException(FailureReason reason, String detail) {
            super(detail);
            this.reason = Objects.requireNonNull(reason, "归并失败分类不能为空");
        }

        /** 返回稳定失败分类，调用方不得把该失败修补成成功状态。 */
        public FailureReason reason() {
            return reason;
        }
    }
}
