# 产物职责、漂移定位与变更回写

这份文档解决两个实际问题：每项内容应该写在哪里，以及 AI 出错后应该改哪份文档。它是当前项目的治理说明，不是运行日志，也不把一次审计结果写成永久事实。

## 1. 一个问题只保留一个权威

| 内容 | 唯一权威 | 下游可做什么 | 下游不能做什么 |
|---|---|---|---|
| 项目目标、范围、非目标 | `GOAL.zh.md` | 引用、提出缺口 | 由代码或测试反推目标 |
| 需求任务、验收问题 | `TASK-TREE.zh.md` | 映射工程任务 | 填入具体运行事实 |
| 环节宇宙 | `AI-WORKFLOW-UNIVERSE.zh.md` | 识别遗漏类型 | 直接充当执行顺序 |
| 16 环节推进顺序 | `PROJECT-DELIVERY-WORKFLOW.zh.md` | 判断下一份产物 | 改写 A/B 机器阶段 |
| 工程 Chain 和文件关系 | `WORKFLOW-MAP.zh.md` | 映射到 Spec/Task/Workflow | 反向修改未确认需求 |
| A/B 阶段与路由 | 对应 `workflows/*/workflow.yaml` | 执行已声明步骤 | 用 README 新增隐藏阶段 |
| 输入输出和失败语义 | `contracts/*.yaml` | 生成、校验和失败关闭 | 由 Prompt 临时放宽 |
| 跨层对象引用和状态晋升 | `contracts/artifact-reference.yaml`、`contracts/promotion-matrix.yaml` | 解析引用、验证迁移和阻断越级 | 用裸 `status` 或 `confidence` 代替晋升依据 |
| 时序和失败回退 | `contracts/temporal-case.yaml`、`failure-report.yaml` | 记录事件、状态、因果证据和最早责任层 | 只凭先后顺序补因果、只重跑末端阶段 |
| P12 实际投影 | `contracts/projection-record.yaml` | 记录选入、排除、授权和版本 | 只写最终选择，不记录排除项 |
| 95+ 验收和声明上限 | `contracts/acceptance-scorecard.yaml` | 计算维度、硬门、独立性和 bounded Claim | 用平均分掩盖硬门失败 |
| 可复用局部能力 | `skills/*/SKILL.md` | 被 Workflow 调用 | 自行决定全局顺序 |
| 确定性执行接口 | `tool-registry.yaml` 与适配器 | 产生运行事实 | 把 `planned` 写成可用 |
| 单次运行 | `run-record`、progress、checkpoint | 供 Evidence 引用 | 覆盖历史 Run |
| 验证材料 | stage evidence、reports、reviews | 支持 Verdict | 只凭文件存在签发通过 |
| 交付结论 | 独立 validator/acceptance | 生成范围受限 Claim | 把局部通过写成全局完成 |
| 变更与失效 | 本文的变更记录及后续审计 | 触发重新生成/验收 | 静默沿用旧下游结果 |

## 2. AI 漂移的八类责任层

先按“错误最早在哪里产生”分类，再决定修改对象：

| 代码 | 漂移类型 | 典型现象 | 首要修改位置 | 不能只修改 |
|---|---|---|---|---|
| D1 | 目标漂移 | 做出了漂亮结果，但不是用户要的东西 | `GOAL.zh.md`、原始意图和需求确认 | 代码提示词 |
| D2 | 事实/来源漂移 | 混入旧文件、相似来源或未授权材料 | Source inventory、Fact/Unknown、来源边界 | 输出 Markdown |
| D3 | 需求解释漂移 | AI 推断被写成明确需求，非目标消失 | 需求候选、业务叙事、确认记录 | Spec |
| D4 | 结构/语义漂移 | 文字存在但层级、关系、顺序或视觉含义改变 | 行为/内容结构、reconciliation、样例反例 | 验收报告措辞 |
| D5 | 方案/契约漂移 | 工程方案改变了未经批准的接口、状态、权限或责任 | ADR、Engineering Chain、I/O 契约 | 临时适配器 |
| D6 | 执行/模型漂移 | 同样输入因模型、Context、预算、重试或 fallback 改变结果 | Workflow、Skill/Tool/Model/Context 投影 | 隐藏重试 |
| D7 | 证据/验收漂移 | Runner 说成功，独立验收或来源覆盖不支持 | stage evidence、acceptance、validator | 总状态字段 |
| D8 | 变更/复用漂移 | 上游已变更，旧 baseline、资产或 Evidence 仍被沿用 | 变更影响、失效传播、版本和回归记录 | 覆盖旧文件 |

## 3. 发现问题后的固定回路

```text
发现问题
→ 锁定当前 source、Run、版本、Projection 和 Evidence
→ 判断最早责任层 D1–D8
→ 修改该层的权威文档/契约，创建新版本
→ 计算受影响的下游对象和声明
→ 从最早受影响的门重新运行
→ 独立验收并记录新 Verdict
→ 旧结果保留为历史，必要时标为 stale/invalidated
```

不得把“再写一段更强的 Prompt”当作通用修复。只有当问题确实属于模型输入装配或局部生成规则时，Prompt 才是 D6 的一个修改对象；即使如此，也必须更新其版本、Context/Schema 指纹和回归样例。

## 4. 变更的最小记录

每次修改至少记录（机器字段见 [`contracts/change-record.yaml`](contracts/change-record.yaml)）：

- `change_id`、触发时间、发起者和变更理由；
- 受影响的上游对象版本/hash，以及变更前后差异；
- 责任层（D1–D8）和影响分类：`unaffected`、`review_required` 或 `invalidated`；
- 受影响的 Requirement、Spec、Task、Workflow、Skill/Tool、Run、Evidence、Verdict 和 Claim；
- 是否需要人工确认、重新生成、重新测试、重新验收或撤回声明；
- 新版本、新 Run、新 Evidence 和新 Verdict 的路径；
- 未覆盖范围、Unknown、剩余风险和下一步 owner。

若要进入正式 Claim，还必须保留 `acceptance-scorecard` 的当前输入摘要、独立 validator 身份和 Verdict；95–96 只能产生范围受限的 `bounded` 声明，97+ 才达到当前质量目标带。上游 digest 变化后，旧 Claim 必须进入 `invalidated` 或 `review_required`，不能只在文字中提醒。

批准对象不能原地改写。需求、基线、契约、Prompt、模型配置或规则集变化，只能创建新版本并保留 `supersedes`；历史 Run 和 Evidence 不被重写。

## 5. 对当前文档转换项目的回写示例

| 发现的问题 | 应回写的文档/契约 | 重新开始位置 |
|---|---|---|
| 新来源没有被正确识别，或 PPTX/PDF 被错误配对 | `GOAL` 的来源边界、A1/source inventory、候选比较契约 | P01 或 A1 |
| OCR 文字缺失、数字不确定 | A2/A4 的事实分层、OCR/视觉 Evidence、Unknown 规则 | A2 或 A4 |
| 页面结构被压平，图示关系被臆造 | P07 的行为/结构样例、A4 reconciliation、Markdown 组装规则 | P07 或 A4 |
| 图片删错、区域裁剪漏掉箭头或图例 | A3/A5/A6 资产策略、crop decision 和验收契约 | A3 或对应图级 F0 |
| 结果文件存在但页面缺失、断链或状态虚假 | A7、`stage-evidence.yaml`、output validator | P14 或 A7 |
| 模型或 fallback 改变了结果 | P12 的 Context/Model/Tool 记录、路由契约和回归样例 | P12 或 P13 |
| 需求范围改变，旧 Markdown 仍被交付 | 新 Requirement/基线、影响记录和失效标记 | P06，并沿影响闭包重开 |

## 6. 声明纪律

- 文档存在不等于需求已批准。
- Workflow 阶段完成不等于业务结果正确。
- Runner 成功不等于独立验收通过。
- Fixture 通过不等于当前真实来源通过。
- `passed` 只在明确的 source、范围、版本、证据等级和验收 Verdict 内有效。
- 任一关键门未通过，结论必须保持 `needs-review`、`failed`、`blocked` 或 `Unknown`，不能用平均分掩盖硬失败。
