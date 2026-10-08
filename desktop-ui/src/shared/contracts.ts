/** 桌面端可选择的模型后端；值必须与旧 listener 参数一致。 */
export type ModelBackend = "original" | "resnet2";

/** 桌面端允许的只读交互模式；select 只选择牌，不提交出牌。 */
export type InputMode = "advice" | "select";

/** 启动一次桌面监听所需的用户配置。 */
export interface DesktopSettings {
  /** 本局建议使用的模型后端。 */
  modelBackend: ModelBackend;
  /** 只展示建议或额外自动选择推荐牌；任何模式都不得提交出牌。 */
  inputMode: InputMode;
  /** 是否启用正式出牌建议与恢复链路。 */
  actionAdvice: boolean;
  /** 是否启用叫地主、抢地主和加倍建议。 */
  preplayAdvice: boolean;
  /** 是否允许旧 listener 自动操作局前按钮；关闭局前建议时必须为 false。 */
  autoPreplayButtons: boolean;
  /** 是否允许旧 listener 在结算稳定后自动换桌；默认关闭。 */
  autoSettlementChangeTable: boolean;
}

/** 启动 Java Runtime 前由操作者明确选择的只读能力。 */
export interface RuntimeSettings {
  /** 正式出牌模型标识；当前 Java 适配器只允许 resnet2。 */
  modelBackend: "resnet2";
  /** 是否启动局前 Python 模型并发布叫地主、抢地主和加倍建议。 */
  preplayAdvice: boolean;
  /** 是否启动正式出牌 Python 模型并发布本方回合建议。 */
  formalPlayAdvice: boolean;
  /**
   * 是否启用渐进式自动选牌：Java 拉起 Python 执行 Worker，在游戏画面选中推荐牌，
   * 但不会自动点击出牌按钮；用户仍需在游戏画面手工确认提交。默认关闭。
   */
  autoPlay: boolean;
  /** 是否允许自动点击叫地主、抢地主和正向加倍按钮；负向建议永远不点击。 */
  autoPreplayButtons: boolean;
}

/** desktop.v1 服务端允许发布的稳定事件类型。 */
export type DesktopEventType =
  | "READY"
  | "PHASE_CHANGED"
  | "PREPLAY_ADVICE"
  | "PLAY_TURN_STARTED"
  | "PLAY_ADVICE"
  | "GAME_FINISHED";

/** desktop.v1 所有事件共有的版本、实例和顺序字段。 */
export interface DesktopEventBase {
  /** 固定协议版本；当前只接受 desktop.v1。 */
  protocol: "desktop.v1";
  /** 明确事件类型，禁止使用通用 payload 猜测业务含义。 */
  type: DesktopEventType;
  /** 同一 Java 实例内的单调业务序号；READY 固定为零。 */
  sequence: number;
  /** Java 产生事件时的 ISO-8601 时间。 */
  emittedAt: string;
  /** Java 进程随机实例标识；实例变化时序号重新开始。 */
  serverInstanceId: string;
}

/** desktop.v1 中明确区分出牌和不出的语义动作。 */
export interface DesktopAction {
  /** true 表示明确不出，此时 cards 必须为空。 */
  pass: boolean;
  /** 实际打出的公开牌面符号；出牌时不能为空。 */
  cards: readonly string[];
}

/** Java 权威出牌历史中的一条动作。 */
export interface DesktopHistoryItem {
  /** 动作玩家在地主坐标系中的稳定座位。 */
  seat: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP";
  /** 玩家已经确认的出牌或不出动作。 */
  action: DesktopAction;
}

/** 模型对一个候选动作给出的原始回报价值。 */
export interface DesktopActionScore {
  /** 已恢复为领域牌面的候选动作。 */
  action: DesktopAction;
  /** 同一次模型请求内可比较的原始值，不是概率或胜率。 */
  value: number;
}

/** 鉴权成功后的 Java 服务能力声明。 */
export interface DesktopReadyEvent extends DesktopEventBase {
  type: "READY";
  /** Java 状态机是否已启用；false 仅表示网关连通。 */
  orchestrationEnabled: boolean;
}

/** Java 权威牌局阶段变化事件。 */
export interface DesktopPhaseChangedEvent extends DesktopEventBase {
  type: "PHASE_CHANGED";
  /** Java 权威牌局阶段。 */
  phase: "WAIT_NEW_GAME" | "PREPLAY" | "DEALING" | "BOTTOM_CARDS_REVEAL" | "ROLE_CONFIRMED" | "PLAYING" | "SETTLEMENT";
  /** 当前牌局标识；等待新局且尚无上下文时为空字符串。 */
  dealId: string;
}

/** Java 已校验的一条局前只读建议。 */
export interface DesktopPreplayAdviceEvent extends DesktopEventBase {
  type: "PREPLAY_ADVICE";
  /** 当前牌局标识。 */
  dealId: string;
  /** 当前牌局代次，用于隔离上一局迟到结果。 */
  generation: number;
  /** 当前实际观察到的局前提示阶段。 */
  stage: "CALL_LANDLORD" | "ROB_LANDLORD" | "DOUBLE";
  /** 当前稳定确认的本方牌面符号。 */
  hand: readonly string[];
  /** 当前已确认的底牌符号；尚未展示时为空。 */
  bottomCards: readonly string[];
  /** 当前画面真实提供的局前语义动作。 */
  availableActions: readonly string[];
  /** 本局是否观察过本方叫地主提示。 */
  callPromptSeen: boolean;
  /** 本局是否观察过本方抢地主提示。 */
  robPromptSeen: boolean;
  /** 产生建议的局前模型标识。 */
  modelId: string;
  /** 推荐动作只用于展示，不证明用户已经执行。 */
  action: string;
  /** 模型原始评分，不是概率或胜率。 */
  score: number;
  /** 本次建议使用的业务阈值。 */
  threshold: number;
}

/** Java 已校验的一条正式出牌只读结果。 */
export interface DesktopPlayAdviceEvent extends DesktopEventBase {
  type: "PLAY_ADVICE";
  /** 当前牌局标识。 */
  dealId: string;
  /** 当前 Java 权威阶段。 */
  phase: "PLAYING";
  /** 本方在地主坐标系中的稳定座位。 */
  localSeat: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP";
  /** 当前 Java 权威本方手牌。 */
  hand: readonly string[];
  /** 已确认的三张底牌；尚未具备时为空。 */
  bottomCards: readonly string[];
  /** 根据完整历史派生的当前行动座位。 */
  currentSeat: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP";
  /** 当前墩仍有效的最近出牌；新墩开始时为空。 */
  lastMove: readonly string[];
  /** 从地主首手开始的完整 Java 权威动作历史。 */
  history: readonly DesktopHistoryItem[];
  /** 推荐可用或必须等待/恢复的明确结果分类。 */
  resultKind: "RECOMMENDATION" | "UNAVAILABLE";
  /** 处理本次请求的模型标识。 */
  modelId: string;
  /** 推荐动作；结果不可用时为 null。 */
  recommendedAction: DesktopAction | null;
  /** 推荐动作原始价值；结果不可用时为 null。 */
  actionValue: number | null;
  /** 推荐动作领先第二名的原始价值差；结果不可用时为 null。 */
  actionMargin: number | null;
  /** 全部候选动作及模型原始价值。 */
  actionScores: readonly DesktopActionScore[];
  /** 稳定失败分类；建议可用时为空字符串。 */
  failure: string;
  /** 非敏感失败摘要；建议可用时为空字符串。 */
  detail: string;
}

/** Java 已确认轮到本方、正式模型正在生成新建议的状态事件。 */
export interface DesktopPlayTurnStartedEvent extends DesktopEventBase {
  type: "PLAY_TURN_STARTED";
  /** 当前牌局标识。 */
  dealId: string;
  /** 固定为 PLAYING。 */
  phase: "PLAYING";
  /** 本方在地主坐标系中的稳定座位。 */
  localSeat: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP";
  /** 当前行动座位；此事件中必须等于 localSeat。 */
  currentSeat: "LANDLORD" | "LANDLORD_DOWN" | "LANDLORD_UP";
}

/** 当前 Java GameContext 已收口的事件。 */
export interface DesktopGameFinishedEvent extends DesktopEventBase {
  type: "GAME_FINISHED";
  /** 已收口牌局标识。 */
  dealId: string;
  /** Java 权威的稳定收口原因。 */
  reason: "SETTLEMENT_DETECTED" | "STATE_TIMEOUT" | "RECOGNITION_FAILED" | "OPERATOR_ABORTED";
}

/** Electron 当前支持的全部 desktop.v1 服务端事件。 */
export type DesktopEvent =
  | DesktopReadyEvent
  | DesktopPhaseChangedEvent
  | DesktopPreplayAdviceEvent
  | DesktopPlayTurnStartedEvent
  | DesktopPlayAdviceEvent
  | DesktopGameFinishedEvent;

/** Electron 主进程向展示层发布的运行时消息类型。 */
export type ListenerMessageKind = "started" | "stdout" | "stderr" | "stopped" | "exit";

/** Electron 与 Vue 之间传递的一条 listener 生命周期或输出消息。 */
export interface LegacyListenerEnvelope {
  /** 消息所属的 listener 生命周期类型。 */
  kind: ListenerMessageKind;
  /** ISO-8601 时间戳，仅用于 UI 时间线。 */
  timestamp: string;
  /** stdout、stderr 或生命周期摘要；不包含图片和二进制数据。 */
  line: string;
  /** listener 退出码；只在 exit 消息中存在。 */
  exitCode?: number | null;
  /** 当前生命周期消息所属的显式传输。 */
  transport?: "python_jsonl" | "java_ws";
}

/** Electron 主进程向 Vue 转发的一条已校验 desktop.v1 事件。 */
export interface DesktopEventEnvelope {
  /** 固定为 desktop_event，renderer 不再解析通用 JSON 字符串。 */
  kind: "desktop_event";
  /** Java 原始事件时间，用于时间线展示。 */
  timestamp: string;
  /** 已在 Electron 主进程完成协议和顺序校验的事件。 */
  event: DesktopEvent;
  /** 当前消息来自 Java WebSocket。 */
  transport: "java_ws";
}

/** Electron 到 Vue 的兼容消息联合；旧 JSONL 可作为显式回退。 */
export type ListenerEnvelope = LegacyListenerEnvelope | DesktopEventEnvelope;

/** 启动或停止完整运行时后返回给 renderer 的可观察结果。 */
export interface ListenerActionResult {
  /** 请求完成后 Java desktop.v1 是否已经完成鉴权连接。 */
  running: boolean;
  /** 面向用户的简短结果说明。 */
  message: string;
}

/** 欢乐斗地主窗口客户区的 DPI 感知只读标定状态。 */
export interface GameWindowGeometry {
  /** 是否找到唯一目标游戏窗口。 */
  found: boolean;
  /** 当前客户区宽高，单位为像素；窗口不可读时为空。 */
  currentSize: readonly [number, number] | null;
  /** 识别标定使用的物理客户区宽高，单位为像素。 */
  targetSize: readonly [number, number];
  /** 按窗口 DPI 将逻辑尺寸换算后的物理像素宽高；无法换算时为空。 */
  effectiveSize: readonly [number, number] | null;
  /** 目标窗口当前 DPI；96 表示 100% 缩放，无法读取时为空。 */
  dpi: number | null;
  /** 当前命中的是物理尺寸、DPI 等效尺寸，还是需要人工检查。 */
  matchMode: "physical_pixels" | "dpi_virtualized" | "mismatch" | "unavailable";
  /** 检查或用户触发的自动调整失败的非敏感摘要；成功时为空。 */
  error: string | null;
  /** 检查或用户触发的自动调整失败是否需要用户人工处理窗口或系统状态。 */
  actionableError: boolean;
  /** 当前客户区是否已按物理像素或 DPI 等效尺寸满足识别标定。 */
  matches: boolean;
}

/** preload 暴露给 Vue 的最小桌面能力，不允许 renderer 直接访问 Node。 */
export interface DesktopApi {
  /** 启动由 Electron 主进程拥有的 Java Runtime 及其 Python 服务树。 */
  startRuntime(settings: RuntimeSettings): Promise<ListenerActionResult>;
  /** 停止本 Electron 实例创建的 Java Runtime 及其 Python 服务树。 */
  stopRuntime(): Promise<ListenerActionResult>;
  /** 检查游戏窗口客户区，不执行尺寸修改。 */
  inspectGameWindow(): Promise<GameWindowGeometry>;
  /** 用户点击检测后，由 renderer 在尺寸不符时调用以调整客户区并回读结果。 */
  adjustGameWindow(): Promise<GameWindowGeometry>;
  /** 订阅 Runtime 生命周期、诊断和 desktop.v1 事件；返回值用于释放当前订阅。 */
  onListenerMessage(callback: (message: ListenerEnvelope) => void): () => void;
}
