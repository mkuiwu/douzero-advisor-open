import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

import type { DesktopSettings, ListenerEnvelope } from "../src/shared/contracts";
import { JavaRuntimeClient } from "../src/main/java-runtime-client";

const settings: DesktopSettings = {
  modelBackend: "resnet2",
  inputMode: "advice",
  actionAdvice: true,
  preplayAdvice: true,
  autoPreplayButtons: false,
  autoSettlementChangeTable: false,
};

void main();

async function main(): Promise<void> {
  const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const repositoryRoot = resolve(desktopRoot, "..");
  const classpathFile = resolve(repositoryRoot, "java-runtime", "target", "desktop-ws-classpath.txt");
  const token = randomBytes(32).toString("hex");
  const mavenArguments = [
    "-q",
    "-pl", "java-runtime",
    "-am",
    "test-compile",
    "dependency:build-classpath",
    `-Dmdep.outputFile=${classpathFile}`,
    "-DincludeScope=test",
  ];
  // Windows 的 mvn.cmd 必须通过 cmd 调用；使用参数数组避免 shell 拼接与转义风险。
  const isWindows = process.platform === "win32";
  const mavenCommand = isWindows ? (process.env.ComSpec ?? "cmd.exe") : "mvn";
  const mavenCommandArguments = isWindows
    ? ["/d", "/c", "call", "mvn.cmd", ...mavenArguments]
    : mavenArguments;
  const compile = spawnSync(
  mavenCommand,
  mavenCommandArguments,
  {
    cwd: repositoryRoot,
    encoding: "utf8",
  },
  );
  if (compile.status !== 0) {
    process.stderr.write(compile.stdout ?? "");
    process.stderr.write(compile.stderr ?? "");
    const detail = compile.error?.message ?? `退出码 ${compile.status ?? "未知"}`;
    throw new Error(`Java 跨语言验收编译失败：${detail}`);
  }

  const dependencyClasspath = readFileSync(classpathFile, "utf8").trim();
  const classpath = [
    resolve(repositoryRoot, "java-runtime", "target", "test-classes"),
    resolve(repositoryRoot, "java-runtime", "target", "classes"),
    dependencyClasspath,
  ].join(process.platform === "win32" ? ";" : ":");
  const java = spawn(
  "java",
  [
    "-cp", classpath,
    "com.mkuiwu.douzero.runtime.infrastructure.desktop.DesktopWebSocketIntegrationServer",
    token,
  ],
  { cwd: repositoryRoot, stdio: ["pipe", "pipe", "pipe"] },
  );

  try {
    const port = await readPort(java);
    const endpoint = `ws://127.0.0.1:${port}/desktop/v1`;
    const readyCheck = spawnSync(
      process.execPath,
      [resolve(desktopRoot, "scripts", "wait-java-ready.mjs")],
      {
        cwd: desktopRoot,
        encoding: "utf8",
        env: {
          ...process.env,
          DOUZERO_DESKTOP_WS_URL: endpoint,
          DOUZERO_DESKTOP_WS_TOKEN: token,
        },
      },
    );
    if (readyCheck.status !== 0) {
      process.stderr.write(readyCheck.stdout);
      process.stderr.write(readyCheck.stderr);
      throw new Error(`联合启动 READY 校验失败，退出码 ${readyCheck.status ?? "未知"}`);
    }
    const published: ListenerEnvelope[] = [];
    const client = new JavaRuntimeClient(
      endpoint,
      token,
      (message) => published.push(message),
    );
    const result = await client.start(settings);
    if (!result.running) throw new Error("Electron 客户端未连接真实 Java 网关");
    await waitFor(() => published.some((item) =>
      item.kind === "desktop_event"
        && item.event.type === "PHASE_CHANGED"
        && item.event.dealId === "integration-deal-1"));
    client.stop();
    process.stdout.write("PASS Java gateway -> Electron main client desktop.v1\n");
  } finally {
    java.stdin.write("\n");
    java.stdin.end();
    await new Promise<void>((resolveExit) => java.once("exit", () => resolveExit()));
  }
}

async function readPort(process: ReturnType<typeof spawn>): Promise<number> {
  const lines = createInterface({ input: process.stdout });
  return new Promise<number>((resolvePort, rejectPort) => {
    const timeout = setTimeout(() => rejectPort(new Error("截止时间内未获得 Java 网关端口")), 5000);
    lines.on("line", (line) => {
      const match = /^DESKTOP_WS_PORT=(\d+)$/.exec(line);
      if (!match) return;
      clearTimeout(timeout);
      lines.close();
      resolvePort(Number(match[1]));
    });
    process.once("exit", (code) => {
      clearTimeout(timeout);
      rejectPort(new Error(`Java 网关在发布端口前退出，代码 ${code ?? "未知"}`));
    });
  });
}

async function waitFor(condition: () => boolean): Promise<void> {
  const deadline = Date.now() + 5000;
  while (!condition()) {
    if (Date.now() >= deadline) throw new Error("截止时间内未收到 Java 权威阶段事件");
    await new Promise((resolveWait) => setTimeout(resolveWait, 10));
  }
}
