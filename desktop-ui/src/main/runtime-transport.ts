import type { DesktopSettings, ListenerActionResult, ListenerEnvelope } from "../shared/contracts";

/** Electron 主进程可选择的运行时传输；Java WS 与旧 Python JSONL 必须显式二选一。 */
export interface RuntimeTransport {
  /** 启动或连接一次只读监控。 */
  start(settings: DesktopSettings): ListenerActionResult | Promise<ListenerActionResult>;
  /** 停止或断开当前只读监控。 */
  stop(): ListenerActionResult | Promise<ListenerActionResult>;
  /** 返回子进程或连接是否仍处于运行/连接状态。 */
  isRunning(): boolean;
}

/** 主进程向受限 IPC 发布运行时消息的回调。 */
export type RuntimePublisher = (message: ListenerEnvelope) => void;
