# recognition.v1

Java Core 与 Python CV Worker 使用 UTF-8 JSONL 长连接。每行都是扁平对象，
禁止 `payload`、`result`、截图、ROI、frame、capture generation、
`evidenceHash` 或 `actionHistory`。

Worker 启动后先输出：

```json
{"contractVersion":"recognition.v1","messageType":"READY","taskTypes":["NEW_GAME","PREPLAY_PROMPT","DEAL","LOCAL_TURN","TURN_END","SETTLEMENT"]}
```

## 公共字段

- `contractVersion`：固定 `recognition.v1`。
- `messageType`：Java 命令为 `SUBMIT`/`CANCEL`；Worker 输出为 `READY`/`RESULT`。
- `taskType`：`NEW_GAME`、`PREPLAY_PROMPT`、`DEAL`、`LOCAL_TURN`、`TURN_END`、`SETTLEMENT`。
- `requestId`：Java 生成的全局唯一任务 ID；重复提交明确失败。
- `deadlineMs`：Worker 收到 SUBMIT 后的最长任务时间，单位毫秒。
- `dealId`/`generation`：Java 的本局身份；除 `NEW_GAME` 外必填。
- `status`：RESULT 固定为 `OK` 或 `FAILED`。失败还带
  `errorCode`/`errorMessage`。

`NEW_GAME` 的 `CANCEL` 只带
`contractVersion/messageType/taskType/requestId`；局内任务的 `CANCEL` 还必须
携带原任务的 `dealId/generation`。取消操作幂等。

## 各任务

- `NEW_GAME`：局前生命周期按钮连续两帧稳定后返回 `observedAt`。
- `PREPLAY_PROMPT`：请求 `entryMode` 为
  `accept_current_stable_prompt` 或 `require_change_then_new`。返回
  `promptType` (`call|rob|double`)、`hand`、始终存在的 `bottomCards`、
  `availableActions` 和 `observedAt`；按钮、手牌和必要底牌连续三帧稳定。
- `DEAL`：返回 `localSeat`、`hand`、`bottomCards`、
  `landlordOpeningPlay` 和 `observedAt`。地主必须在正式出牌入口闭合
  20+3，不能在加倍页提前完成；农民必须闭合 17+3 和唯一地主首手。
- `LOCAL_TURN`：请求 `localSeat`、`requiredActors` 和 `turnEntryMode`。正式编排只使用
  `accept_current_stable_turn`；`require_exit_then_reenter` 仅保留给旧调用方兼容。`requiredActors` 由 Java 权威历史
  推导：Deal 已确认的地主首手已进入农民初始历史，首次只含该历史之后、轮到本方前尚未确认
  的对手，之后含本方动作后的两个对手；空集合表示已直接轮到本方，不得因左右结果区失败。
  从 Python 收到 SUBMIT 起使用 `deadlineMs`，并在本方操作按钮、手牌及 Java 指定的对手动作
  连续两帧稳定后返回 `currentHand`、`actionsBySeat` 和 `observedAt`。动作数组为空
  明确表示 Pass；非空数组表示 Play。只要求 Java 指定的座位连续两帧稳定。
- `TURN_END`：Java 在建议返回后提交 `localSeat`、`baselineHand` 和
  `baselineActionsBySeat`。Worker 只确认建议所在的本方回合已经离开：本方操作按钮连续两帧消失为
  主证据；手牌或已知两侧动作相对基线连续两帧变化为辅助证据。它只返回 `observedAt`，不推导动作、
  不等待下一次本方回合，也不维护局内历史。Java 收到成功后才提交新的 `LOCAL_TURN`。
- `SETTLEMENT`：两个完整结算按钮同帧出现并连续两帧稳定后返回
  `observedAt`；该帧优先于同 deal 普通任务结果。

牌面使用 `3..10,J,Q,K,A,2,X,D`，座位使用
`landlord|landlord_down|landlord_up`。`observedAt` 是 UTC ISO-8601 时间。

生产入口：

```bash
python -m douzero_advisor.recognition_service --calibration-manifest config/live.json
```
