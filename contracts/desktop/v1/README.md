# desktop.v1

`desktop.v1` 是 Java Runtime 到 Electron 主进程的本机只读事件协议。Java 是牌局状态、阶段和建议有效性的唯一权威；Vue 只展示经过 Electron 主进程校验的语义事件。

## 连接

- 地址固定为 `ws://127.0.0.1:<port>/desktop/v1`，不得绑定局域网地址。
- Electron 在 WebSocket 握手的 `Authorization` 请求头中发送 `Bearer <launch-token>`。令牌每次联合启动随机生成，至少三十二个字符，不进入 URL、renderer、日志或仓库配置。
- Java 鉴权成功后首先发送 `READY`。`orchestrationEnabled=false` 只证明网关连通，不表示牌局状态机已经工作。
- 客户端发送任何文本或二进制消息都会被关闭；本版本不提供出牌、点击或自动操作命令。

## 顺序与重连

- `protocol` 固定为 `desktop.v1`，未知版本必须拒绝，不能按宽松 JSON 猜测字段。
- `serverInstanceId` 在每次 Java 进程启动时变化。业务事件 `sequence` 在同一实例内从一开始单调递增；`READY.sequence` 固定为零且不参与业务去重。
- Electron 必须先接受 `READY`，再接受同一 `serverInstanceId` 的业务事件；同一实例内 `sequence` 小于或等于已消费序号的事件必须丢弃。
- 新连接会收到 `READY`，随后按原始序号回放当前阶段、最近局前建议、最近本方回合状态、最近正式建议和最近收口事件。回放不创建新业务事实。

## 事件

| 类型 | 语义 |
| --- | --- |
| `READY` | 声明 Java 服务实例和编排器启用状态。 |
| `PHASE_CHANGED` | 发布 Java 权威牌局阶段和当前 `dealId`。 |
| `PREPLAY_ADVICE` | 发布已经通过身份与阶段校验的局前只读建议；建议不是用户操作事实。 |
| `PLAY_TURN_STARTED` | Java 已确认轮到本方，正式模型正在生成新建议；展示层应立即清空上一条建议。 |
| `PLAY_ADVICE` | 发布完整 Java 语义快照以及可信建议或明确不可用结果。`recommendedAction.pass=true` 且 `cards=[]` 才表示不出。 |
| `GAME_FINISHED` | 发布当前 `GameContext` 的稳定收口原因。 |

本目录五份 JSON 是字段名和形状的规范样例。协议不使用通用 `payload` 或 `result` 容器；新增破坏性字段语义时必须升级协议版本。
