import { join, resolve } from "node:path";

import { app, BrowserWindow, dialog, ipcMain } from "electron";

import {
  ADJUST_GAME_WINDOW,
  INSPECT_GAME_WINDOW,
  LISTENER_MESSAGE,
  START_RUNTIME,
  STOP_RUNTIME,
} from "../shared/ipc";
import type { ListenerEnvelope } from "../shared/contracts";
import { queryGameWindow } from "./geometry";
import { resolveRepositoryRoot } from "./listener-command";
import { RuntimeSupervisor } from "./runtime-supervisor";

let mainWindow: BrowserWindow | null = null;
let runtime: RuntimeSupervisor | null = null;

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 800,
    height: 900,
    minWidth: 700,
    minHeight: 700,
    backgroundColor: "#0b1220",
    show: false,
    title: "DouZero Advisor",
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    void window.loadFile(join(__dirname, "../renderer/index.html"));
  }
  return window;
}

function repositoryRoot(): string {
  return resolveRepositoryRoot([
    process.env.DOUZERO_REPO_ROOT ?? "",
    process.cwd(),
    resolve(app.getAppPath(), ".."),
    resolve(app.getAppPath(), "../.."),
    "C:\\work\\douzero-advisor",
  ]);
}

function registerIpc(root: string): void {
  const publish = (message: ListenerEnvelope): void => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(LISTENER_MESSAGE, message);
    }
  };
  runtime = new RuntimeSupervisor(root, publish);
  ipcMain.handle(START_RUNTIME, (_event, rawSettings: unknown) => runtime?.start(rawSettings));
  ipcMain.handle(STOP_RUNTIME, () => runtime?.stop());
  ipcMain.handle(INSPECT_GAME_WINDOW, () => queryGameWindow(root, "inspect"));
  ipcMain.handle(ADJUST_GAME_WINDOW, () => queryGameWindow(root, "adjust"));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  void app.whenReady().then(() => {
    registerIpc(repositoryRoot());
    mainWindow = createWindow();
  }).catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error("DouZero Advisor 启动失败:", error);
    dialog.showErrorBox("DouZero Advisor 启动失败", message);
    app.exit(1);
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => {
    void runtime?.stop();
  });
}
