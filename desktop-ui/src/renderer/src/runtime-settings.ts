import type { RuntimeSettings } from "../../shared/contracts";

/** 将 Vue 响应式启动配置转换为可跨 Electron IPC 传输的普通对象。 */
export function copyRuntimeSettings(settings: RuntimeSettings): RuntimeSettings {
  return {
    modelBackend: settings.modelBackend,
    preplayAdvice: settings.preplayAdvice,
    formalPlayAdvice: settings.formalPlayAdvice,
    autoPlay: settings.autoPlay,
    autoPreplayButtons: settings.autoPreplayButtons,
  };
}
