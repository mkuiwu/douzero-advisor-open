import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { DesktopSettings } from "../shared/contracts";
import {
  listenerArguments,
  resolveRepositoryRoot,
  validateDesktopSettings,
} from "./listener-command";

const readOnlySettings: DesktopSettings = {
  modelBackend: "original",
  inputMode: "advice",
  actionAdvice: true,
  preplayAdvice: true,
  autoPreplayButtons: false,
  autoSettlementChangeTable: false,
};

describe("listener command boundary", () => {
  /** 验证默认只读桌面配置只能生成固定 listener 参数，不能出现自动提交出牌能力。 */
  it("只生成受控的只读 listener 参数", () => {
    expect(listenerArguments(readOnlySettings)).toEqual([
      "--model-backend",
      "original",
      "--action-recovery-live",
      "--preplay-advice",
    ]);
    expect(listenerArguments(readOnlySettings)).not.toContain("--auto-play");
  });

  /** 验证选择牌模式只增加不提交的选牌参数，不会隐式开启其它自动化开关。 */
  it("选择牌模式保持不提交边界", () => {
    expect(listenerArguments({ ...readOnlySettings, inputMode: "select" })).toEqual([
      "--model-backend",
      "original",
      "--action-recovery-live",
      "--preplay-advice",
      "--auto-select-cards",
    ]);
  });

  /** 验证 renderer 不能提交任意模型名或额外字段把 IPC 变成命令注入入口。 */
  it("拒绝不受支持的模型配置", () => {
    expect(() => validateDesktopSettings({ ...readOnlySettings, modelBackend: "x;calc" })).toThrow(
      "不支持的模型后端",
    );
  });

  /** 验证关闭局前建议时不能单独启用局前按钮操作，避免缺少建议依据的点击。 */
  it("拒绝无局前建议依据的按钮自动化", () => {
    expect(() =>
      validateDesktopSettings({
        ...readOnlySettings,
        preplayAdvice: false,
        autoPreplayButtons: true,
      }),
    ).toThrow("自动局前按钮依赖局前建议");
  });

  /** 清理旧 Python listener 后，仓库定位必须只依赖仍发布的 Java Runtime 启动器。 */
  it("使用当前 Java Runtime 启动器识别仓库根目录", () => {
    const root = mkdtempSync(join(tmpdir(), "douzero-advisor-root-"));
    try {
      mkdirSync(join(root, "scripts"));
      writeFileSync(join(root, "Run-DouZeroRuntime.cmd"), "@echo off\r\n");

      expect(resolveRepositoryRoot([join(root, "missing"), root])).toBe(root);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
