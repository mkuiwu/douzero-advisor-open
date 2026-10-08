import { randomBytes } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { createServer, connect } from "node:net";
import { resolve } from "node:path";
import { createInterface } from "node:readline";

import type {
  DesktopSettings,
  LegacyListenerEnvelope,
  ListenerActionResult,
  ListenerEnvelope,
  RuntimeSettings,
} from "../shared/contracts";
import { JavaRuntimeClient } from "./java-runtime-client";

const READY_TIMEOUT_MS = 75_000;
const PORT_PROBE_TIMEOUT_MS = 300;
const DEFAULT_READ_ONLY_SETTINGS: DesktopSettings = {
  modelBackend: "resnet2",
  inputMode: "advice",
  actionAdvice: true,
  preplayAdvice: false,
  autoPreplayButtons: false,
  autoSettlementChangeTable: false,
};

/** Electron 主进程拥有 Java 及其 Python 子进程树；renderer 只能请求启动或停止。 */
export class RuntimeSupervisor {
  private process: ChildProcess | null = null;
  private client: JavaRuntimeClient | null = null;
  private startOperation: Promise<ListenerActionResult> | null = null;

  public constructor(
    private readonly repositoryRoot: string,
    private readonly publish: (message: ListenerEnvelope) => void,
  ) {}

  /** 构建并启动完整只读 Runtime，等待 Java desktop.v1 READY 后才向界面报告可用。 */
  public start(rawSettings: unknown): Promise<ListenerActionResult> {
    if (this.client?.isRunning()) {
      return Promise.resolve({ running: true, message: "Java 与 Python 服务已启动" });
    }
    if (this.startOperation) return this.startOperation;
    let settings: RuntimeSettings;
    try {
      settings = validateRuntimeSettings(rawSettings);
    } catch (error) {
      return Promise.resolve({
        running: false,
        message: error instanceof Error ? error.message : String(error),
      });
    }
    this.startOperation = this.startOwnedRuntime(settings).finally(() => {
      this.startOperation = null;
    });
    return this.startOperation;
  }

  /** 停止 Electron 本次创建的 cmd 进程树，并主动断开只读桌面连接。 */
  public async stop(): Promise<ListenerActionResult> {
    const client = this.client;
    this.client = null;
    if (client?.isRunning()) client.stop();
    const child = this.process;
    this.process = null;
    if (!child || child.exitCode !== null) {
      this.publishMessage("stopped", "Java Runtime 服务未运行");
      return { running: false, message: "服务未运行" };
    }
    await terminateProcessTree(child);
    this.publishMessage("stopped", "已停止本界面启动的 Java 与 Python 服务");
    return { running: false, message: "服务已停止" };
  }

  /** Runtime 必须同时完成 Java READY 与桌面鉴权，才视为运行中。 */
  public isRunning(): boolean {
    return this.client?.isRunning() ?? false;
  }

  private async startOwnedRuntime(settings: RuntimeSettings): Promise<ListenerActionResult> {
    const port = await reserveLoopbackPort();
    const token = randomBytes(32).toString("hex");
    const url = `ws://127.0.0.1:${port}/desktop/v1`;
    const launcher = resolve(this.repositoryRoot, "Run-DouZeroRuntime.cmd");
    // 将 call 和 .cmd 路径拆成独立参数；把带引号路径拼为单一 `/c` 命令会在 Windows
    // 再次转义，导致 cmd 把引号当作命令名的一部分，服务尚未启动就退出。
    const child = spawn("cmd.exe", ["/d", "/c", "call", launcher, ...runtimeLaunchArguments(settings)], {
      cwd: this.repositoryRoot,
      env: {
        ...process.env,
        DOUZERO_REPO_ROOT: this.repositoryRoot,
        DOUZERO_DESKTOP_WS_ENABLED: "true",
        DOUZERO_DESKTOP_WS_PORT: String(port),
        DOUZERO_DESKTOP_WS_URL: url,
        DOUZERO_DESKTOP_WS_TOKEN: token,
      },
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.process = child;
    this.attachProcessOutput(child);
    // 进程已创建不等于服务已就绪；只有 JavaRuntimeClient 收到 READY 才发布 started。
    this.publishMessage("stdout", "正在构建并启动 Java Runtime 与 Python 服务");

    const client = new JavaRuntimeClient(url, token, this.publish);
    this.client = client;
    const ready = await this.waitForReady(child, client, port);
    if (ready) return { running: true, message: "Java 与 Python 服务已启动" };

    const launcherExited = child.exitCode !== null || this.process !== child;
    await this.stop();
    return {
      running: false,
      message: launcherExited
        ? "Java Runtime 启动器提前退出，请在诊断与日志查看原因"
        : "服务未能在截止时间内完成启动",
    };
  }

  private async waitForReady(
    child: ChildProcess,
    client: JavaRuntimeClient,
    port: number,
  ): Promise<boolean> {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline && child.exitCode === null && this.process === child) {
      if (await isPortOpen(port)) {
        const result = await client.start(DEFAULT_READ_ONLY_SETTINGS);
        if (result.running) return true;
      }
      await delay(250);
    }
    if (child.exitCode !== null || this.process !== child) {
      this.publishMessage(
        "stderr",
        `Java Runtime 启动器提前退出，退出码 ${child.exitCode ?? "未知"}`,
      );
      return false;
    }
    this.publishMessage("stderr", "Java Runtime 未在 75 秒内通过 desktop.v1 READY 验证");
    return false;
  }

  private attachProcessOutput(child: ChildProcess): void {
    if (child.stdout) {
      createInterface({ input: child.stdout }).on("line", (line) => this.publishMessage("stdout", line));
    }
    if (child.stderr) {
      createInterface({ input: child.stderr }).on("line", (line) => this.publishMessage("stderr", line));
    }
    child.once("error", (error) => this.publishMessage("stderr", `${error.name}: ${error.message}`));
    child.once("exit", (exitCode) => {
      if (this.process !== child) return;
      this.process = null;
      this.client?.stop();
      this.client = null;
      this.publish({
        kind: "exit",
        timestamp: new Date().toISOString(),
        line: exitCode === 0 ? "Java Runtime 正常退出" : `Java Runtime 退出码 ${exitCode ?? "未知"}`,
        exitCode,
        transport: "java_ws",
      });
    });
  }

  private publishMessage(kind: LegacyListenerEnvelope["kind"], line: string): void {
    this.publish({ kind, line, timestamp: new Date().toISOString(), transport: "java_ws" });
  }
}

/** 只由前端选择普通建议能力；固定自动不出完全由 Java application.yml 管理。 */
export function runtimeLaunchArguments(settings: RuntimeSettings): string[] {
  return [
    `--douzero.preplay-model.enabled=${settings.preplayAdvice}`,
    `--douzero.model.enabled=${settings.formalPlayAdvice}`,
    `--douzero.execution.card-play-enabled=${settings.autoPlay}`,
    `--douzero.execution.preplay-buttons-enabled=${settings.autoPreplayButtons}`,
  ];
}

/** 只接受固定 Java Runtime 能力，避免 renderer 注入 Spring 参数或未知模型。 */
export function validateRuntimeSettings(value: unknown): RuntimeSettings {
  if (!value || typeof value !== "object") {
    throw new Error("启动配置无效");
  }
  const candidate = value as Partial<RuntimeSettings>;
  if (candidate.modelBackend !== "resnet2") {
    throw new Error("当前 Java Runtime 只支持 ResNet2 正式出牌模型");
  }
  if (typeof candidate.preplayAdvice !== "boolean" || typeof candidate.formalPlayAdvice !== "boolean") {
    throw new Error("局前和正式出牌能力必须明确选择");
  }
  // autoPlay 为可选字段，旧 renderer 不发送时默认 false 保持只读。
  if (candidate.autoPlay !== undefined && typeof candidate.autoPlay !== "boolean") {
    throw new Error("自动选牌开关必须为布尔值");
  }
  if (candidate.autoPreplayButtons !== undefined && typeof candidate.autoPreplayButtons !== "boolean") {
    throw new Error("局前按钮自动点击开关必须为布尔值");
  }
  if (candidate.autoPreplayButtons === true && candidate.preplayAdvice !== true) {
    throw new Error("开启局前按钮自动点击时必须同时开启局前建议");
  }
  return {
    modelBackend: "resnet2",
    preplayAdvice: candidate.preplayAdvice,
    formalPlayAdvice: candidate.formalPlayAdvice,
    autoPlay: candidate.autoPlay ?? false,
    autoPreplayButtons: candidate.autoPreplayButtons ?? false,
  };
}

/** 仅预留回环端口；令牌与端口只通过本次 Runtime 子进程环境传递。 */
async function reserveLoopbackPort(): Promise<number> {
  const server = createServer();
  server.listen({ host: "127.0.0.1", port: 0 });
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("无法分配 Java desktop.v1 回环端口");
  }
  await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
  return address.port;
}

/** Java 网关尚未监听时静默等待，避免构建阶段向 UI 重复刷写连接失败。 */
function isPortOpen(port: number): Promise<boolean> {
  return new Promise((resolveOpen) => {
    const socket = connect({ host: "127.0.0.1", port });
    let settled = false;
    const finish = (open: boolean): void => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolveOpen(open);
    };
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(PORT_PROBE_TIMEOUT_MS, () => finish(false));
  });
}

/** 只终止本实例创建的 cmd PID 及其子进程，不影响用户独立启动的 Runtime。 */
function terminateProcessTree(child: ChildProcess): Promise<void> {
  if (!child.pid) return Promise.resolve();
  return new Promise((resolveTerminate) => {
    const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    killer.once("error", () => resolveTerminate());
    killer.once("close", () => resolveTerminate());
  });
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}
