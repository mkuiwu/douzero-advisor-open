import type {
  DesktopEvent,
  DesktopPhaseChangedEvent,
  DesktopPlayAdviceEvent,
  ListenerEnvelope,
} from "../../shared/contracts";

/** 旧 Python tracker 对外发布的稳定阶段。 */
export type TrackerPhase = "waiting_deal" | "tracking" | "complete" | "uncertain";
/** 当前本方在地主坐标系中的语义座位。 */
export type Seat = "landlord" | "landlord_down" | "landlord_up";
/** UI 时间线事件的严重程度。 */
export type EventLevel = "good" | "info" | "warn" | "error";

/** Vue 展示层消费的一份不可变牌局快照。 */
export interface AdvisorSnapshot {
  /** 当前 Java 或旧 listener 已锁定的牌局标识；尚无牌局时为空。 */
  dealId: string | null;
  /** 当前 tracker 阶段。 */
  phase: TrackerPhase;
  /** 当前无法形成可信建议的原因；正常跟踪时为空。 */
  reason: string | null;
  /** 本方语义座位；尚未锁定角色时为空。 */
  role: Seat | null;
  /** 当前稳定确认的本方手牌，使用公开牌点字符串。 */
  myCards: readonly string[];
  /** 当前牌墩最后一个有效领出；新牌墩时为空。 */
  lastMove: readonly string[];
  /** 相对本方左侧玩家最近一次动作；null 表示本局尚未收到该玩家动作。 */
  leftMove: readonly string[] | null;
  /** 相对本方右侧玩家最近一次动作；null 表示本局尚未收到该玩家动作。 */
  rightMove: readonly string[] | null;
  /** 当前动作轮次，从 1 开始；尚无权威历史时为空。 */
  round: number | null;
  /** 三个语义座位的剩余手牌数量。 */
  remainingCards: Readonly<Record<string, number>>;
  /** 模型推荐动作；null 表示没有建议，空数组明确表示不出。 */
  recommendation: readonly string[] | null;
  /** 当前建议生命周期；pending 表示本方回合已开始但模型尚未返回。 */
  recommendationStatus: "idle" | "pending" | "ready" | "unavailable";
  /** 每次清空或产生新建议时递增，用于触发 UI 到达动画。 */
  recommendationRevision: number;
  /** 识别阶段耗时，单位为毫秒；未提供时为空。 */
  recognitionMs: number | null;
  /** 正式建议耗时，单位为毫秒；未提供时为空。 */
  adviceMs: number | null;
  /** 本方回合开始的 Java 事件时间，仅用于计算本次正式建议的端到端耗时。 */
  adviceStartedAtMs: number | null;
  /** 按牌点统计的牌桌外剩余张数。 */
  remainingByRank: Readonly<Record<string, number>>;
}

/** UI 最近事件与诊断日志共用的结构化消息。 */
export interface LiveEvent {
  /** 事件所属的捕获、识别、牌局或建议阶段。 */
  stage: string;
  /** 面向用户的短标题。 */
  title: string;
  /** 不包含图片或敏感内容的诊断摘要。 */
  detail: string;
  /** 事件在 UI 中使用的严重程度。 */
  level: EventLevel;
  /** 事件产生时间，使用 ISO-8601 格式。 */
  timestamp: string;
}

/** 局前建议面板最近一次稳定建议。 */
export interface PreplayView {
  /** 当前局前动作的中文展示文本。 */
  action: string;
  /** 模型原始评分；不是概率或胜率。 */
  score: number | null;
  /** 最近一次局前事件的完整摘要。 */
  detail: string;
  /** 当前局前阶段开始的 Java 事件时间，仅用于计算局前流程耗时。 */
  startedAtMs: number | null;
  /** 当前局前建议从进入局前阶段到发布建议的耗时，单位为毫秒。 */
  adviceMs: number | null;
  /** 每次收到局前建议时递增，用于触发同文案建议的到达动画。 */
  revision: number;
}

/** Electron renderer 内唯一的桌面运行状态。 */
export interface DesktopRuntimeState {
  /** 当前实际使用的运行时传输；Java WS 未配置时显式回退旧 Python JSONL。 */
  transport: "python_jsonl" | "java_ws";
  /** listener 是否仍被主进程视为运行中。 */
  running: boolean;
  /** 当前展示的可信牌局快照。 */
  snapshot: AdvisorSnapshot;
  /** 最近事件，最新一条位于数组首位。 */
  events: readonly LiveEvent[];
  /** 有界诊断日志，最新一条位于数组首位。 */
  logs: readonly LiveEvent[];
  /** 捕获、识别、tracker 和 listener 的最近状态。 */
  diagnostics: Readonly<Record<string, string>>;
  /** 最近一次局前建议。 */
  preplay: PreplayView;
}

const NORMAL_WAIT_REASONS = new Set([
  "waiting_for_game",
  "waiting_for_confirmations",
  "waiting_for_local_turn_ui",
  "waiting_for_result_animation",
]);

/** 构造不会保留任何旧建议的初始桌面状态。 */
export function createInitialRuntimeState(): DesktopRuntimeState {
  return {
    transport: "python_jsonl",
    running: false,
    snapshot: {
      dealId: null,
      phase: "uncertain",
      reason: "waiting_for_game",
      role: null,
      myCards: [],
      lastMove: [],
      leftMove: null,
      rightMove: null,
      round: null,
      remainingCards: {},
      recommendation: null,
      recommendationStatus: "idle",
      recommendationRevision: 0,
      recognitionMs: null,
      adviceMs: null,
      adviceStartedAtMs: null,
      remainingByRank: {},
    },
    events: [],
    logs: [],
    diagnostics: {
      listener: "未启动",
      capture: "未启动",
      recognition: "未启动",
      tracker: "未启动",
    },
    preplay: createInitialPreplayView(),
  };
}

/** 将一条 Electron 消息折叠进桌面状态；非法输出只能成为诊断，不能产生建议。 */
export function consumeListenerEnvelope(
  state: DesktopRuntimeState,
  envelope: ListenerEnvelope,
): DesktopRuntimeState {
  if (envelope.kind === "desktop_event") {
    return consumeDesktopEvent({ ...state, transport: "java_ws" }, envelope.event);
  }
  if (envelope.kind === "started") {
    return recordEvent(
      { ...state, running: true, transport: envelope.transport ?? state.transport },
      event("startup", "监听器启动", envelope.line, "good", envelope.timestamp),
    );
  }
  if (envelope.kind === "stopped") {
    return recordEvent(
      { ...state, running: false, snapshot: clearRecommendation(state.snapshot) },
      event("listener", "监听器停止", envelope.line, "info", envelope.timestamp),
    );
  }
  if (envelope.kind === "exit") {
    const level = envelope.exitCode === 0 ? "info" : "error";
    return recordEvent(
      { ...state, running: false, snapshot: clearRecommendation(state.snapshot) },
      event("listener", "监听器退出", envelope.line, level, envelope.timestamp),
    );
  }
  if (envelope.kind === "stderr") {
    return recordEvent(
      state,
      event("listener", "监听器错误输出", envelope.line, "error", envelope.timestamp),
    );
  }
  return consumeStdout(state, envelope.line, envelope.timestamp);
}

function consumeStdout(
  state: DesktopRuntimeState,
  line: string,
  timestamp: string,
): DesktopRuntimeState {
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    if (!line.trim()) return state;
    const level: EventLevel = line.toLowerCase().includes("error") ? "error" : "info";
    return recordEvent(state, event("listener", "监听器输出", line.trim(), level, timestamp));
  }
  if (!isRecord(raw)) return state;

  if (isRecord(raw.snapshot)) {
    const snapshot = parseSnapshot(raw.snapshot);
    return recordEvent(
      { ...state, snapshot },
      snapshotEvent(snapshot, timestamp),
    );
  }

  const advice = isRecord(raw.advice) ? raw.advice : raw;
  if (Object.hasOwn(advice, "recommendation") && typeof advice.role === "string") {
    if (state.snapshot.phase !== "tracking") return state;
    const timings = isRecord(advice.timings_ms) ? advice.timings_ms : {};
    const recommendation = stringList(advice.recommendation);
    const snapshot: AdvisorSnapshot = {
      ...state.snapshot,
      recommendation,
      recommendationStatus: "ready",
      recommendationRevision: state.snapshot.recommendationRevision + 1,
      recognitionMs: numberOrNull(timings.recognition),
      adviceMs: numberOrNull(advice.elapsed_ms),
    };
    const legalCount = Array.isArray(advice.legal_actions) ? advice.legal_actions.length : null;
    let detail = recommendation.length ? recommendation.join(" ") : "不出";
    if (legalCount !== null) detail += ` · ${legalCount} 个合法动作`;
    return recordEvent(
      { ...state, snapshot },
      event("advice", "出牌建议", detail, "good", timestamp),
    );
  }

  const transition = latestConfirmedAction(raw);
  if (transition) return consumeConfirmedAction(state, transition, timestamp);
  if (isRecord(raw.action)) return consumeConfirmedAction(state, raw.action, timestamp);

  const derived = payloadEvent(raw, timestamp);
  if (!derived) return state;
  let next = state;
  if (derived.stage === "preplay" && isRecord(raw.preplay_advice)) {
    next = {
      ...state,
      preplay: {
        action: preplayLabel(String(raw.preplay_advice.action ?? "unavailable")),
        score: numberOrNull(raw.preplay_advice.score),
        detail: derived.detail,
        startedAtMs: null,
        adviceMs: null,
        revision: state.preplay.revision + 1,
      },
    };
  }
  return recordEvent(next, derived);
}

function parseSnapshot(raw: Record<string, unknown>): AdvisorSnapshot {
  const phase = isTrackerPhase(raw.phase) ? raw.phase : "uncertain";
  const role = isSeat(raw.role) ? raw.role : null;
  return {
    dealId: typeof raw.deal_id === "string" && raw.deal_id ? raw.deal_id : null,
    phase,
    reason: typeof raw.reason === "string" && raw.reason ? raw.reason : null,
    role,
    myCards: stringList(raw.my_cards),
    lastMove: stringList(raw.last_move),
    leftMove: null,
    rightMove: null,
    round: null,
    remainingCards: numberRecord(raw.remaining_cards),
    recommendation: null,
    recommendationStatus: "idle",
    recommendationRevision: 0,
    recognitionMs: null,
    adviceMs: null,
    adviceStartedAtMs: null,
    remainingByRank: numberRecord(raw.remaining_by_rank),
  };
}

function consumeDesktopEvent(state: DesktopRuntimeState, message: DesktopEvent): DesktopRuntimeState {
  if (message.type === "READY") {
    const detail = message.orchestrationEnabled
      ? "Java Runtime 已连接，权威状态机已启用"
      : "Java Runtime 网关已连接，但权威状态机尚未启用";
    return recordEvent(
      {
        ...state,
        running: true,
        diagnostics: { ...state.diagnostics, listener: detail },
      },
      event("runtime", "Java Runtime 就绪", detail, message.orchestrationEnabled ? "good" : "warn", message.emittedAt),
    );
  }
  if (message.type === "PHASE_CHANGED") {
    const startsNewGame = message.phase === "WAIT_NEW_GAME"
      || (message.phase === "PREPLAY" && message.dealId !== state.snapshot.dealId);
    const snapshot = startsNewGame
      ? createNewGameSnapshot(state.snapshot.recommendationRevision, message)
      : {
          ...state.snapshot,
          dealId: message.dealId || null,
          phase: javaTrackerPhase(message.phase),
          reason: null,
          recommendation: null,
          leftMove: null,
          rightMove: null,
          round: null,
          recommendationStatus: "idle" as const,
          recommendationRevision: state.snapshot.recommendationRevision + 1,
          adviceMs: null,
          adviceStartedAtMs: null,
        };
    const preplay = startsNewGame
      ? createInitialPreplayView(message.phase === "PREPLAY" ? timestampMs(message.emittedAt) : null)
      : state.preplay;
    return recordEvent(
      {
        ...state,
        snapshot,
        diagnostics: { ...state.diagnostics, tracker: `Java ${message.phase}` },
        preplay,
      },
      event("phase", "Java 阶段变化", `${message.phase}${message.dealId ? ` · ${message.dealId}` : ""}`, "info", message.emittedAt),
    );
  }
  if (message.type === "PLAY_TURN_STARTED") {
    const snapshot: AdvisorSnapshot = {
      ...state.snapshot,
      dealId: message.dealId,
      phase: "tracking",
      reason: null,
      role: seatFromJava(message.localSeat),
      recommendation: null,
      recommendationStatus: "pending",
      recommendationRevision: state.snapshot.recommendationRevision + 1,
      recognitionMs: null,
      adviceMs: null,
      adviceStartedAtMs: timestampMs(message.emittedAt),
    };
    return recordEvent(
      { ...state, snapshot },
      event("advice", "轮到本方，正在生成建议", "上一条建议已清空", "info", message.emittedAt),
    );
  }
  if (message.type === "PREPLAY_ADVICE") {
    const action = preplayLabel(message.action.toLowerCase());
    const detail = `${action} · 评分 ${message.score.toFixed(3)} · 阈值 ${message.threshold.toFixed(3)}`;
    const preplayStartedAtMs = state.preplay.startedAtMs;
    return recordEvent(
      {
        ...state,
        preplay: {
          action,
          score: message.score,
          detail,
          startedAtMs: preplayStartedAtMs,
          adviceMs: elapsedMs(preplayStartedAtMs, message.emittedAt),
          revision: state.preplay.revision + 1,
        },
        snapshot: { ...state.snapshot, dealId: message.dealId },
      },
      event("preplay", "Java 局前建议", detail, "good", message.emittedAt),
    );
  }
  if (message.type === "PLAY_ADVICE") {
    return consumeDesktopPlayAdvice(state, message);
  }
  const snapshot: AdvisorSnapshot = {
    ...state.snapshot,
    dealId: message.dealId,
    phase: "complete",
    reason: message.reason,
    recommendation: null,
    leftMove: null,
    rightMove: null,
    round: null,
    recommendationStatus: "idle",
    recommendationRevision: state.snapshot.recommendationRevision + 1,
  };
  return recordEvent(
    { ...state, snapshot },
    event("finish", "牌局已收口", finishReasonText(message.reason), "info", message.emittedAt),
  );
}

function consumeDesktopPlayAdvice(
  state: DesktopRuntimeState,
  message: DesktopPlayAdviceEvent,
): DesktopRuntimeState {
  const sideMoves = relativeMoves(message);
  const recommendation = message.resultKind === "RECOMMENDATION"
    ? message.recommendedAction?.cards ?? null
    : null;
  const snapshot: AdvisorSnapshot = {
    dealId: message.dealId,
    phase: "tracking",
    reason: message.resultKind === "UNAVAILABLE" ? message.failure || message.detail : null,
    role: seatFromJava(message.localSeat),
    myCards: message.hand,
    lastMove: message.lastMove,
    leftMove: sideMoves.leftMove,
    rightMove: sideMoves.rightMove,
    round: sideMoves.round,
    remainingCards: remainingCards(message),
    recommendation,
    recommendationStatus: message.resultKind === "RECOMMENDATION" ? "ready" : "unavailable",
    recommendationRevision: state.snapshot.recommendationRevision + 1,
    recognitionMs: null,
    adviceMs: elapsedMs(state.snapshot.adviceStartedAtMs, message.emittedAt),
    adviceStartedAtMs: null,
    remainingByRank: remainingByRank(message),
  };
  const detail = message.resultKind === "RECOMMENDATION"
    ? `${recommendation?.length ? recommendation.join(" ") : "不出"} · ${message.actionScores.length} 个候选动作`
    : `${message.failure}: ${message.detail}`;
  return recordEvent(
    { ...state, snapshot },
    event("advice", message.resultKind === "RECOMMENDATION" ? "Java 出牌建议" : "Java 建议暂不可用", detail, message.resultKind === "RECOMMENDATION" ? "good" : "warn", message.emittedAt),
  );
}

/** 从 Java 权威历史中提取相对本方左右两侧的最近动作和当前轮次。 */
function relativeMoves(message: DesktopPlayAdviceEvent): {
  leftMove: readonly string[] | null;
  rightMove: readonly string[] | null;
  round: number;
} {
  const seats = ["LANDLORD", "LANDLORD_DOWN", "LANDLORD_UP"] as const;
  const localIndex = seats.indexOf(message.localSeat);
  const leftSeat = seats[(localIndex + seats.length - 1) % seats.length]!;
  const rightSeat = seats[(localIndex + 1) % seats.length]!;
  return {
    leftMove: latestMoveForSeat(message.history, leftSeat),
    rightMove: latestMoveForSeat(message.history, rightSeat),
    round: Math.floor(message.history.length / seats.length) + 1,
  };
}

/** 返回指定座位最近一次动作；出牌返回牌面，不出返回空数组。 */
function latestMoveForSeat(
  history: DesktopPlayAdviceEvent["history"],
  seat: DesktopPlayAdviceEvent["localSeat"],
): readonly string[] | null {
  const item = [...history].reverse().find((entry) => entry.seat === seat);
  if (!item) return null;
  return item.action.pass ? [] : item.action.cards;
}

function remainingCards(message: DesktopPlayAdviceEvent): Readonly<Record<string, number>> {
  const counts: Record<string, number> = { landlord: 20, landlord_down: 17, landlord_up: 17 };
  for (const item of message.history) {
    const seat = seatFromJava(item.seat);
    counts[seat] = Math.max(0, (counts[seat] ?? 0) - item.action.cards.length);
  }
  return counts;
}

function remainingByRank(message: DesktopPlayAdviceEvent): Readonly<Record<string, number>> {
  const counts: Record<string, number> = {
    "3": 4, "4": 4, "5": 4, "6": 4, "7": 4, "8": 4, "9": 4, "10": 4,
    J: 4, Q: 4, K: 4, A: 4, "2": 4, X: 1, D: 1,
  };
  for (const card of message.hand) counts[card] = Math.max(0, (counts[card] ?? 0) - 1);
  for (const item of message.history) {
    for (const card of item.action.cards) counts[card] = Math.max(0, (counts[card] ?? 0) - 1);
  }
  return counts;
}

function seatFromJava(value: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP"): Seat {
  return value.toLowerCase() as Seat;
}

function javaTrackerPhase(value: DesktopPhaseChangedEvent["phase"]): TrackerPhase {
  if (value === "PLAYING") return "tracking";
  if (value === "SETTLEMENT") return "complete";
  return "waiting_deal";
}

function finishReasonText(reason: string): string {
  const labels: Record<string, string> = {
    SETTLEMENT_DETECTED: "已稳定识别结算页",
    STATE_TIMEOUT: "权威状态任务超时",
    RECOGNITION_FAILED: "识别结果已失信",
    OPERATOR_ABORTED: "Java Runtime 主动收口",
  };
  return labels[reason] ?? reason;
}

function consumeConfirmedAction(
  state: DesktopRuntimeState,
  action: Record<string, unknown>,
  timestamp: string,
): DesktopRuntimeState {
  const passed = action.passed === true;
  const cards = stringList(action.cards);
  const cardText = passed ? "不出" : cards.length ? cards.join(" ") : "已出牌";
  const position = String(action.position ?? action.seat ?? "未知位置");
  if (position === "self") {
    return recordEvent(
      { ...state, snapshot: clearRecommendation(state.snapshot) },
      event(
        "local_action",
        passed ? "本局不出已确认" : "本家出牌已确认",
        cardText,
        "good",
        timestamp,
      ),
    );
  }
  return recordEvent(
    state,
    event("action", "确认牌桌动作", `${position}：${cardText}`, "good", timestamp),
  );
}

function payloadEvent(raw: Record<string, unknown>, timestamp: string): LiveEvent | null {
  if (raw.status === "waiting") {
    const wait = isRecord(raw.wait) ? raw.wait : raw;
    const stage = String(wait.stage ?? "waiting");
    const reason = String(wait.reason ?? "等待下一帧");
    const geometryFailure = ["geometry", "resize", "client_rect", "1455x819"].some((marker) =>
      reason.toLowerCase().includes(marker),
    );
    const level: EventLevel = geometryFailure
      ? "error"
      : NORMAL_WAIT_REASONS.has(reason)
        ? "info"
        : "warn";
    return event(stage, stageTitle(stage), reason, level, timestamp);
  }
  if (isRecord(raw.observation)) {
    const cards = stringList(raw.observation.cards);
    const detail = cards.length ? cards.join(" ") : String(raw.observation.result ?? "待确认");
    const actor = String(raw.observation.position ?? raw.observation.seat ?? "未知位置");
    return event("tracker", "牌桌观察", `${actor}：${detail}`, "info", timestamp);
  }
  if (isRecord(raw.deal)) {
    const bottom = stringList(raw.deal.bottom_cards);
    return event("deal", "新牌局", `底牌：${bottom.length ? bottom.join(" ") : "—"}`, "good", timestamp);
  }
  if (isRecord(raw.preplay_advice)) {
    const action = preplayLabel(String(raw.preplay_advice.action ?? "unavailable"));
    const score = numberOrNull(raw.preplay_advice.score);
    const detail = score === null ? action : `${action} · 评分 ${score.toFixed(3)}`;
    return event("preplay", "局前建议", detail, "good", timestamp);
  }
  for (const [key, title] of [
    ["auto_preplay", "局前执行"],
    ["settlement_buttons", "结算按钮"],
    ["settlement_auto", "结算执行"],
    ["auto_settlement", "结算执行"],
  ] as const) {
    if (isRecord(raw[key])) return event(key, title, compactDetail(raw[key]), "info", timestamp);
  }
  if (isRecord(raw.deal_result)) {
    return event("tracker", "本局结果", compactDetail(raw.deal_result), "good", timestamp);
  }
  return null;
}

function snapshotEvent(snapshot: AdvisorSnapshot, timestamp: string): LiveEvent {
  if (snapshot.phase === "tracking") {
    return event(
      "tracker",
      "牌局已确认",
      `TRACKING · ${snapshot.role ?? "未知角色"}`,
      "good",
      timestamp,
    );
  }
  if (snapshot.phase === "complete") {
    return event("tracker", "牌局结束", "等待下一局", "good", timestamp);
  }
  if (snapshot.phase === "uncertain") {
    return event("tracker", "牌局状态不可靠", snapshot.reason ?? "uncertain", "error", timestamp);
  }
  return event("tracker", "等待开局", "尚未建立牌局状态", "info", timestamp);
}

function recordEvent(state: DesktopRuntimeState, item: LiveEvent): DesktopRuntimeState {
  const healthKey = healthDiagnosticKey(item.stage);
  return {
    ...state,
    events: newestFirst([item, ...state.events]).slice(0, 10),
    logs: newestFirst([item, ...state.logs]).slice(0, 80),
    diagnostics: healthKey
      ? { ...state.diagnostics, [healthKey]: item.detail }
      : state.diagnostics,
  };
}

/** 按事件时间倒序排列；无效时间戳排在有效事件之后。 */
function newestFirst(items: readonly LiveEvent[]): LiveEvent[] {
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(left.timestamp);
    const rightTime = Date.parse(right.timestamp);
    if (Number.isNaN(leftTime) && Number.isNaN(rightTime)) return 0;
    if (Number.isNaN(leftTime)) return 1;
    if (Number.isNaN(rightTime)) return -1;
    return rightTime - leftTime;
  });
}

function event(
  stage: string,
  title: string,
  detail: string,
  level: EventLevel,
  timestamp: string,
): LiveEvent {
  return { stage, title, detail, level, timestamp };
}

function clearRecommendation(snapshot: AdvisorSnapshot): AdvisorSnapshot {
  return {
    ...snapshot,
    recommendation: null,
    recommendationStatus: "idle",
    recommendationRevision: snapshot.recommendationRevision + 1,
    adviceMs: null,
    adviceStartedAtMs: null,
  };
}

/** 构造不带旧局事实的局前展示状态。 */
function createInitialPreplayView(startedAtMs: number | null = null): PreplayView {
  return {
    action: "等待局前阶段",
    score: null,
    detail: "尚未收到局前建议",
    startedAtMs,
    adviceMs: null,
    revision: 0,
  };
}

/** 新局边界必须撤销所有上一局可见事实，避免把旧牌面展示为当前牌局。 */
function createNewGameSnapshot(
  recommendationRevision: number,
  message: DesktopPhaseChangedEvent,
): AdvisorSnapshot {
  return {
    dealId: message.dealId || null,
    phase: javaTrackerPhase(message.phase),
    reason: message.phase === "WAIT_NEW_GAME" ? "waiting_for_game" : null,
    role: null,
    myCards: [],
    lastMove: [],
    leftMove: null,
    rightMove: null,
    round: null,
    remainingCards: {},
    recommendation: null,
    recommendationStatus: "idle",
    recommendationRevision: recommendationRevision + 1,
    recognitionMs: null,
    adviceMs: null,
    adviceStartedAtMs: null,
    remainingByRank: {},
  };
}

/** 只接受可解析的 Java 事件时间；异常时间不得伪造耗时。 */
function timestampMs(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 按同一 Java 事件时间线计算耗时，乱序或无效时间一律不展示。 */
function elapsedMs(startedAtMs: number | null, completedAt: string): number | null {
  const completedAtMs = timestampMs(completedAt);
  if (startedAtMs === null || completedAtMs === null || completedAtMs < startedAtMs) return null;
  return completedAtMs - startedAtMs;
}

function latestConfirmedAction(raw: Record<string, unknown>): Record<string, unknown> | null {
  if (!Array.isArray(raw.action_transitions)) return null;
  const actions = raw.action_transitions.filter(isRecord);
  return actions.at(-1) ?? null;
}

function numberRecord(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, number] =>
      typeof entry[1] === "number" && Number.isFinite(entry[1]),
    ),
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTrackerPhase(value: unknown): value is TrackerPhase {
  return ["waiting_deal", "tracking", "complete", "uncertain"].includes(String(value));
}

function isSeat(value: unknown): value is Seat {
  return ["landlord", "landlord_down", "landlord_up"].includes(String(value));
}

function preplayLabel(action: string): string {
  return {
    call: "叫地主",
    no_call: "不叫",
    rob: "抢地主",
    no_rob: "不抢",
    double: "加倍",
    super_double: "超级加倍",
    no_double: "不加倍",
  }[action] ?? "暂不可用";
}

function stageTitle(stage: string): string {
  return {
    capture: "画面捕获",
    recognition: "牌面识别",
    stabilization: "稳定确认",
    lifecycle: "牌局阶段",
    self_turn_snapshot: "本方回合",
    waiting: "等待画面",
  }[stage] ?? "等待状态";
}

function healthDiagnosticKey(stage: string): string | null {
  return {
    startup: "listener",
    listener: "listener",
    capture: "capture",
    recognition: "recognition",
    stabilization: "recognition",
    tracker: "tracker",
    lifecycle: "tracker",
    self_turn_snapshot: "tracker",
    action: "tracker",
    deal: "tracker",
    advice: "tracker",
  }[stage] ?? null;
}

function compactDetail(value: Record<string, unknown>): string {
  return Object.entries(value)
    .filter(([, item]) => typeof item === "string" || typeof item === "number" || typeof item === "boolean")
    .slice(0, 4)
    .map(([key, item]) => `${key}=${String(item)}`)
    .join(" · ");
}

/** 将语义座位转换为中文展示，不改变权威值。 */
export function roleText(role: Seat | null): string {
  return role
    ? { landlord: "地主", landlord_down: "地主下家", landlord_up: "地主上家" }[role]
    : "—";
}

/** 将 tracker 阶段转换为简短中文状态。 */
export function phaseText(phase: TrackerPhase): string {
  return {
    waiting_deal: "等待发牌",
    tracking: "跟踪中",
    complete: "本局完成",
    uncertain: "等待可靠状态",
  }[phase];
}

/** 明确区分无建议和模型建议不出。 */
export function recommendationText(recommendation: readonly string[] | null): string {
  if (recommendation === null) return "等待建议";
  return recommendation.length ? recommendation.join(" ") : "不出";
}
