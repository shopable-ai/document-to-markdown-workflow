# 产物权威、生命周期、迁移与变更治理

> **方法参考：** 下表保留旧英文目录映射，不再定义当前项目权威。当前映射见[文件与对象矩阵](../项目治理/文件与对象矩阵.md)，当前变更规则见[权威与变更治理](../项目治理/权威与变更治理.md)。

## 唯一字段级权威

| 逻辑对象 | 唯一权威 | 派生或消费方 | 禁止反向行为 |
|---|---|---|---|
| 原始请求与来源登记 | requirements/discovery/RAW-REQUESTS.md | discovery、requirements | 用摘要覆盖原始对话 |
| 项目目标、范围、非目标 | requirements/PROJECT-GOAL-AND-SCOPE.md | REQUIREMENTS、design | 从代码或 Workflow 反推 |
| 稳定需求字段与状态 | requirements/REQUIREMENTS.md | requirement tree、index、traceability | 在树或 YAML 新增需求 |
| 需求树 | requirements/REQUIREMENT-TREE.md | 人工导航 | 产生列表外 ID |
| 三次基线 | requirements/BASELINES.md | design、execution | 用测试通过代替人工确认 |
| 场景与业务链路 | requirements/BUSINESS-SCENARIOS-AND-CHAINS.md | functions、behavior | 从 Workflow 阶段反推场景 |
| 能力、功能列表和功能卡 | requirements/FUNCTIONS.md | function tree、index、design | 在功能树产生新功能 |
| 行为、状态和时序 | requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md | acceptance、Spec | 把 run order 写成业务因果 |
| 项目验收和测试空间 | requirements/ACCEPTANCE-AND-TEST-SPACE.md | verification design、Workflow acceptance | 用 Workflow acceptance 替代 |
| 类型化追溯边 | requirements/TRACEABILITY.md | tasks、tests、change closure | 缺边时默认 unaffected |
| C01–C40 主链 | PROJECT-DELIVERY-WORKFLOW.md | P 导航、validator | 让 P/Workflow 变第二主链 |
| U01–U24 覆盖域 | governance/AI-WORKFLOW-UNIVERSE.md | review、validator | 当成线性 Workflow |
| 方案与架构 | design/SOLUTION-AND-ARCHITECTURE.md | engineering design、Spec | V2 前声明批准 |
| 工程精确字段与枚举 | contracts/*.yaml | code、tests、Workflow | 反向定义业务语义 |
| 人类契约说明 | contracts/CONTRACTS.md | reviewer、validator | 与 YAML 维护两套独立字段规则 |
| 工程任务列表 | execution/ENGINEERING-TASKS.md | task tree、Workflow | 无 Spec/需求来源的任务晋升 |
| Workflow 阶段 | workflows/*/workflow.yaml | run projection | 改写需求和功能 |
| Run/Evidence/Verdict/Claim | 当前运行记录和独立验收记录 | delivery、retrospective | 覆盖历史或扩大范围 |

## 产物状态

| 状态 | 语义 |
|---|---|
| authoritative | 当前层字段级权威，可由有权角色修改 |
| authoritative-draft | 字段位置权威，但内容尚未基线确认 |
| derived | 从权威生成或重排，禁止独立修改语义 |
| reference | 提供来源、背景或历史，不进入默认执行上下文 |
| runtime | 一次运行事实，只读追加 |
| archived | 不可变历史快照，不参与当前路由 |
| stale | 可能仍相关，但不能支持当前结论 |
| invalidated | 已知与当前上游不一致 |

## 旧文件迁移表

| 旧活动文件 | 处置 | 新职责位置 | 历史保存 |
|---|---|---|---|
| GOAL.zh.md | 拆分并退出根目录 | PROJECT-GOAL-AND-SCOPE、REQUIREMENTS、BASELINES、discovery | archive/governance-migration-20260828/GOAL.zh.md |
| TASK-TREE.zh.md | 拆分；需求树降为 derived；移除当前状态 | REQUIREMENT-TREE、SCENARIOS、FUNCTIONS、BEHAVIOR、ACCEPTANCE | archive/.../TASK-TREE.zh.md |
| WORKFLOW-MAP.zh.md | 拆分工程映射和追溯 | design/WORKFLOW-MAP.md、requirements/TRACEABILITY.md | archive/.../WORKFLOW-MAP.zh.md |
| EXECUTION-TASK-TREE.zh.md | 拆工程任务列表和树；移除单次状态 | execution/ENGINEERING-TASKS.md、ENGINEERING-TASK-TREE.md | archive/.../EXECUTION-TASK-TREE.zh.md |
| ARCHITECTURE.zh.md | 纠正名称，不再冒充 Solution Architecture | design/WORKFLOW-ARCHITECTURE.md | archive/.../ARCHITECTURE.zh.md |
| WORKFLOWS.zh.md | 总览并入 Workflow Architecture 和各 Workflow README | design/WORKFLOW-ARCHITECTURE.md | archive/.../WORKFLOWS.zh.md |
| WORKFLOW-A-EXECUTION.zh.md | 移到执行层 | execution/WORKFLOW-A-EXECUTION.md | archive/.../WORKFLOW-A-EXECUTION.zh.md |
| AI-WORKFLOW-UNIVERSE.zh.md | 移到治理层并补 U 映射 | governance/AI-WORKFLOW-UNIVERSE.md | archive/.../AI-WORKFLOW-UNIVERSE.zh.md |
| COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.zh.md | 移到治理层并收敛为叠加层 | governance/COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md | archive/.../COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.zh.md |
| ARTIFACT-AND-CHANGE-GOVERNANCE.zh.md | 移到治理层并补权威/生命周期/迁移 | 本文件 | archive/.../ARTIFACT-AND-CHANGE-GOVERNANCE.zh.md |
| PROJECT-DELIVERY-WORKFLOW.zh.md | 由 P16 导航升级为根 C40 主链 | PROJECT-DELIVERY-WORKFLOW.md | archive/.../PROJECT-DELIVERY-WORKFLOW.zh.md |
| EXPERT-CONSENSUS-REVIEW.zh.md | 降为历史审查快照 | archive；新复审写入 .audit 日期快照 | archive/.../EXPERT-CONSENSUS-REVIEW.zh.md |

旧根名称不保留活动跳转 stub，避免形成第二权威。活动引用必须同一次变更切换；archive 内部相对链接是不可变历史文本，不参与当前链接和路由校验。

## YAML/Markdown 配对

- 每个活跃工程 YAML 必须有 id、version、human_reference。
- human_reference 可以指向 contracts/CONTRACTS.md 的逐契约锚点；该卡必须反链 YAML、同 ID/版本并解释目的、来源、I/O、状态/失败、正常/异常/恢复、不变量、关系、失效和最小示例。
- Markdown 解释为什么和适用范围；YAML 对精确字段、枚举和 schema 权威。
- 冲突时 validator 输出 contract-doc-drift 并停止，不静默选择其中一份。
- requirements.index.yaml 和 functions.index.yaml 只能由 Markdown 生成；source_hash 不一致时为 stale。

## 变更影响与失效传播

1. 对 changed_ref 产生新版本，保留旧版本。
2. 沿 Requirement → Scenario → Function → Behavior → Acceptance → Design → Spec → Task → Workflow → Code → Test → Run → Evidence → Verdict → Delivery → Claim 计算闭包。
3. 对每个下游标记 unaffected、review_required、stale 或 invalidated；缺边按 review_required。
4. 从最早受影响的基线或门重建，产生新 Spec、Task、Run 和 Evidence。
5. 只有新的独立 Verdict 可以恢复 Claim；旧 Evidence 和 Claim 保持历史状态。

## 漂移定位

| 漂移 | 最早责任层 | 不应只修哪里 |
|---|---|---|
| 做错了目标 | PROJECT-GOAL-AND-SCOPE / REQUIREMENTS | prompt、code |
| 漏场景或功能 | SCENARIOS / FUNCTIONS | Workflow stage |
| 行为和时序错误 | BEHAVIOR specs | Run 日志 |
| 验收规则错误 | ACCEPTANCE / verification design | validator 输出 |
| schema 或枚举错误 | contracts YAML + CONTRACTS doc | README 摘要 |
| 执行或恢复错误 | Spec、Task、Workflow、Code | 需求文本 |
| 证据陈旧 | Run/Evidence/change record | 新建“通过”说明 |

任何一次修正都必须重新读取原需求、目录职责和验收判据；目录更整齐不是完成证据。
