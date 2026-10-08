import { delimiter, resolve } from "node:path";
import { existsSync } from "node:fs";

import type { DesktopSettings } from "../shared/contracts";

/** 校验 renderer 提交的配置，禁止通过 IPC 注入任意命令参数。 */
export function validateDesktopSettings(value: unknown): DesktopSettings {
  if (typeof value !== "object" || value === null) {
    throw new Error("桌面监听配置必须是对象");
  }
  const candidate = value as Partial<DesktopSettings>;
  if (candidate.modelBackend !== "original" && candidate.modelBackend !== "resnet2") {
    throw new Error("不支持的模型后端");
  }
  if (candidate.inputMode !== "advice" && candidate.inputMode !== "select") {
    throw new Error("不支持的运行方式");
  }
  for (const key of [
    "actionAdvice",
    "preplayAdvice",
    "autoPreplayButtons",
    "autoSettlementChangeTable",
  ] as const) {
    if (typeof candidate[key] !== "boolean") {
      throw new Error(`桌面监听配置 ${key} 必须是布尔值`);
    }
  }
  if (candidate.autoPreplayButtons && !candidate.preplayAdvice) {
    throw new Error("自动局前按钮依赖局前建议");
  }
  return candidate as DesktopSettings;
}

/** 将已校验的桌面设置转换成旧 listener 的固定参数集合。 */
export function listenerArguments(settings: DesktopSettings): string[] {
  const args = ["--model-backend", settings.modelBackend];
  if (settings.actionAdvice) args.push("--action-recovery-live");
  if (settings.preplayAdvice) args.push("--preplay-advice");
  if (settings.autoPreplayButtons) args.push("--auto-preplay-buttons");
  if (settings.autoSettlementChangeTable) args.push("--auto-settlement-change-table");
  if (settings.inputMode === "select") args.push("--auto-select-cards");
  return args;
}

/** 从受控候选目录中解析包含当前 Java Runtime 启动器的仓库根目录。 */
export function resolveRepositoryRoot(candidates: readonly string[]): string {
  for (const candidate of candidates) {
    if (candidate && existsSync(resolve(candidate, "Run-DouZeroRuntime.cmd"))) {
      return resolve(candidate);
    }
  }
  throw new Error("未找到 DouZero Advisor 仓库根目录");
}

/** 优先使用项目虚拟环境中的 Python 3.11，避免加载全局不兼容依赖。 */
export function resolvePythonExecutable(repositoryRoot: string): string {
  const configured = process.env.DOUZERO_PYTHON;
  const candidates = [
    configured,
    resolve(repositoryRoot, ".venv", "Scripts", "python.exe"),
    resolve(repositoryRoot, ".venv", "bin", "python"),
  ].filter((value): value is string => Boolean(value));
  const resolved = candidates.find((candidate) => existsSync(candidate));
  if (!resolved) {
    throw new Error("未找到项目 .venv 中的 Python；请设置 DOUZERO_PYTHON");
  }
  return resolved;
}

/** 为 Python listener 构造只包含项目源码和固定上游目录的导入路径。 */
export function pythonPath(repositoryRoot: string): string {
  return [
    resolve(repositoryRoot, "src"),
    resolve(repositoryRoot, "vendor", "DouZero"),
  ].join(delimiter);
}
