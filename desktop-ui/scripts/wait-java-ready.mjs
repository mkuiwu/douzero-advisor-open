import WebSocket from "ws";

const endpoint = process.env.DOUZERO_DESKTOP_WS_URL ?? "";
const token = process.env.DOUZERO_DESKTOP_WS_TOKEN ?? "";
const deadline = Date.now() + 90_000;

if (!endpoint.startsWith("ws://127.0.0.1:") || !endpoint.endsWith("/desktop/v1")) {
  throw new Error("联合启动只允许等待固定 desktop.v1 回环地址");
}
if (token.length < 32) throw new Error("联合启动缺少有效 desktop.v1 令牌");

await new Promise((resolve, reject) => {
  let lastError = "Java Runtime 尚未监听";
  let finished = false;

  const attempt = () => {
    if (finished) return;
    if (Date.now() >= deadline) {
      finished = true;
      reject(new Error(`等待 Java Runtime READY 超时：${lastError}`));
      return;
    }
    const socket = new WebSocket(endpoint, {
      headers: { Authorization: `Bearer ${token}` },
      handshakeTimeout: 2_000,
    });
    let ready = false;
    const readyTimeout = setTimeout(() => {
      lastError = "连接后未在两秒内收到 READY";
      socket.close(1002, "READY timeout");
    }, 2_000);
    socket.once("message", (data, binary) => {
      try {
        if (binary) throw new Error("READY 不得使用二进制消息");
        const event = JSON.parse(data.toString());
        if (event.protocol !== "desktop.v1" || event.type !== "READY") {
          throw new Error("首条消息不是 desktop.v1 READY");
        }
        if (event.orchestrationEnabled !== true) {
          throw new Error("Java Runtime 未启用完整只读编排");
        }
        ready = true;
        finished = true;
        clearTimeout(readyTimeout);
        socket.close(1000, "launcher ready check complete");
        resolve();
      } catch (error) {
        finished = true;
        clearTimeout(readyTimeout);
        socket.close(1002, "invalid READY");
        reject(error);
      }
    });
    socket.once("error", (error) => {
      lastError = error instanceof Error ? error.message : String(error);
    });
    socket.once("close", () => {
      clearTimeout(readyTimeout);
      if (!ready && !finished) setTimeout(attempt, 200);
    });
  };

  attempt();
});

console.log("PASS Java Runtime desktop.v1 READY");
