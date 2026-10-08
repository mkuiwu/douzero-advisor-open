import { describe, expect, it } from "vitest";

import { runtimeLaunchArguments, validateRuntimeSettings } from "./runtime-supervisor";

describe("Java Runtime 启动配置", () => {
  /** 验证 Electron 只接受建议、自动选牌和局前按钮开关，不能注入其他参数。 */
  it("仅接受固定 ResNet2 与明确的局前、正式建议和执行开关", () => {
    expect(validateRuntimeSettings({
      modelBackend: "resnet2",
      preplayAdvice: false,
      formalPlayAdvice: true,
      autoPlay: true,
      autoPreplayButtons: false,
    })).toEqual({
      modelBackend: "resnet2",
      preplayAdvice: false,
      formalPlayAdvice: true,
      autoPlay: true,
      autoPreplayButtons: false,
    });
    expect(() => validateRuntimeSettings({
      modelBackend: "original",
      preplayAdvice: true,
      formalPlayAdvice: true,
      autoPreplayButtons: false,
    })).toThrow("当前 Java Runtime 只支持 ResNet2 正式出牌模型");
    expect(() => validateRuntimeSettings({
      modelBackend: "resnet2",
      preplayAdvice: "false",
      formalPlayAdvice: true,
    })).toThrow("局前和正式出牌能力必须明确选择");
  });

  /** 验证旧 renderer 不发送 autoPlay 时默认 false，保持只读模式向后兼容。 */
  it("旧 renderer 未发送 autoPlay 时默认只读", () => {
    expect(validateRuntimeSettings({
      modelBackend: "resnet2",
      preplayAdvice: true,
      formalPlayAdvice: true,
    }).autoPlay).toBe(false);
  });

  /** 验证 autoPlay 必须为布尔值，防止注入非法类型。 */
  it("拒绝非布尔 autoPlay", () => {
    expect(() => validateRuntimeSettings({
      modelBackend: "resnet2",
      preplayAdvice: true,
      formalPlayAdvice: true,
      autoPlay: "true",
    })).toThrow("自动选牌开关必须为布尔值");
  });

  /** 验证局前按钮自动点击不能脱离局前建议能力单独开启。 */
  it("局前建议关闭时拒绝自动点击局前按钮", () => {
    expect(() => validateRuntimeSettings({
      modelBackend: "resnet2",
      preplayAdvice: false,
      formalPlayAdvice: true,
      autoPreplayButtons: true,
    })).toThrow("必须同时开启局前建议");
  });

  it("固定自动不出不由 Electron 生成启动参数", () => {
    // 场景：用户仅开启普通自动选牌。预期：前端只传普通能力，不能覆盖 application.yml 的 PASS 策略。
    const argumentsForRuntime = runtimeLaunchArguments({
      modelBackend: "resnet2",
      preplayAdvice: true,
      formalPlayAdvice: true,
      autoPlay: true,
      autoPreplayButtons: false,
    });

    expect(argumentsForRuntime).toContain("--douzero.execution.card-play-enabled=true");
    expect(argumentsForRuntime).not.toContain("--douzero.execution.enabled=true");
    expect(argumentsForRuntime.some((value) => value.includes("auto-pass"))).toBe(false);
  });
});
