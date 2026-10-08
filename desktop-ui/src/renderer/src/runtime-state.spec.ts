import { describe, expect, it } from "vitest";

import type { LegacyListenerEnvelope } from "../../shared/contracts";
import {
  consumeListenerEnvelope,
  createInitialRuntimeState,
  recommendationText,
} from "./runtime-state";

function message(kind: LegacyListenerEnvelope["kind"], line: string): LegacyListenerEnvelope {
  return { kind, line, timestamp: "2026-08-28T08:00:00.000Z" };
}

describe("desktop runtime state", () => {
  /** 验证 Java 正式建议按 desktop.v1 语义更新角色、手牌、显式不出和记牌器。 */
  it("折叠 Java desktop.v1 正式建议", () => {
    const state = consumeListenerEnvelope(createInitialRuntimeState(), {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_ADVICE",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        hand: ["D", "3"],
        bottomCards: ["A", "2", "X"],
        currentSeat: "LANDLORD_DOWN",
        lastMove: ["A"],
        history: [{ seat: "LANDLORD", action: { pass: false, cards: ["A"] } }],
        resultKind: "RECOMMENDATION",
        modelId: "resnet2",
        recommendedAction: { pass: true, cards: [] },
        actionValue: 0.3,
        actionMargin: 0.1,
        actionScores: [{ action: { pass: true, cards: [] }, value: 0.3 }],
        failure: "",
        detail: "",
      },
    });
    expect(state.transport).toBe("java_ws");
    expect(state.snapshot.dealId).toBe("deal-java-1");
    expect(state.snapshot.role).toBe("landlord_down");
    expect(state.snapshot.recommendation).toEqual([]);
    expect(state.snapshot.leftMove).toEqual(["A"]);
    expect(state.snapshot.rightMove).toBeNull();
    expect(state.snapshot.round).toBe(1);
    expect(state.snapshot.remainingByRank.D).toBe(0);
    expect(state.events[0]?.title).toBe("Java 出牌建议");
  });

  /** 验证局前建议进入专用展示字段，而不是覆盖正式出牌建议卡片。 */
  it("折叠 Java desktop.v1 局前建议", () => {
    const state = consumeListenerEnvelope(createInitialRuntimeState(), {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PREPLAY_ADVICE",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        generation: 1,
        stage: "DOUBLE",
        hand: ["X", "2"],
        bottomCards: [],
        availableActions: ["DOUBLE", "NO_DOUBLE"],
        callPromptSeen: false,
        robPromptSeen: true,
        modelId: "fullauto-v1",
        action: "NO_DOUBLE",
        score: 0.187,
        threshold: 1,
      },
    });
    expect(state.preplay.action).toBe("不加倍");
    expect(state.preplay.score).toBe(0.187);
    expect(state.preplay.detail).toContain("不加倍");
    expect(state.snapshot.recommendation).toBeNull();
    expect(state.events[0]?.title).toBe("Java 局前建议");
  });

  /** 验证局前建议显示从局前阶段开始的耗时，且相同文案也会递增版本以重放到达动画。 */
  it("局前建议按阶段时间计算耗时并递增动画版本", () => {
    let state = consumeListenerEnvelope(createInitialRuntimeState(), {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PHASE_CHANGED",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        phase: "PREPLAY",
        dealId: "deal-java-2",
      },
    });
    const preplayEvent = {
      protocol: "desktop.v1" as const,
      type: "PREPLAY_ADVICE" as const,
      serverInstanceId: "server-1",
      dealId: "deal-java-2",
      generation: 1,
      stage: "DOUBLE" as const,
      hand: ["X", "2"],
      bottomCards: [],
      availableActions: ["DOUBLE", "NO_DOUBLE"],
      callPromptSeen: false,
      robPromptSeen: true,
      modelId: "fullauto-v1",
      action: "NO_DOUBLE",
      score: 0.187,
      threshold: 1,
    };
    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:01.250Z",
      transport: "java_ws",
      event: { ...preplayEvent, sequence: 2, emittedAt: "2026-08-28T08:00:01.250Z" },
    });
    expect(state.preplay.adviceMs).toBe(1250);
    expect(state.preplay.revision).toBe(1);

    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:02.000Z",
      transport: "java_ws",
      event: { ...preplayEvent, sequence: 3, emittedAt: "2026-08-28T08:00:02.000Z" },
    });
    expect(state.preplay.adviceMs).toBe(2000);
    expect(state.preplay.revision).toBe(2);
  });

  /** 验证新局边界不会把旧局手牌、记牌器、角色、轮次或建议带到下一局。 */
  it("进入下一局时初始化上一局展示状态", () => {
    let state = consumeListenerEnvelope(createInitialRuntimeState(), {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_ADVICE",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        hand: ["D", "3"],
        bottomCards: ["A", "2", "X"],
        currentSeat: "LANDLORD_DOWN",
        lastMove: ["A"],
        history: [{ seat: "LANDLORD", action: { pass: false, cards: ["A"] } }],
        resultKind: "RECOMMENDATION",
        modelId: "resnet2",
        recommendedAction: { pass: false, cards: ["3"] },
        actionValue: 0.3,
        actionMargin: 0.1,
        actionScores: [{ action: { pass: false, cards: ["3"] }, value: 0.3 }],
        failure: "",
        detail: "",
      },
    });
    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:01:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PHASE_CHANGED",
        sequence: 2,
        emittedAt: "2026-08-28T08:01:00.000Z",
        serverInstanceId: "server-1",
        phase: "WAIT_NEW_GAME",
        dealId: "",
      },
    });
    expect(state.snapshot.dealId).toBeNull();
    expect(state.snapshot.role).toBeNull();
    expect(state.snapshot.myCards).toEqual([]);
    expect(state.snapshot.remainingByRank).toEqual({});
    expect(state.snapshot.leftMove).toBeNull();
    expect(state.snapshot.rightMove).toBeNull();
    expect(state.snapshot.round).toBeNull();
    expect(state.snapshot.recommendation).toBeNull();
    expect(state.snapshot.recognitionMs).toBeNull();
    expect(state.snapshot.adviceMs).toBeNull();
    expect(state.preplay.score).toBeNull();
  });

  /** 验证 listener 快照先建立可信牌局，再由同一角色的建议更新展示和记牌器。 */
  it("折叠牌局快照和正式建议", () => {
    let state = createInitialRuntimeState();
    state = consumeListenerEnvelope(
      state,
      message(
        "stdout",
        JSON.stringify({
          snapshot: {
            phase: "tracking",
            reason: null,
            role: "landlord_down",
            my_cards: ["D", "2", "A"],
            last_move: ["9", "9"],
            remaining_cards: { landlord: 18, landlord_down: 14, landlord_up: 17 },
            remaining_by_rank: { D: 0, X: 1, "2": 2, A: 3, "3": 4 },
          },
        }),
      ),
    );
    state = consumeListenerEnvelope(
      state,
      message(
        "stdout",
        JSON.stringify({
          role: "landlord_down",
          recommendation: ["3", "4", "5", "6", "7"],
          elapsed_ms: 1.8,
          timings_ms: { recognition: 3.2 },
          legal_actions: [["3"], ["4"]],
        }),
      ),
    );

    expect(state.snapshot.role).toBe("landlord_down");
    expect(state.snapshot.remainingByRank.X).toBe(1);
    expect(state.snapshot.recommendation).toEqual(["3", "4", "5", "6", "7"]);
    expect(state.snapshot.recognitionMs).toBe(3.2);
    expect(state.events[0]?.detail).toContain("2 个合法动作");
  });

  /** 验证空推荐是明确“不出”，而 null 仍表示尚无建议。 */
  it("区分不出和没有建议", () => {
    expect(recommendationText(null)).toBe("等待建议");
    expect(recommendationText([])).toBe("不出");
  });

  /** 验证尚未建立 tracking 快照时，即使收到形似建议的数据也不能显示建议。 */
  it("拒绝在不可靠状态展示建议", () => {
    const state = consumeListenerEnvelope(
      createInitialRuntimeState(),
      message("stdout", JSON.stringify({ role: "landlord", recommendation: ["A"] })),
    );
    expect(state.snapshot.recommendation).toBeNull();
    expect(state.events).toHaveLength(0);
  });

  /** 验证捕获尺寸或 DPI 标定失败以错误事件显示，不能伪装成普通等待。 */
  it("提升捕获尺寸或 DPI 标定失败的可见级别", () => {
    const state = consumeListenerEnvelope(
      createInitialRuntimeState(),
      message(
        "stdout",
        JSON.stringify({
          status: "waiting",
          wait: {
            stage: "capture",
            reason: "CaptureGeometryError: client frame does not match calibration",
          },
        }),
      ),
    );
    expect(state.events[0]?.level).toBe("error");
    expect(state.diagnostics.capture).toContain("does not match");
  });

  /** 验证局前模型原始评分只作为解释信息展示，并保留明确动作语义。 */
  it("展示局前动作及原始评分", () => {
    const state = consumeListenerEnvelope(
      createInitialRuntimeState(),
      message(
        "stdout",
        JSON.stringify({ preplay_advice: { action: "double", score: 1.25 } }),
      ),
    );
    expect(state.preplay.action).toBe("加倍");
    expect(state.preplay.score).toBe(1.25);
    expect(state.preplay.detail).toBe("加倍 · 评分 1.250");
  });

  /** 验证本家不出确认会立即清除上一条建议，防止跨回合残留。 */
  it("本家不出后撤销旧建议", () => {
    let state = createInitialRuntimeState();
    state = consumeListenerEnvelope(
      state,
      message(
        "stdout",
        JSON.stringify({
          snapshot: {
            phase: "tracking",
            role: "landlord_up",
            my_cards: ["3"],
            last_move: [],
            remaining_cards: {},
            remaining_by_rank: { "3": 1 },
          },
        }),
      ),
    );
    state = consumeListenerEnvelope(
      state,
      message("stdout", JSON.stringify({ role: "landlord_up", recommendation: ["3"] })),
    );
    state = consumeListenerEnvelope(
      state,
      message(
        "stdout",
        JSON.stringify({ action: { position: "self", cards: [], passed: true } }),
      ),
    );
    expect(state.snapshot.recommendation).toBeNull();
    expect(state.events[0]?.title).toBe("本局不出已确认");
  });

  /** 验证本方回合开始事件会先清空旧建议，正式建议到达后递增版本供 UI 播放新建议动画。 */
  it("本方回合开始时清空旧建议并标记新建议", () => {
    let state = createInitialRuntimeState();
    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:00.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_ADVICE",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        hand: ["D", "3"],
        bottomCards: ["A", "2", "X"],
        currentSeat: "LANDLORD_DOWN",
        lastMove: ["A"],
        history: [],
        resultKind: "RECOMMENDATION",
        modelId: "resnet2",
        recommendedAction: { pass: false, cards: ["3"] },
        actionValue: 0.3,
        actionMargin: 0.1,
        actionScores: [{ action: { pass: false, cards: ["3"] }, value: 0.3 }],
        failure: "",
        detail: "",
      },
    });
    expect(state.snapshot.recommendation).toEqual(["3"]);
    expect(state.snapshot.recommendationRevision).toBe(1);

    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:01.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_TURN_STARTED",
        sequence: 2,
        emittedAt: "2026-08-28T08:00:01.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        currentSeat: "LANDLORD_DOWN",
      },
    });
    expect(state.snapshot.recommendation).toBeNull();
    expect(state.snapshot.recommendationStatus).toBe("pending");
    expect(state.snapshot.recommendationRevision).toBe(2);
    expect(state.events[0]?.title).toBe("轮到本方，正在生成建议");
  });

  /** 验证正式建议按本方回合开始与结果发布的 Java 时间差展示耗时。 */
  it("正式建议按本方回合时间计算耗时", () => {
    let state = consumeListenerEnvelope(createInitialRuntimeState(), {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:01.000Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_TURN_STARTED",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:01.000Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        currentSeat: "LANDLORD_DOWN",
      },
    });
    state = consumeListenerEnvelope(state, {
      kind: "desktop_event",
      timestamp: "2026-08-28T08:00:01.450Z",
      transport: "java_ws",
      event: {
        protocol: "desktop.v1",
        type: "PLAY_ADVICE",
        sequence: 2,
        emittedAt: "2026-08-28T08:00:01.450Z",
        serverInstanceId: "server-1",
        dealId: "deal-java-1",
        phase: "PLAYING",
        localSeat: "LANDLORD_DOWN",
        hand: ["3"],
        bottomCards: ["A", "2", "X"],
        currentSeat: "LANDLORD_DOWN",
        lastMove: ["A"],
        history: [],
        resultKind: "RECOMMENDATION",
        modelId: "resnet2",
        recommendedAction: { pass: false, cards: ["3"] },
        actionValue: 0.3,
        actionMargin: 0.1,
        actionScores: [{ action: { pass: false, cards: ["3"] }, value: 0.3 }],
        failure: "",
        detail: "",
      },
    });
    expect(state.snapshot.adviceMs).toBe(450);
  });

  /** 验证实时建议页只保留按事件时间倒序排列的最近十条事件。 */
  it("按时间倒序截取最近十条事件", () => {
    let state = createInitialRuntimeState();
    for (let index = 0; index < 12; index += 1) {
      state = consumeListenerEnvelope(state, {
        kind: "stderr",
        line: `event-${index}`,
        timestamp: `2026-08-28T08:00:${String(index).padStart(2, "0")}.000Z`,
      });
    }

    expect(state.events).toHaveLength(10);
    expect(state.events[0]?.detail).toBe("event-11");
    expect(state.events.at(-1)?.detail).toBe("event-2");

    state = consumeListenerEnvelope(state, {
      kind: "stderr",
      line: "late-arriving-old-event",
      timestamp: "2026-08-28T08:00:01.000Z",
    });
    expect(state.events[0]?.detail).toBe("event-11");
    expect(state.events.at(-1)?.detail).toBe("event-2");
  });

  /** 验证 listener 异常退出后 UI 进入停止状态并撤销仍在展示的建议。 */
  it("异常退出时安全收口展示状态", () => {
    const initial = createInitialRuntimeState();
    const running = consumeListenerEnvelope(initial, message("started", "主模型 original"));
    const exited = consumeListenerEnvelope(running, {
      ...message("exit", "监听器退出码 2"),
      exitCode: 2,
    });
    expect(exited.running).toBe(false);
    expect(exited.events[0]?.level).toBe("error");
  });
});
