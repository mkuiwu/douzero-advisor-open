# preplay-inference.v1

Java 的局前 DecisionPort 与独立 Python 模型 Worker 使用一行一个扁平 JSON。
CV Worker 不加载该模型，模型 Worker 也不截图或解释按钮。

请求字段：

- `contractVersion`：固定 `preplay-inference.v1`。
- `requestId/dealId/generation`：Java 的一次请求和牌局身份。
- `modelId`：本次指定的局前模型标识，响应原样回显。
- `deadlineMs`：单次推理最长时间，单位毫秒。
- `stage`：`call|rob|double`。
- `hand/bottomCards`：语义牌面数组；农民和叫抢请求的底牌为空数组。
- `availableActions`：当前 UI 确认可选的 lower_snake 动作；返回动作必须在内。
- `callPromptSeen/robPromptSeen`：Java 局前上下文，用于选择原 legacy 阈值。

`double` 阶段的 `availableActions` 可以是 `double/no_double` 两按钮布局，
因为 `super_double` 并非每局都出现。模型若按分数和牌型命中 `super_double`，
但当前动作集合没有该按钮且包含 `double`，必须将建议降级为 `double`；没有可用
的合法正向动作时仍返回失败，不能猜测或点击其他按钮。

成功响应包含 `action/score/threshold/decisionReason/modelVersion/latencyMs`。`score` 是 legacy
模型原始分数，不是概率或胜率。失败响应为 `status=FAILED` 和明确的
`errorCode/errorMessage`；任何失败都只表示暂无建议。

Worker 启动握手：

```json
{"contractVersion":"preplay-inference.v1","messageType":"READY"}
```

生产入口：

```bash
python -m douzero_advisor.preplay.model_worker --legacy-root C:\\work\\FullAuto
```
