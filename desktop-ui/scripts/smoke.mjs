import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { _electron as electron } from "playwright-core";

const require = createRequire(import.meta.url);
const executablePath = require("electron");
const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repositoryRoot = resolve(desktopRoot, "..");
const errors = [];

const application = await electron.launch({
  executablePath,
  args: [resolve(desktopRoot, "out", "main", "index.js")],
  cwd: desktopRoot,
  env: {
    ...process.env,
    DOUZERO_REPO_ROOT: process.env.DOUZERO_REPO_ROOT || repositoryRoot,
  },
});

try {
  const page = await application.firstWindow();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (entry) => {
    if (entry.type() === "error") errors.push(entry.text());
  });
  await page.waitForLoadState("domcontentloaded");

  // 验证生产构建能打开真正的 Electron 窗口，并显示默认启动配置页。
  if ((await page.title()) !== "DouZero Advisor") throw new Error("桌面窗口标题不正确");
  await page.getByText("开始一局监控", { exact: true }).waitFor();

  // 验证三个工作区使用同一 renderer 状态切换，不会导航到空白页或外部地址。
  await page.getByText("实时建议", { exact: true }).first().click();
  await page.locator("h1").getByText("实时建议", { exact: true }).waitFor();
  await page.getByText("诊断与日志", { exact: true }).first().click();
  await page.locator("h1").getByText("诊断与日志", { exact: true }).waitFor();
  await page.getByText("启动配置", { exact: true }).click();
  await page.locator("h1").getByText("开始一局监控", { exact: true }).waitFor();

  if (errors.length) throw new Error(`renderer 错误: ${errors.join(" | ")}`);
  console.log("Electron smoke PASS: 启动配置、实时建议、诊断与日志均可访问");
} finally {
  await application.close();
}
