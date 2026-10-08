import { contextBridge, ipcRenderer } from "electron";

import type { DesktopApi, ListenerEnvelope, RuntimeSettings } from "../shared/contracts";
import {
  ADJUST_GAME_WINDOW,
  INSPECT_GAME_WINDOW,
  LISTENER_MESSAGE,
  START_RUNTIME,
  STOP_RUNTIME,
} from "../shared/ipc";

const api: DesktopApi = {
  startRuntime: (settings: RuntimeSettings) => ipcRenderer.invoke(START_RUNTIME, settings),
  stopRuntime: () => ipcRenderer.invoke(STOP_RUNTIME),
  inspectGameWindow: () => ipcRenderer.invoke(INSPECT_GAME_WINDOW),
  adjustGameWindow: () => ipcRenderer.invoke(ADJUST_GAME_WINDOW),
  onListenerMessage: (callback: (message: ListenerEnvelope) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, message: ListenerEnvelope): void => {
      callback(message);
    };
    ipcRenderer.on(LISTENER_MESSAGE, listener);
    return () => ipcRenderer.removeListener(LISTENER_MESSAGE, listener);
  },
};

contextBridge.exposeInMainWorld("desktopApi", api);
