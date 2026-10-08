import { describe, expect, it } from "vitest";

import { parseDesktopEvent } from "./desktop-protocol";

const base = {
  protocol: "desktop.v1",
  sequence: 1,
  emittedAt: "2026-08-28T08:00:00.000Z",
  serverInstanceId: "server-1",
};

describe("desktop.v1 protocol", () => {
  /** 验证明确阶段事件能通过边界校验，供主进程按序转发给 Vue。 */
  it("接受合法阶段事件", () => {
    const event = parseDesktopEvent({
      ...base,
      type: "PHASE_CHANGED",
      phase: "PLAYING",
      dealId: "deal-1",
    });
    expect(event.type).toBe("PHASE_CHANGED");
  });

  /** 验证本方回合开始事件必须明确证明当前行动座位与本方一致。 */
  it("接受本方回合开始事件", () => {
    const event = parseDesktopEvent({
      ...base,
      type: "PLAY_TURN_STARTED",
      dealId: "deal-1",
      phase: "PLAYING",
      localSeat: "LANDLORD_DOWN",
      currentSeat: "LANDLORD_DOWN",
    });
    expect(event.type).toBe("PLAY_TURN_STARTED");
  });

  /** 验证未知版本不能被当作兼容 JSON 继续消费，避免接口漂移产生假建议。 */
  it("拒绝未知协议版本", () => {
    expect(() => parseDesktopEvent({
      ...base,
      protocol: "desktop.v2",
      type: "PHASE_CHANGED",
      phase: "PLAYING",
      dealId: "deal-1",
    })).toThrow("不支持的桌面协议版本");
  });

  /** 验证不可用正式结果不得夹带推荐动作，保持失败即等待的安全语义。 */
  it("拒绝不可用结果夹带推荐动作", () => {
    expect(() => parseDesktopEvent({
      ...base,
      type: "PLAY_ADVICE",
      phase: "PLAYING",
      dealId: "deal-1",
      localSeat: "LANDLORD",
      currentSeat: "LANDLORD",
      hand: ["3"],
      bottomCards: ["A", "2", "D"],
      lastMove: [],
      history: [],
      resultKind: "UNAVAILABLE",
      modelId: "resnet2",
      recommendedAction: { pass: false, cards: ["3"] },
      actionValue: 1,
      actionMargin: 0,
      actionScores: [],
      failure: "MODEL_TIMEOUT",
      detail: "timeout",
    })).toThrow("不可用结果不得携带推荐动作");
  });
});
