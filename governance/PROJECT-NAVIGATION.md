# 文档驱动项目交付工作流

这份文档是当前项目的 16 环节人类推进导航。它把“需求逐步澄清、工程设计、执行和证明”分开；它不把 16 个环节强制实现成 16 个 Workflow，也不替代 `workflow.yaml` 的 A1–A7/B1–B7 机器阶段。

## 1. 一句话主链

```text
项目与来源
→ 原始需求与事实
→ 样例与反例
→ AI 需求发现
→ 人工可读需求
→ 需求确认与基线
→ 行为与追溯
→ 方案与架构决策
→ 工程设计
→ Spec、Task 与验证设计
→ Workflow 编排
→ Skill/Tool/Model/Context 投影
→ 代表样例试做
→ 受控运行与证据
→ 独立验收与声明
→ 反馈、变更与重开
```

## 2. 16 个环节

| 编号 | 环节 | 主要回答 | 主要产物 | 退出门 |
|---|---|---|---|---|
| P01 | 项目与来源建立 | 当前项目、来源、范围、权限和版本是什么？ | 项目入口、来源清单、范围快照 | 来源可定位，权限和处理范围明确 |
| P02 | 原始需求与背景事实 | 用户原话、目标背景、事实、假设和 Unknown 是什么？ | 原始需求/事实包 | 原文保留，Fact/Assumption/Unknown 分层 |
| P03 | 参考样例与期望结果 | 哪些结果是期望、允许变化或不可接受？ | 样例、期望输出、反例包 | 每个样例用途、来源和边界明确；无样例要记录缺失影响 |
| P04 | AI 需求发现与推理 | AI 从明确内容和来源中发现了什么候选、冲突和缺口？ | 需求候选稿、需求推理记录、待确认问题、AI 自检 | 每项候选回指依据，保留替代解释和 Unknown，推断不冒充事实 |
| P05 | 人工可读的需求梳理 | 人怎样理解目标、范围、角色、场景、质量和非目标？ | 需求叙事、范围稿 | 读者能复述目标、非目标、边界和成功标准 |
| P06 | 需求确认与需求基线 | 哪些最小范围已被合法接受？ | Business Requirement、Decision Gate、Requirement Baseline、Confirmation Record | 决策权威、确认角色、版本/hash、接受范围和未接受范围可重算 |
| P07 | 业务链路、能力、功能与行为规格 | 用户/调用方应看到什么行为，如何覆盖到下游？ | Scenario、Trigger、Business Chain、Capability、Function、Functional Requirement、Behavior Specification、Behavior Case、Temporal Case、Traceability | 主路径、边界、反例、失败/恢复、顺序/状态和需求映射完整 |
| P08 | 候选方案与架构决策 | 哪些方案可行，关键取舍是什么？ | 研究结论、方案比较、ADR 或 `not_applicable` | 选择有依据、后果、退出条件和重新开启条件 |
| P09 | 工程解决方案与契约设计 | 节点、I/O、状态、错误、权限和外部边界怎样连接？ | Engineering Chain、I/O 契约、技术能力图 | 责任交接、失败路由、权限和兼容边界可执行 |
| P10 | Spec、Task 与验证设计 | 怎样从已批准范围派生可实现、可验证工作包？ | Spec、Acceptance、Test Space、Temporal/Decision Coverage、Tasks、Traceability | 每个任务回指需求/案例/判据，并有顺序、边界和 Evidence 出口 |
| P11 | Workflow 与运行编排 | 如何组织步骤、分支、并行、队列、恢复和取消？ | Workflow、阶段门、运行状态图 | 前置、后置、重试、超时、checkpoint、终态和失败隔离明确 |
| P12 | Skill/Tool/Model/Context 投影 | 本次运行实际加载哪些能力、模型、规则和上下文？ | 注册绑定、Context Snapshot、Projection Record、运行清单 | 记录实际选择与排除；只投影当前范围；planned、candidate、available 不混淆 |
| P13 | 代表样例试做与校准 | 端到端最小样例是否暴露需求、方案或工具问题？ | Smoke Run、校准报告、差异清单 | 代表风险覆盖；差异回到最早责任层并用新 Run 回归 |
| P14 | 受控运行与分层证明 | 本次实际运行发生了什么？ | Run、checkpoint、逐阶段 Evidence、失败报告 | 输入、版本、环境、授权、范围、进度和失败均可重读 |
| P15 | 交付、独立验收与声明 | 结果是否符合事前判据，可以声称到什么边界？ | Acceptance Verdict、交付包、受限 Claim | 独立验收通过；Claim 不超过范围、证据等级和新鲜度 |
| P16 | 反馈、变更与持续演进 | 错误应修改哪份文档，哪些结果需要重开？ | 变更记录、影响预览、失效/回归/复盘记录 | 上游新版本、下游影响闭包和重新验收完成 |

## 2.1 需求推理与时序推理的使用边界

这两类内容不是额外的线性阶段，而是对部分环节增加“可审阅推理记录”：

- P04 先区分用户目标、需求、问题、困惑、失败现象和期望结果，再记录事实、推理、替代解释、Unknown、确认人和晋升状态。
- P07/P09/P11 对顺序敏感的行为必须区分时间先后、因果、依赖、优先、阅读和执行顺序；“A 先发生”不能单独推出“A 导致 B”。
- P10/P13 把边界、反例、状态转移、恢复路径和重新进入作为验证空间；不能只测主路径。
- P14/P15 只把本次实际观察到的顺序和证据写入 Run/Evidence；推断仍须标注 `inferred`，未知仍保留 `Unknown`。
- P16 发生失败时，先判断是意图、需求、边界、方案、执行、输入还是验收规则的问题，再回到最早责任环节。

六种对象、J01–J18 候选认知生命周期和机器字段见 [认知、需求推理与时序参考叠加层](COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md)。该层默认为候选参考，不自动生成需求，也不改变本导航的 16 环节。

## 3. 当前项目的投影

当前项目不是一个完整的 ALM 平台，而是文档资产化工作流规格包。因此 16 环节的投影如下：

| 16 环节 | 当前已有承载 | 仍需补强的方向 |
|---|---|---|
| P01–P02 | `GOAL.zh.md`、`TASK-TREE.zh.md`、A1 来源预检、source inventory | 将项目范围、原始意图和运行范围分开登记 |
| P03 | `tests/fixtures/`、expected 文件、各 Workflow 的 `ACCEPTANCE.zh.md` | 形成可复用的正例/反例/边界样例说明 |
| P04–P06 | `contracts/requirement-chain.yaml` 的前置链和人工确认状态 | 需求候选、业务确认、版本/hash 基线需要独立受控产物 |
| P07–P10 | `WORKFLOW-MAP.zh.md`、`EXECUTION-TASK-TREE.zh.md`、contracts | 当前内容转换任务已有映射；通用项目需求对象仍不应伪装成 A1–A7 |
| P11–P12 | `workflow.yaml`、`workflow-registry.yaml`、Skill/Tool registry、A1–A7/B1–B7 | 增加 Context Snapshot、模型/规则指纹和统一 projection 记录 |
| P13 | smoke、fixture contract、页面结构守卫和 route benchmark | 让代表样例与目标风险一一对应，不把 fixture 结果外推到所有来源 |
| P14–P15 | `workflow-run-record.yaml`、`stage-evidence.yaml`、A7 validator、`ACCEPTANCE`、`acceptance-scorecard.yaml` | 继续区分 Run、Evidence、Verdict 和 Claim；补齐当前输入摘要、证据权威和独立验收边界 |
| P16 | `change-record.yaml`、known failures、`.audit/`、`.prompt/`、恢复和批次 ledger | 建立正式变更影响、最早责任层和失效传播记录；旧 Claim 不得静默复用 |

## 4. 与 A1–A7/B1–B7 的关系

```text
P01–P06 认知和需求准备
P07–P10 业务/工程设计与验证设计
P11–P12 执行投影
P13     校准门
P14–P15 运行、证据、验收与声明
P16     演进回路

P11–P15 的当前文档转换投影：
  A1 来源预检 → A2 原生提取 → A3 资产保留 → A4 视觉理解
  → A5 Markdown 组装 → A6 来源登记 → A7 资产包验收

  B1 内容选择 → B2 用途判断 → B3 结构规划 → B4 参考选择
  → B5 请求组装 → B6 单图调用 → B7 单图验收
```

A/B 的七阶段是单次 Workflow 的执行阶段；P01–P16 是项目级推进导航。A7 或 B7 通过只能证明对应运行范围，不自动证明 P01–P16 全部完成，也不能把局部结果升级为通用生产能力。

P06–P07 内部的业务需求推导链固定为：

~~~text
业务需求（Business Requirement）
→ 使用场景 / 触发条件（Scenario / Trigger）
→ 业务链路（Business Chain）
→ 业务能力（Capability）
→ 业务功能（Function）
→ 功能需求（Functional Requirement）
→ 决策门 / 需求基线（Decision Gate / Requirement Baseline）
→ 行为规范 / 行为案例（Behavior Specification / Behavior Case）
~~~

当前项目的文档转换需求可以使用这条链描述“来源保真、页面结构、视觉资产、验收和交付”等业务结果；没有实际业务对象时，不创建名称相似的空卡片，只在对应环节记录 `not_applicable` 和原因。

这条业务需求推导链不是新的阶段编号：项目主链 P01–P16 决定“什么时候做哪类工作”，它决定“工作中的对象如何逐步变得清晰、可实现、可验收”。例如，P04 产生需求推理候选，P06 确认业务需求和基线，P07 展开场景到行为，P08–P10 继续形成方案、工程设计和验收规格。

## 5. 环节推进规则

1. 先完成当前环节的输入、输出、门禁和失败出口，再开始下游工作。
2. 允许并行的工作必须声明共同输入和汇合条件；并行不等于可以绕过前置批准。
3. 条件环节（如样例、研究、ADR、视觉资产、复杂图重建）没有触发时记录 `not_applicable` 的理由，不创建空占位物。
4. `needs-review` 是明确存在未决问题的状态，不是任何失败后的默认下一步。
5. 出错时回到最早受影响的责任环节；下游修补必须回写上游文档或新版本，不能只改临时 Prompt。
6. 文档、契约和检查器决定“允许做什么”；Run/Evidence/Verdict 决定“这一次实际证明了什么”。
