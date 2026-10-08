import WebSocket from "ws";

import type {
  DesktopEvent,
  DesktopSettings,
  ListenerActionResult,
  ListenerEnvelope,
} from "../shared/contracts";
import { parseDesktopEvent } from "./desktop-protocol";
import type { RuntimePublisher, RuntimeTransport } from "./runtime-transport";

const CONNECT_TIMEOUT_MS = 5_000;
const MAX_RECONNECT_DELAY_MS = 5_000;

/** Electron 主进程拥有的 Java desktop.v1 客户端；renderer 永远看不到启动令牌。 */
export class JavaRuntimeClient implements RuntimeTransport {
  private socket: WebSocket | null = null;
  private desired = false;
  private reconnectAttempt = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private serverInstanceId: string | null = null;
  private lastSequence = 0;

  public constructor(
    private readonly url: string,
    private readonly token: string,
    private readonly publish: RuntimePublisher,
  ) {
    validateEndpoint(url, token);
  }

  /** 连接 Java 网关；桌面设置仍由 Java 启动配置控制，不通过只读 WS 下发。 */
  public async start(_settings: DesktopSettings): Promise<ListenerActionResult> {
    if (this.isRunning()) return { running: true, message: "Java Runtime 已连接" };
    this.desired = true;
    try {
      await this.connect();
      return { running: true, message: "Java Runtime 已连接" };
    } catch (error) {
      this.desired = false;
      this.publishLifecycle("stderr", `Java Runtime 连接失败: ${errorText(error)}`);
      return { running: false, message: "Java Runtime 连接失败" };
    }
  }

  /** 主动断开 Java 网关；不向 Java 发送停止牌局或点击类命令。 */
  public stop(): ListenerActionResult {
    this.desired = false;
    this.clearReconnect();
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "desktop stop");
    this.publishLifecycle("stopped", "已断开 Java Runtime；未发送任何游戏操作");
    return { running: false, message: "Java Runtime 已断开" };
  }

  /** 返回 WebSocket 是否已经完成鉴权并保持打开。 */
  public isRunning(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  private connect(): Promise<void> {
    this.clearReconnect();
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this.url, {
        headers: { Authorization: `Bearer ${this.token}` },
        handshakeTimeout: CONNECT_TIMEOUT_MS,
        maxPayload: 1024 * 1024,
      });
      this.socket = socket;
      let settled = false;
      const readyTimeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        socket.close(1002, "READY timeout");
        reject(new Error("连接后未在截止时间内收到有效 READY"));
      }, CONNECT_TIMEOUT_MS);

      socket.on("message", (data, binary) => {
        if (binary) {
          socket.close(1003, "binary messages are unsupported");
          return;
        }
        const event = this.consume(data.toString());
        if (event?.type === "READY" && !settled) {
          settled = true;
          clearTimeout(readyTimeout);
          this.reconnectAttempt = 0;
          this.publishLifecycle("started", "Java Runtime desktop.v1 已鉴权连接");
          resolve();
        }
      });
      socket.once("error", (error) => {
        if (!settled) {
          settled = true;
          clearTimeout(readyTimeout);
          reject(error);
        } else {
          this.publishLifecycle("stderr", `Java Runtime 传输错误: ${errorText(error)}`);
        }
      });
      socket.once("close", (code) => {
        if (this.socket === socket) this.socket = null;
        if (!settled) {
          settled = true;
          clearTimeout(readyTimeout);
          reject(new Error(`连接在 READY 前关闭，代码 ${code}`));
        }
        if (this.desired) this.scheduleReconnect(code);
      });
    });
  }

  private consume(text: string): DesktopEvent | null {
    let event: DesktopEvent;
    try {
      event = parseDesktopEvent(JSON.parse(text));
      if (event.type === "READY") {
        if (this.serverInstanceId !== event.serverInstanceId) {
          this.serverInstanceId = event.serverInstanceId;
          this.lastSequence = 0;
        }
      } else {
        if (event.serverInstanceId !== this.serverInstanceId) {
          throw new Error("业务事件在 READY 前到达或服务实例不匹配");
        }
        if (event.sequence <= this.lastSequence) return null;
        this.lastSequence = event.sequence;
      }
    } catch (error) {
      this.publishLifecycle("stderr", `拒绝无效 desktop.v1 事件: ${errorText(error)}`);
      this.socket?.close(1002, "invalid desktop.v1 event");
      return null;
    }
    const envelope: ListenerEnvelope = {
      kind: "desktop_event",
      timestamp: event.emittedAt,
      event,
      transport: "java_ws",
    };
    this.publish(envelope);
    return event;
  }

  private scheduleReconnect(code: number): void {
    const delay = Math.min(250 * (2 ** this.reconnectAttempt++), MAX_RECONNECT_DELAY_MS);
    this.publishLifecycle("stderr", `Java Runtime 连接已断开，${delay} ms 后重连（代码 ${code}）`);
    this.reconnectTimer = setTimeout(() => {
      if (!this.desired) return;
      void this.connect().catch((error) => {
        this.publishLifecycle("stderr", `Java Runtime 重连失败: ${errorText(error)}`);
        if (this.desired) this.scheduleReconnect(1006);
      });
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private publishLifecycle(kind: "started" | "stderr" | "stopped", line: string): void {
    this.publish({ kind, line, timestamp: new Date().toISOString(), transport: "java_ws" });
  }
}

/** 只允许明文回环 desktop.v1 地址，并要求至少三十二字符的每次启动令牌。 */
export function validateEndpoint(url: string, token: string): void {
  const endpoint = new URL(url);
  if (endpoint.protocol !== "ws:" || endpoint.hostname !== "127.0.0.1") {
    throw new Error("Java desktop.v1 只允许 ws://127.0.0.1 回环地址");
  }
  if (endpoint.pathname !== "/desktop/v1" || endpoint.search || endpoint.hash) {
    throw new Error("Java desktop.v1 路径必须精确为 /desktop/v1");
  }
  const port = Number(endpoint.port);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error("Java desktop.v1 端口无效");
  }
  if (token.length < 32) throw new Error("Java desktop.v1 启动令牌至少需要三十二个字符");
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
