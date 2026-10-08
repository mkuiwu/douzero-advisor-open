import { execFile } from "node:child_process";
import { resolve } from "node:path";

import type { GameWindowGeometry } from "../shared/contracts";
import { pythonPath, resolvePythonExecutable } from "./listener-command";

/** 通过受控 Python helper 检查或按显式请求调整游戏窗口，不向 renderer 暴露系统调用。 */
export function queryGameWindow(
  repositoryRoot: string,
  action: "inspect" | "adjust",
): Promise<GameWindowGeometry> {
  const python = resolvePythonExecutable(repositoryRoot);
  const script = resolve(repositoryRoot, "scripts", "desktop_window_geometry.py");
  return new Promise((accept, reject) => {
    execFile(
      python,
      [script, action],
      {
        cwd: repositoryRoot,
        env: { ...process.env, PYTHONPATH: pythonPath(repositoryRoot) },
        encoding: "utf8",
        windowsHide: true,
        timeout: 10_000,
      },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(stderr.trim() || error.message));
          return;
        }
        try {
          accept(JSON.parse(stdout) as GameWindowGeometry);
        } catch (parseError) {
          reject(new Error(`窗口检查返回无效 JSON: ${String(parseError)}`));
        }
      },
    );
  });
}
