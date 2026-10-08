import { afterEach, describe, expect, it } from "vitest";
import { WebSocketServer } from "ws";

import type { DesktopSettings, ListenerEnvelope } from "../shared/contracts";
import { JavaRuntimeClient, validateEndpoint } from "./java-runtime-client";

const TOKEN = "0123456789abcdef0123456789abcdef";
const SETTINGS: DesktopSettings = {
  modelBackend: "resnet2",
  inputMode: "advice",
  actionAdvice: true,
  preplayAdvice: true,
  autoPreplayButtons: false,
  autoSettlementChangeTable: false,
};

describe("Java runtime WebSocket client", () => {
  let server: WebSocketServer | null = null;

  afterEach(async () => {
    if (!server) return;
    for (const client of server.clients) client.terminate();
    await new Promise<void>((resolve) => server?.close(() => resolve()));
    server = null;
  });

  /** 验证主进程通过 Bearer 令牌连接 Java，并拒绝同一实例中倒退的业务序号。 */
  it("鉴权连接并丢弃陈旧序号", async () => {
    server = new WebSocketServer({ host: "127.0.0.1", port: 0, path: "/desktop/v1" });
    await new Promise<void>((resolve) => server?.once("listening", () => resolve()));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("测试 WebSocket 未绑定 TCP 端口");
    const received: ListenerEnvelope[] = [];
    server.on("connection", (socket, request) => {
      expect(request.headers.authorization).toBe(`Bearer ${TOKEN}`);
      socket.send(JSON.stringify({
        protocol: "desktop.v1",
        type: "READY",
        sequence: 0,
        emittedAt: "2026-08-28T08:00:00.000Z",
        serverInstanceId: "server-1",
        orchestrationEnabled: true,
      }));
      socket.send(JSON.stringify({
        protocol: "desktop.v1",
        type: "PHASE_CHANGED",
        sequence: 2,
        emittedAt: "2026-08-28T08:00:01.000Z",
        serverInstanceId: "server-1",
        phase: "PLAYING",
        dealId: "deal-new",
      }));
      socket.send(JSON.stringify({
        protocol: "desktop.v1",
        type: "PHASE_CHANGED",
        sequence: 1,
        emittedAt: "2026-08-28T08:00:02.000Z",
        serverInstanceId: "server-1",
        phase: "PREPLAY",
        dealId: "deal-stale",
      }));
    });

    const client = new JavaRuntimeClient(
      `ws://127.0.0.1:${address.port}/desktop/v1`,
      TOKEN,
      (message) => received.push(message),
    );
    expect((await client.start(SETTINGS)).running).toBe(true);
    await waitFor(() => received.filter((item) => item.kind === "desktop_event").length === 2);
    const events = received.filter((item) => item.kind === "desktop_event");
    expect(events.map((item) => item.event.type)).toEqual(["READY", "PHASE_CHANGED"]);
    expect(events[1]?.event.type === "PHASE_CHANGED" && events[1].event.dealId).toBe("deal-new");
    client.stop();
  });

  /** 验证 TCP 升级成功但服务端未发送有效 READY 时，启动结果不能伪装成已鉴权连接。 */
  it("未收到 READY 时启动失败", async () => {
    server = new WebSocketServer({ host: "127.0.0.1", port: 0, path: "/desktop/v1" });
    await new Promise<void>((resolve) => server?.once("listening", () => resolve()));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("测试 WebSocket 未绑定 TCP 端口");
    server.on("connection", (socket) => socket.close(1008, "unauthorized"));
    const received: ListenerEnvelope[] = [];
    const client = new JavaRuntimeClient(
      `ws://127.0.0.1:${address.port}/desktop/v1`,
      TOKEN,
      (message) => received.push(message),
    );

    expect((await client.start(SETTINGS)).running).toBe(false);
    expect(received.some((item) => item.kind === "stderr" && item.line.includes("READY"))).toBe(true);
  });

  /** 验证 Electron 拒绝非回环地址、错误路径和过短令牌，不让令牌进入 URL 或 renderer。 */
  it("限制连接到固定回环路径", () => {
    expect(() => validateEndpoint("ws://192.168.1.2:17373/desktop/v1", TOKEN)).toThrow("回环地址");
    expect(() => validateEndpoint("ws://127.0.0.1:17373/other", TOKEN)).toThrow("路径");
    expect(() => validateEndpoint("ws://127.0.0.1:17373/desktop/v1", "short")).toThrow("令牌");
  });
});

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("截止时间内未收到预期 desktop.v1 事件");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
