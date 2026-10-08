import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { resolve } from "node:path";

import type {
  DesktopSettings,
  ListenerActionResult,
  ListenerEnvelope,
  LegacyListenerEnvelope,
} from "../shared/contracts";
import { listenerArguments, pythonPath, resolvePythonExecutable } from "./listener-command";
import type { RuntimeTransport } from "./runtime-transport";

/** Electron 主进程拥有的唯一旧 Python listener。 */
export class ListenerProcess implements RuntimeTransport {
  private process: ChildProcessWithoutNullStreams | null = null;

  public constructor(
    private readonly repositoryRoot: string,
    private readonly publish: (message: ListenerEnvelope) => void,
  ) {}

  /** 启动一次监听；重复启动只返回当前运行状态。 */
  public start(settings: DesktopSettings): ListenerActionResult {
    if (this.process && this.process.exitCode === null) {
      return { running: true, message: "监听器已经运行" };
    }
    const python = resolvePythonExecutable(this.repositoryRoot);
    const script = resolve(this.repositoryRoot, "scripts", "run_live_advisor.py");
    const args = ["-u", script, "--json", ...listenerArguments(settings)];
    const child = spawn(python, args, {
      cwd: this.repositoryRoot,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath(this.repositoryRoot),
        PYTHONUNBUFFERED: "1",
      },
      shell: false,
      windowsHide: true,
    });
    this.process = child;
    this.publishMessage("started", `主模型 ${settings.modelBackend} · 模式 ${settings.inputMode}`);

    createInterface({ input: child.stdout }).on("line", (line) => {
      this.publishMessage("stdout", line);
    });
    createInterface({ input: child.stderr }).on("line", (line) => {
      this.publishMessage("stderr", line);
    });
    child.once("error", (error) => {
      this.publishMessage("stderr", `${error.name}: ${error.message}`);
    });
    child.once("exit", (exitCode) => {
      if (this.process === child) this.process = null;
      this.publish({
        kind: "exit",
        timestamp: new Date().toISOString(),
        line: exitCode === 0 ? "监听器正常退出" : `监听器退出码 ${exitCode ?? "未知"}`,
        exitCode,
        transport: "python_jsonl",
      });
    });
    return { running: true, message: "监听器已启动" };
  }

  /** 停止当前 listener；只终止本进程拥有的子进程。 */
  public stop(): ListenerActionResult {
    const child = this.process;
    if (!child || child.exitCode !== null) {
      this.process = null;
      return { running: false, message: "监听器未运行" };
    }
    child.kill("SIGTERM");
    this.publishMessage("stopped", "已请求停止监听器");
    return { running: false, message: "监听器正在停止" };
  }

  /** 返回当前 listener 是否仍允许继续读取输出。 */
  public isRunning(): boolean {
    return this.process !== null && this.process.exitCode === null;
  }

  private publishMessage(kind: LegacyListenerEnvelope["kind"], line: string): void {
    this.publish({ kind, line, timestamp: new Date().toISOString(), transport: "python_jsonl" });
  }
}
