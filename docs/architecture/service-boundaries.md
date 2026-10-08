# DouZero Advisor 服务边界

## 目标拓扑

Java Core 编排牌局和业务任务，不编排 Python 内部步骤。Python CV 可以是一个服务，但
Java 按能力依赖它，不使用带 `intent` 的万能接口：

```text
                         Java Core
               GameSession / StateMachine / Orchestrator
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
     Recognition capability ports        Semantic decision ports
     ────────────────────────────              ────────────
     NewGameRecognitionPort                    正式出牌建议
     PreplayRecognitionPort                    局前只读建议
     DealRecognitionPort                             │
     LocalTurnRecognitionPort                        ▼
     SettlementWatchPort                       模型适配/tensor/推理
                 │
                 ▼
              Python CV
     截图/Reader/重试/稳定/捕获恢复
```

五个识别端口可以由同一 Python 进程和同一套内部组件实现。端口拆分只表达 Java 的业务
依赖，不要求重复截图或复制 Python 代码。

## Java Core 拥有

- `GameSession`、`GameContext`、状态机和生命周期；
- `requestId`、`dealId`、`generation`、deadline、状态 timeout 和迟到结果拒绝；
- 新局后进入 `PREPLAY`，并行局前提示、正式 Deal、结算旁路，再推进正式出牌和统一销毁；
- 从两次建议手牌的多重集差推导本方动作，按期望座位原子归并完整历史，以及只读建议展示；
- 运行时诊断、证据关联和操作员可见状态。

Java 不拥有截图、窗口捕获、ROI、Reader、OCR、多帧稳定、识别重试、捕获恢复、模型
整数编码、tensor、合法动作生成或推理，也不拥有任何自动点击。

## Python CV 拥有

每个端口调用都是一个完整业务任务。Python CV 自己取得截图、选择内部 Reader、处理
遮挡和动画、做稳定确认、重试并恢复捕获，最终只返回该端口定义的成功结果或
`RecognitionFailure`。

| 端口 | 成功语义 | 生命周期 |
| --- | --- | --- |
| `NewGameRecognitionPort` | 新局边界 | 仅 `WAIT_NEW_GAME` |
| `PreplayRecognitionPort` | 稳定的本方叫、抢或加倍提示 | `PREPLAY` 当前状态支路 |
| `DealRecognitionPort` | 正式出牌入口已成立且初始事实闭合 | `PREPLAY` 并行支路 |
| `LocalTurnRecognitionPort` | 当前手牌和两侧语义动作形成稳定快照 | 每个本方回合 |
| `TurnEndRecognitionPort` | 建议所在的当前本方回合已经离开 | 每次建议之后 |
| `SettlementWatchPort` | 结算页 | 整个 `GameSession` |

局前首次提示可接受当前稳定态，后续必须先观察旧提示离开再重新进入。Java 只记录真实
观察到的 CALL/ROB 提示；模型建议不是用户已执行事实。局前失败不停止并行 Deal。

牌局初始化成功本身就是正式 play-stage ready 门禁，并有一项不能省略的业务字段：本方为农民时，Python 通过地主首手牌确定相对
座位，该首手牌必须随结果交给 Java 并成为动作历史第一条。本方为地主时该字段为空。

本方回合请求中的 `requiredActors` 只限定必须读取、且尚未进入权威历史的对手区域：Deal
已把农民视角下的地主首手写入初始历史，首次只读取该历史之后轮到本方前的座位，之后每次
读取本方动作后的两个对手。响应以 `Map<Seat, PlayAction>` 返回这些座位的结果。Python 不维护
`actionHistory/currentSeat`，也不能自己决定认左还是认右。Java 不能按物理左右读取顺序
写历史，只能消费自身状态当前期望的座位。
`LocalTurnRecognitionPort` 只接受当前稳定态并立即返回。Java 保存该语义快照后，单独提交
`TurnEndRecognitionPort`，由 Python 以按钮离开为主、手牌或已知侧区相对基线变化为辅助确认
旧回合结束；Java 收到结束结果后才提交下一次 `LocalTurnRecognitionPort`。语义快照相同不代表
重复回合，Python 也不维护局内历史。

## Python 模型服务拥有

`PreplayDecisionPort` 接收 Java 已确认的 `PreplaySnapshot`，根据当前阶段、手牌、底牌、
可用动作以及 `callPromptSeen/robPromptSeen` 返回叫、抢、加倍只读建议。CALL/ROB 手里
必须为 17 张；DOUBLE 只能为 17 或 20 张，地主 20 张时底牌必须是三张。

`DecisionPort` 接收 Java 已确认的正式出牌状态。Python DouZero 负责模型选择、牌面编码、
tensor、固定规则下的合法动作生成和推理，返回推荐动作、原始价值、领先值、候选评分或
明确失败。模型原始价值不是概率或胜率。

线协议按模型明确分为 `DouZeroRequest/DouZeroResponse` 和
`ResNet2Request/ResNet2Response`，不使用通用 `payload/result`。Java 领域层只接收
`CardSet`、`PlayAction.Play` 和 `PlayAction.Pass`。

## 任务身份与取消

- 无局态任务只有 `requestId`；新局确认后由 Java 创建 `dealId` 和正数 `generation`。
- 所有局内任务使用 `GameTaskIdentity(requestId, dealId, generation)`。
- `RecognitionJob<R>` 只提供最终完成和幂等取消，不暴露帧、进度或内部重试。
- `GameSession` 明确持有 `currentStateJob/dealJob/settlementJob/decision Future` 及各超时，
  `close()` 在同一收口幂等取消全部资源和两个上下文。
- 回调生效前必须核对当前 `dealId/generation/requestId`；关闭后因
  `CONTEXT_CLOSED` 拒绝，旧代结果因 `generation` 拒绝，两者都只进入诊断。

## 明确禁止

- 禁止恢复 `RecognitionPort.submit(RecognitionTask)`、`RecognitionIntent`、
  `CURRENT_STATE`、通用 `RecognitionFact` 或可选字段拼装的结果容器。
- 禁止 `GameSession.dispatch(GameAction)` 这类把全部业务动作重新装进一个入口的设计。
- 禁止 Java 传 Reader、ROI、帧号、模板、稳定帧数或重试策略。
- 禁止把失败、空结果或旧局结果修补成成功观测。
- 禁止把建议解释为已执行动作；自动“不出”和其他显式开启的执行能力必须经独立门禁与结果确认。

## 迁移与验收

现有 `src/douzero_advisor/` 仍是行为基线。Java 编排与局前/正式语义端口已经拆开，下一步
由 Python 现有新局、preplay、bootstrap、本方回合和结算能力实现各端口，再用 Windows
三角色回放核对最终事件和建议。局前可选输入不会迁移；任何自动输入仍需另行授权。
