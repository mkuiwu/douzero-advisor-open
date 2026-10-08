# execution.v1

Java Core 与 Python 执行 Worker 使用 UTF-8 JSONL 长连接，协议风格与
`recognition.v1` 一致。每行都是扁平对象，禁止 `payload`、`result`、截图、ROI、
frame、capture generation、鼠标坐标、窗口句柄或任何像素位置字段。

执行 Worker 是独立进程，不加载模型、不维护牌局历史、不调度识别任务。它负责
Java 已授权的本方执行：按 Java 权威手牌选牌，或点击局前正向按钮。
所有鼠标后端、选牌几何、点击延迟和证据校验都是 Python 内部实现细节，不得出现在
协议中。

## 设计原则：按任务类型隔离执行

出牌执行只有一个接口 `CARD_PLAY`，由 `autoSubmit` 参数控制是否自动提交：

- `autoSubmit=false`：只选牌 + 视觉验证，不点击出牌按钮。用户在画面上确认后
  手动提交，用于建立信任的渐进模式。
- `autoSubmit=true`：选牌 + 视觉验证 + 点击出牌按钮 + 验证状态变化。

Python 内部仍按 选牌→验证→（条件）点击→验证 的两阶段逻辑执行，但 Java 侧只看到
单一任务、单一响应。这避免了两次网络往返和中间状态管理。

局前按钮使用独立的 `PREPLAY_BUTTON` 任务。只有模型明确建议 `call`、`rob`、
`double` 或 `super_double` 时才创建任务；`no_call`、`no_rob` 和 `no_double`
不会创建任务，也不会点击任何按钮。

Worker 启动后先输出：

```json
{"contractVersion":"execution.v1","messageType":"READY","taskTypes":["CARD_PLAY","PREPLAY_BUTTON"]}
```

## 安全边界

- **Java 是唯一权威**：每个 `CARD_PLAY` 必须携带 `dealId` 和 `generation`；
  Python 在执行任何点击前必须校验当前授权代际仍匹配，代际过期或会话关闭时立即拒绝。
- **手牌一致性硬校验**：`CARD_PLAY` 收到后必须先截图核对当前手牌与
  `authoritativeHand` 完全一致，不一致直接失败，不尝试选牌或点击。
- **证据新鲜度**：`autoSubmit=true` 时，点击前必须校验最近一次视觉证据时间不超过
  Python 内部阈值（默认 0.55 秒），证据过期即失败。
- **不确定即失败**：点击后无法确认结果时返回 `UNCERTAIN`，Java 必须重新走
  LOCAL_TURN 识别当前状态，绝不能假设成功。
- **局前负向动作禁点**：`PREPLAY_BUTTON` 只接受与阶段匹配的正向动作；不叫、
  不抢和不加倍在协议解析层直接拒绝，避免误点。
- **只读模式默认**：Java 侧配置 `execution.enabled` 默认为 `false`；未启用时
  不会创建执行 Worker 进程，也不会提交任何执行任务。

## 公共字段

- `contractVersion`：固定 `execution.v1`。
- `messageType`：Java 命令为 `SUBMIT`/`CANCEL`；Worker 输出为 `READY`/`RESULT`。
- `taskType`：`CARD_PLAY` 或 `PREPLAY_BUTTON`。
- `requestId`：Java 生成的全局唯一任务 ID；重复提交明确失败。
- `deadlineMs`：Worker 收到 SUBMIT 后的最长任务时间，单位为毫秒。
- `dealId`/`generation`：Java 的本局身份；所有执行任务必填。
- `status`：RESULT 为 `OK`、`FAILED` 或 `UNCERTAIN`。`UNCERTAIN` 只在
  `autoSubmit=true` 且可能已点击但无法确认结果时出现。

`CANCEL` 携带原任务的 `contractVersion`/`messageType`/`taskType`/`requestId`/
`dealId`/`generation`。取消操作幂等。

## CARD_PLAY

执行一次出牌动作。根据 `actionType` 和 `autoSubmit` 决定是否选牌、是否点击。

### 请求字段

- `localSeat`：`landlord`、`landlord_down` 或 `landlord_up`。
- `authoritativeHand`：Java 权威当前手牌，牌面使用 `3..10,J,Q,K,A,2,X,D`。
  Python 必须先截图核对与此完全一致，不一致即失败。
- `actionType`：`PLAY` 或 `PASS`。
- `recommendedCards`：建议出的牌，必须是 `authoritativeHand` 的子集。
  `PLAY` 时必填且非空；`PASS` 时必须省略或为空。
- `autoSubmit`：是否自动点击出牌按钮。`false` 时只选牌不点击（仅 `PLAY` 有意义）；
  `true` 时选牌后自动点击并验证状态变化。

### 成功结果

```json
{"contractVersion":"execution.v1","messageType":"RESULT","taskType":"CARD_PLAY",
 "requestId":"...","dealId":"...","generation":1,"status":"OK",
 "autoSubmitEcho":false,
 "verifiedAt":"2026-08-29T10:00:00Z"}
```

- `autoSubmitEcho`：回显请求中的 `autoSubmit` 值，供 Java 审计。
- `verifiedAt`：Python 视觉验证时间戳。`autoSubmit=false` 时是牌已正确顶起的时间；
  `autoSubmit=true` 时是出牌状态已确认变化的时间。

### 不确定结果

仅 `autoSubmit=true` 时可能出现：

```json
{"contractVersion":"execution.v1","messageType":"RESULT","taskType":"CARD_PLAY",
 "requestId":"...","dealId":"...","generation":1,"status":"UNCERTAIN",
 "detail":"button_clicked_but_no_state_change_observed"}
```

`UNCERTAIN` 表示可能已点击但无法确认结果。Java 必须重新走 LOCAL_TURN 识别
当前状态，绝不能假设成功。`detail` 是简短诊断说明，不得包含坐标或截图路径。

### 失败结果

`status` 为 `FAILED`，带 `errorCode` 和 `errorMessage`。常见错误码：

- `HAND_MISMATCH`：当前手牌与 Java 权威手牌不一致。
- `RECOMMENDED_NOT_IN_HAND`：建议牌不在权威手牌内。
- `SLOT_NOT_FOUND`：找不到对应牌槽位。
- `VERIFY_FAILED`：选牌后视觉验证失败（牌未顶起或几何漂移）。
- `EVIDENCE_STALE`：自动点击前视觉证据超过新鲜度阈值。
- `SELECTION_NOT_CONFIRMED`：选牌视觉验证未通过。
- `BUTTON_NOT_FOUND`：找不到出牌或不出按钮。
- `AUTHORITY_EXPIRED`：Java generation 已过期或会话已关闭。
- `TIMEOUT`：超过 `deadlineMs` 未完成。
- `USER_INTERVENTION`：选牌期间检测到用户按下鼠标左键，用户接管，自动化主动放弃；Java 侧不重试，继续等待用户手动操作。
- `INTERNAL_ERROR`：未预期的内部错误。

## PREPLAY_BUTTON

执行一次局前正向按钮点击。Worker 连续读取当前生命周期按钮，只有目标按钮在稳定
证据中出现才点击，并在点击后确认目标消失或阶段离开。

### 请求字段

- `stage`：`call`、`rob` 或 `double`。
- `action`：与阶段匹配的 `call`、`rob`、`double` 或 `super_double`。
  `no_call`、`no_rob`、`no_double` 和跨阶段动作均拒绝。

### 成功结果

```json
{"contractVersion":"execution.v1","messageType":"RESULT","taskType":"PREPLAY_BUTTON",
 "requestId":"...","dealId":"...","generation":1,"status":"OK",
 "verifiedAt":"2026-08-29T10:00:00Z"}
```

点击后无法确认阶段变化返回 `UNCERTAIN`；点击前找不到按钮或超时返回 `FAILED`，
并携带 `BUTTON_NOT_FOUND`、`TIMEOUT` 或 `INTERNAL_ERROR` 等错误码。Java 不会因为
负向建议而发送此任务。

## 牌面编码

牌面使用 `3..10,J,Q,K,A,2,X,D`，座位使用
`landlord|landlord_down|landlord_up`。`verifiedAt` 是带时区的 UTC ISO-8601 时间。

生产入口：

```bash
python -m douzero_advisor.execution_service --calibration-manifest config/live.json
```
