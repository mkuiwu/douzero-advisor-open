import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";
import { WebSocketServer } from "ws";

let server: WebSocketServer | null = null;

afterEach(async () => {
  if (!server) return;
  const closing = once(server, "close");
  server.close();
  await closing;
  server = null;
});

describe("联合启动 READY 门禁", () => {
  // 验证联合启动器只有在强令牌鉴权成功且 Java 声明完整只读编排后才会继续启动界面。
  it("等待经过鉴权且启用完整编排的 desktop.v1 READY", async () => {
    const token = "a".repeat(64);
    let authorization: string | undefined;
    server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
    await once(server, "listening");
    const address = server.address();
    if (typeof address === "string" || address === null) throw new Error("测试服务未绑定 TCP 端口");
    const ready = await readFile(resolve("../contracts/desktop/v1/ready.example.json"), "utf8");
    server.on("connection", (socket, request) => {
      authorization = request.headers.authorization;
      socket.send(ready);
    });

    const child = spawn(process.execPath, [resolve("scripts/wait-java-ready.mjs")], {
      cwd: resolve("."),
      env: {
        ...process.env,
        DOUZERO_DESKTOP_WS_URL: `ws://127.0.0.1:${address.port}/desktop/v1`,
        DOUZERO_DESKTOP_WS_TOKEN: token,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    const [exitCode] = await once(child, "close");

    expect(exitCode, stderr).toBe(0);
    expect(authorization).toBe(`Bearer ${token}`);
    expect(stdout).toContain("PASS Java Runtime desktop.v1 READY");
    expect(stdout).not.toContain(token);
  });
});
