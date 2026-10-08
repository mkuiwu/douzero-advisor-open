import type { DesktopApi } from "../../shared/contracts";

declare global {
  interface Window {
    /** Electron preload 暴露的受限桌面能力。 */
    desktopApi: DesktopApi;
  }
}

export {};
