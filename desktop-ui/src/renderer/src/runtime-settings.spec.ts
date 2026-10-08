import { reactive } from "vue";
import { describe, expect, it } from "vitest";

import { copyRuntimeSettings } from "./runtime-settings";

describe("Java Runtime 启动配置 IPC 边界", () => {
  /** 验证 Vue Proxy 在发送给 Electron 前会被展开为结构化克隆可接受的普通对象。 */
  it("将响应式建议开关转换为可克隆的普通配置", () => {
    const reactiveSettings = reactive({
      modelBackend: "resnet2" as const,
      preplayAdvice: true,
      formalPlayAdvice: false,
      autoPlay: true,
      autoPreplayButtons: true,
    });

    const copied = copyRuntimeSettings(reactiveSettings);

    expect(structuredClone(copied)).toEqual({
      modelBackend: "resnet2",
      preplayAdvice: true,
      formalPlayAdvice: false,
      autoPlay: true,
      autoPreplayButtons: true,
    });
  });
});
