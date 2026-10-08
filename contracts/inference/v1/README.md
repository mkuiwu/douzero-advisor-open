# inference.v1

这是 Java 控制层与 Python 模型服务之间的第一版通用推理协议。Java 发送已经
确认的牌局状态，Python 只返回模型结果，不负责推进权威牌局状态。

`contractVersion` 表示线协议兼容版本。每个模型只有一个明确 Request 和一个明确
Response，不再使用通用 Envelope、任意 `payload` 或任意 `result`：

| 模型 | Request | Response | modelId |
| --- | --- | --- | --- |
| 默认 DouZero | `DouZeroRequest` | `DouZeroResponse` | `original` |
| ResNet2 | `ResNet2Request` | `ResNet2Response` | `resnet2` |

对应 JSON 示例为 `douzero.request.example.json`、`douzero.response.example.json`、
`resnet2.request.example.json` 和 `resnet2.response.example.json`。

ResNet2 的生产端是持久 JSONL worker。在仓库根目录、已经把 `src` 和固定
`vendor/DouZero` 加入 `PYTHONPATH` 的环境中，Java 的 `douzero.model.command`
应等价于：

```text
python -m douzero_advisor.model_service.resnet2_worker --manifest models/resnet2/manifest.json --device cpu
```

worker 在输出 READY 前一次加载并校验三个座位的 checkpoint；manifest、权重、
哈希或运行依赖缺失时直接非零退出。stdout 只承载 READY 和逐行 Response，模型
诊断写入 stderr。同一进程串行处理后续所有 Request，不会按回合重复加载模型。

两个模型当前需要相同的斗地主公开状态，但仍各自拥有明确 DTO，后续某个模型字段
变化时不会修改另一个模型的契约。Request 直接包含 `contractVersion`、
`requestId`、`dealId`、`modelId`、`deadlineMs`、`position`、`hand`、
`bottomCards` 和 `actionHistory`。合法动作不由 Java 传入，Python 使用仓库固定的
DouZero 规则实现根据手牌和完整动作历史统一生成。
`actionHistory` 只包含 Java Core 已按座位、手牌差和幂等门禁确认的完整历史。
Python 模型适配器可以拒绝结构不合法的历史，但不得补齐、去重、改写或推测漏记轮次。

Response 直接包含 `status`、`action`、`actionValue`、`actionMargin` 和
`actionScores`，不再把模型细节藏在任意 `result` 中。`actionScores` 按模型评估
顺序返回 Python 生成的全部候选动作及其原始价值；`actionValue` 是推荐动作价值，
`actionMargin` 是推荐动作价值减去第二名价值，只有一个候选时为 0。

这些 value 只适合在同一模型、同一次请求的候选动作之间比较，不是概率，也不能
直接解释为胜率。`WAIT` 和 `ERROR` 的动作及评分字段必须全部为 `null`，并携带非
`NONE` 错误码和非空诊断说明；`OK` 的 `action` 必须非 `null`，其中空集合明确
表示“不出”。

`frameId`、ROI 状态和捕获代属于 Python CV 的 RecognitionPort 实现，仅用于识别
任务内部判断画面新旧与稳定性，不进入 Java 任务或模型请求。模型只消费 Java Core
已经确认的牌局状态。

## 模型请求字段

模型只接收推理真正需要的四类牌局数据：本方座位、当前手牌、三张底牌和完整动作
序列。牌局阶段、当前行动人、上一手、动作座位和动作序号都由 Java Core
在请求前确认，或由完整动作序列确定，不重复塞进模型 DTO。Java 只完成项目语义与
线协议字段之间的无损转换；Python DouZero 的 DecisionPort 实现负责模型专用牌面
编码、合法动作生成、tensor 构造和响应适配。Java 收到响应后只还原项目语义动作。

inference.v1 当前保留以下线协议牌面取值；它是版本化传输约定，不授权 Java 构造
DouZero/ResNet tensor 或实现模型适配：

| 牌面 | 3-10 | J | Q | K | A | 2 | 小王 | 大王 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 编码 | 3-10 | 11 | 12 | 13 | 14 | 17 | 20 | 30 |

线协议中的空动作 `[]` 表示“不出”。该含义只能存在于模型适配器和 JSON 中；
Java 内部必须转换为显式 `PlayAction.Pass`。Python 适配器会用完整历史推导各家
剩余牌、已出牌和未知牌集合，并计算合法动作后交给模型评分。

现有 ResNet2 编码器最终需要 `z_batch[N,40,54]` 和 `x_batch[N,15]`，其中 `N`
是合法动作数量。模型对每个合法动作评分，返回被选动作的同一套整数编码，而不是
返回另一套牌面定义。

## 失败判定

| 条件 | 对外错误码或内部结果 |
| --- | --- |
| 协议版本不支持 | `UNSUPPORTED_CONTRACT` |
| 模型标识不支持 | `UNSUPPORTED_MODEL` |
| 请求字段缺失、牌数冲突或历史顺序不一致 | `INVALID_SNAPSHOT` |
| 模型返回动作不在 Python 生成的合法集合 | `ILLEGAL_ACTION` |
| 调用超过 `deadlineMs` | `MODEL_TIMEOUT` |
| 模型进程或传输不可用 | `MODEL_UNAVAILABLE` |
| 响应的 `requestId`、`dealId` 或 `modelId` 不匹配 | Java 丢弃并返回内部 `INVALID_RESPONSE` |

`[]` 是合法的“不出”，不是无结果。真正没有建议必须返回非 `OK` 状态和
`NO_RECOMMENDATION`，不能用空动作混淆两种语义。
