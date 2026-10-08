import type {
  DesktopAction,
  DesktopEvent,
  DesktopEventType,
  DesktopHistoryItem,
} from "../shared/contracts";

const TYPES = new Set<DesktopEventType>([
  "READY",
  "PHASE_CHANGED",
  "PREPLAY_ADVICE",
  "PLAY_TURN_STARTED",
  "PLAY_ADVICE",
  "GAME_FINISHED",
]);

/** 将未知 JSON 严格收窄为当前 Electron 支持的 desktop.v1 明确事件。 */
export function parseDesktopEvent(raw: unknown): DesktopEvent {
  if (!isRecord(raw)) throw new Error("desktop.v1 事件必须是对象");
  requireBase(raw);
  switch (raw.type) {
    case "READY":
      require(raw.sequence === 0, "READY 序号必须为零");
      require(typeof raw.orchestrationEnabled === "boolean", "READY 缺少编排状态");
      return raw as unknown as DesktopEvent;
    case "PHASE_CHANGED":
      require(oneOf(raw.phase, ["WAIT_NEW_GAME", "PREPLAY", "DEALING", "BOTTOM_CARDS_REVEAL", "ROLE_CONFIRMED", "PLAYING", "SETTLEMENT"]), "未知 Java 阶段");
      require(typeof raw.dealId === "string", "阶段事件缺少牌局标识");
      return raw as unknown as DesktopEvent;
    case "PREPLAY_ADVICE":
      requireText(raw.dealId, "局前建议缺少牌局标识");
      requirePositiveInteger(raw.generation, "局前建议代次无效");
      require(oneOf(raw.stage, ["CALL_LANDLORD", "ROB_LANDLORD", "DOUBLE"]), "未知局前阶段");
      requireStrings(raw.hand, "局前手牌无效");
      requireStrings(raw.bottomCards, "局前底牌无效");
      requireStrings(raw.availableActions, "局前可用动作无效");
      require(typeof raw.callPromptSeen === "boolean", "局前叫地主提示状态无效");
      require(typeof raw.robPromptSeen === "boolean", "局前抢地主提示状态无效");
      requireText(raw.modelId, "局前模型标识无效");
      requireText(raw.action, "局前建议动作无效");
      requireFinite(raw.score, "局前评分无效");
      requireFinite(raw.threshold, "局前阈值无效");
      return raw as unknown as DesktopEvent;
    case "PLAY_TURN_STARTED":
      requireText(raw.dealId, "本方回合事件缺少牌局标识");
      require(raw.phase === "PLAYING", "本方回合事件阶段必须为 PLAYING");
      requireSeat(raw.localSeat, "本方回合事件座位无效");
      requireSeat(raw.currentSeat, "本方回合事件当前座位无效");
      require(raw.currentSeat === raw.localSeat, "本方回合事件必须确认当前轮到本方");
      return raw as unknown as DesktopEvent;
    case "PLAY_ADVICE":
      requireText(raw.dealId, "正式建议缺少牌局标识");
      require(raw.phase === "PLAYING", "正式建议阶段必须为 PLAYING");
      requireSeat(raw.localSeat, "本方座位无效");
      requireSeat(raw.currentSeat, "当前行动座位无效");
      requireStrings(raw.hand, "正式建议手牌无效");
      requireStrings(raw.bottomCards, "正式建议底牌无效");
      requireStrings(raw.lastMove, "最近出牌无效");
      require(Array.isArray(raw.history) && raw.history.every(isHistoryItem), "正式建议历史无效");
      require(oneOf(raw.resultKind, ["RECOMMENDATION", "UNAVAILABLE"]), "正式建议结果分类无效");
      requireText(raw.modelId, "正式建议模型标识无效");
      require(Array.isArray(raw.actionScores) && raw.actionScores.every(isActionScore), "候选动作评分无效");
      require(typeof raw.failure === "string" && typeof raw.detail === "string", "正式建议失败信息无效");
      if (raw.resultKind === "RECOMMENDATION") {
        require(isAction(raw.recommendedAction), "可信建议缺少动作");
        requireFinite(raw.actionValue, "推荐动作价值无效");
        requireFinite(raw.actionMargin, "推荐动作领先值无效");
      } else {
        require(raw.recommendedAction === null && raw.actionValue === null && raw.actionMargin === null, "不可用结果不得携带推荐动作");
      }
      return raw as unknown as DesktopEvent;
    case "GAME_FINISHED":
      requireText(raw.dealId, "牌局收口事件缺少牌局标识");
      require(oneOf(raw.reason, ["SETTLEMENT_DETECTED", "STATE_TIMEOUT", "RECOGNITION_FAILED", "OPERATOR_ABORTED"]), "未知牌局收口原因");
      return raw as unknown as DesktopEvent;
  }
  throw new Error("未知 desktop.v1 事件");
}

function requireBase(raw: Record<string, unknown>): void {
  require(raw.protocol === "desktop.v1", "不支持的桌面协议版本");
  require(typeof raw.type === "string" && TYPES.has(raw.type as DesktopEventType), "未知桌面事件类型");
  require(Number.isSafeInteger(raw.sequence) && Number(raw.sequence) >= 0, "桌面事件序号无效");
  requireText(raw.emittedAt, "桌面事件时间无效");
  requireText(raw.serverInstanceId, "桌面服务实例标识无效");
}

function isAction(value: unknown): value is DesktopAction {
  if (!isRecord(value) || typeof value.pass !== "boolean" || !Array.isArray(value.cards)) return false;
  if (!value.cards.every((card) => typeof card === "string")) return false;
  return value.pass === (value.cards.length === 0);
}

function isHistoryItem(value: unknown): value is DesktopHistoryItem {
  return isRecord(value) && isSeat(value.seat) && isAction(value.action);
}

function isActionScore(value: unknown): boolean {
  return isRecord(value) && isAction(value.action) && Number.isFinite(value.value);
}

function requireSeat(value: unknown, message: string): void {
  require(isSeat(value), message);
}

function isSeat(value: unknown): value is DesktopHistoryItem["seat"] {
  return oneOf(value, ["LANDLORD", "LANDLORD_DOWN", "LANDLORD_UP"]);
}

function requireStrings(value: unknown, message: string): void {
  require(Array.isArray(value) && value.every((item) => typeof item === "string"), message);
}

function requireText(value: unknown, message: string): void {
  require(typeof value === "string" && value.length > 0, message);
}

function requirePositiveInteger(value: unknown, message: string): void {
  require(Number.isSafeInteger(value) && Number(value) > 0, message);
}

function requireFinite(value: unknown, message: string): void {
  require(typeof value === "number" && Number.isFinite(value), message);
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && allowed.includes(value as T);
}

function require(condition: boolean, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
