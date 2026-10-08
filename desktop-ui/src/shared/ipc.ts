/** renderer 请求启动 Electron 所拥有的 Java 与 Python 服务树。 */
export const START_RUNTIME = "desktop:start-runtime";
/** renderer 请求停止 Electron 本次创建的 Java 与 Python 服务树。 */
export const STOP_RUNTIME = "desktop:stop-runtime";
/** 主进程向 renderer 单向发布 listener 消息的 IPC 通道。 */
export const LISTENER_MESSAGE = "desktop:listener-message";
/** renderer 请求只读检查游戏窗口尺寸的 IPC 通道。 */
export const INSPECT_GAME_WINDOW = "desktop:inspect-game-window";
/** renderer 在用户点击检测且尺寸不符后请求调整游戏窗口客户区的 IPC 通道。 */
export const ADJUST_GAME_WINDOW = "desktop:adjust-game-window";
