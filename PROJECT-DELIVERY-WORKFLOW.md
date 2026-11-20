# 人工治理、AI 推导、Markdown 权威、工程投影、证据闭环

| 字段 | 值 |
|---|---|
| document_id | PROJECT-DELIVERY-WORKFLOW |
| version | 0.3.0 |
| authority | method-reference |
| status | brownfield-reference |

本文件保留既有 C01—C40 候选方法和旧样板投影，供当前真实项目逐节点核对。当前项目的阶段入口由[项目导航与阶段门](项目治理/项目导航与阶段门.md)维护，当前业务语义只在 `项目需求/` 中文 Markdown 中维护。

本文件不能代替当前项目实际经过 C01—C40，也不能凭旧样板状态、既有代码、测试或运行结果批准当前节点。物理文档可以合并，但 C01—C40 的逻辑节点仍不能删除、重编号或被导航、Workflow、代码和测试替代。

## 文件职责与保存边界

本文件只保存跨项目可复核的生命周期方法：C01—C40 的逻辑顺序和返回关系、C08 与 C09/C18/C22 的不同职责、C 主链与 U01—U24 横向检查域的正交关系、项目树等五类结构的区别，以及 Evidence→Verdict→Delivery→Claim 和变更失效传播的不变量。

本文件不保存当前真实项目的业务答案、U01—U24 检查结果、人工确认正文、当前问题和决定、运行参数、Evidence、Verdict、Delivery 或 Claim。那些内容必须留在当前项目各自的唯一权威文件或具体运行实例中。下文旧英文路径和样板状态只用于 Brownfield 对账，不能解释为当前中文项目的实时状态或推荐目录。

## 完整主链

    C01 用户原始表达
    → C02 原始需求记录
    → C03 项目目标与范围
    → C04 事实、样例、反例、冲突与 Unknown
    → C05 AI需求发现与对象分类
    → C06 业务需求叙事
    → C07 草稿需求列表与需求树
    → C08 人工业务确认
    → C09 业务范围基线
    → C10 场景与触发
    → C11 业务链路
    → C12 业务能力
    → C13 功能列表
    → C14 全类型需求细化
    → C15 行为规格与行为案例
    → C16 状态、时序与因果模型
    → C17 验收判据与测试空间
    → C18 完整需求基线
    → C19 存量实现盘点与差距对账〔Brownfield 条件分支〕
    → C20 候选方案比较 / Research / ADR
    → C21 Solution / Architecture Design
    → C22 架构基线
    → C23 详细工程设计
    → C24 验证设计
    → C25 Spec
    → C26 追溯闭合门
    → C27 工程任务列表与任务树
    → C28 Workflow
    → C29 Skill / Tool / Model / Context 投影
    → C30 Implementation
    → C31 Build / Static / Unit / Contract / Integration
    → C32 Preflight
    → C33 Smoke / 代表样例
    → C34 Release Readiness
    → C35 Controlled Run
    → C36 Evidence
    → C37 Independent Verdict
    → C38 Delivery
    → C39 Bounded Claim
    → C40 Feedback / Change / Invalidation / Rebuild / Retest / Retrospective

## C01–C40 旧样板投影（非当前项目状态）

状态含义：existing 表示对象和职责已存在；partial 表示有内容但未确认或未闭合；missing 表示当前没有所需对象；not_applicable 表示本阶段明确不执行，但逻辑节点仍保留。

| ID | 逻辑节点 | 人类权威或主要文件 | 样板状态 | 当前证据与缺口 |
|---|---|---|---|---|
| C01 | 用户原始表达 | 对话原文；requirements/discovery/RAW-REQUESTS.md 登记 | existing | 原始文本在对话，仓库登记来源和边界 |
| C02 | 原始需求记录 | requirements/discovery/RAW-REQUESTS.md | existing | 有 source ID、历史快照和哈希 |
| C03 | 项目目标与范围 | requirements/PROJECT-GOAL-AND-SCOPE.md | partial | 文档资产化目标已迁移，C09 尚未重新建立 |
| C04 | 事实、样例、反例、冲突与 Unknown | requirements/discovery/REQUIREMENT-DISCOVERY.md；requirements/REQUIREMENTS.md | partial | 有实例和 Unknown 台账，仍有八项未决 |
| C05 | AI需求发现与对象分类 | requirements/discovery/REQUIREMENT-DISCOVERY.md；governance/COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md | partial | 候选与替代解释存在，未晋升 |
| C06 | 业务需求叙事 | requirements/PROJECT-GOAL-AND-SCOPE.md | partial | 当前项目叙事可读，等待产品负责人确认 |
| C07 | 草稿需求列表与需求树 | requirements/REQUIREMENTS.md；requirements/REQUIREMENT-TREE.md | existing | 22 条稳定 draft 和派生树；树不生成需求 |
| C08 | 人工阅读、讨论、纠正和决定 | requirements/BASELINES.md | missing | 没有合法人工决定记录；C08 本身不是基线 |
| C09 | 业务范围基线 | requirements/BASELINES.md | missing | 等待 C08 的人工决定固化 |
| C10 | 场景与触发 | requirements/BUSINESS-SCENARIOS-AND-CHAINS.md | partial | 8 个 draft 场景 |
| C11 | 业务链路 | requirements/BUSINESS-SCENARIOS-AND-CHAINS.md | partial | 4 条业务链，尚未进入 C18 基线 |
| C12 | 业务能力 | requirements/FUNCTIONS.md | partial | 能力视角已重建为 5 组人类可读能力视角，等待 C18 确认 |
| C13 | 功能列表 | requirements/FUNCTIONS.md | partial | 12 项人类可读功能说明已建立，尚未进入 C18 基线 |
| C14 | 全类型需求细化 | requirements/REQUIREMENTS.md | partial | 22 条需求已改写为自然语言控制面，仍待 C09/C18 确认 |
| C15 | 行为规格与行为案例 | requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md | partial | 17 条行为草稿 |
| C16 | 状态、时序与因果模型 | requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md | partial | 状态机、7 类关系和 3 个时序案例存在 |
| C17 | 验收判据与测试空间 | requirements/ACCEPTANCE-AND-TEST-SPACE.md | partial | 22 条判据和测试维度存在，尚未进入 C18 基线 |
| C18 | 完整需求基线 | requirements/BASELINES.md | missing | 被尚未建立的 C09 阻断 |
| C19 | 存量实现盘点与差距对账 | references/legacy-code-status.zh.md；design/WORKFLOW-MAP.md | partial | 样板为 Brownfield；只能对账，不能回写需求 |
| C20 | 候选方案比较 / Research / ADR | references/；design/SOLUTION-AND-ARCHITECTURE.md | partial | 有历史选择资料，尚无基于 C18 的正式 ADR 集 |
| C21 | Solution / Architecture Design | design/SOLUTION-AND-ARCHITECTURE.md | partial | 作为 Brownfield candidate，不是批准架构 |
| C22 | 架构基线 | requirements/BASELINES.md | missing | 被尚未建立的 C18 阻断 |
| C23 | 详细工程设计 | design/ENGINEERING-DESIGN.md | partial | 既有设计待 C22 对账 |
| C24 | 验证设计 | design/VERIFICATION-DESIGN.md | partial | 静态、契约和真实包层次已区分；未获 C22 授权 |
| C25 | Spec | contracts/；workflows/*/workflow.yaml | partial | 现有精确契约是 Brownfield 资产，不反向成为批准 Spec |
| C26 | 追溯闭合门 | requirements/TRACEABILITY.md | partial | 类型化链存在；C18/C22 后段边仍 blocked |
| C27 | 工程任务列表与任务树 | execution/ENGINEERING-TASKS.md；execution/ENGINEERING-TASK-TREE.md | partial | 既有 ET 投影待 C22；无来源任务不得晋升 |
| C28 | Workflow | workflows/；workflow-registry.yaml | existing | 现有 Workflow 可定位，但不是需求权威 |
| C29 | Skill / Tool / Model / Context 投影 | skill-registry.yaml；tool-registry.yaml；contracts/projection-record.yaml | partial | 注册资产存在；当前无基于已建立 C22 的新投影 |
| C30 | Implementation | adapters/；skills/*/scripts | existing | Brownfield 实现存在，不证明需求正确 |
| C31 | Build / Static / Unit / Contract / Integration | tests/；tests/validate_workflow_package.py | existing | 静态基线可执行；不能证明生产能力 |
| C32 | Preflight | references/execution-preflight.zh.md；contracts/workflow-a-preflight.yaml | existing | 执行前投影存在；本轮未运行真实来源 preflight |
| C33 | Smoke / 代表样例 | tests/fixtures；tests/runs；路线 benchmark 规格 | partial | 历史 fixture/smoke 只证明限定测试，不外推生产 |
| C34 | Release Readiness | references/quality-targets.zh.md；workflows/*/ACCEPTANCE.zh.md | partial | 有质量门，无当前 C09/C18/C22 基线和生产 readiness Verdict |
| C35 | Controlled Run | contracts/workflow-run-record.yaml；runs/ | not_applicable | 本轮明确禁止真实转换 |
| C36 | Evidence | contracts/stage-evidence.yaml；runs/ | not_applicable | 本轮只产生静态迁移证据，不产生真实转换 Evidence |
| C37 | Independent Verdict | contracts/acceptance-scorecard.yaml；独立 validator | not_applicable | 没有当前真实包，不签发业务 Verdict |
| C38 | Delivery | 未来当前范围交付记录 | not_applicable | 本轮只交付治理文档变更 |
| C39 | Bounded Claim | requirements/TRACEABILITY.md；governance/ARTIFACT-AND-CHANGE-GOVERNANCE.md | not_applicable | 不声明真实转换或生产能力 |
| C40 | Feedback / Change / Invalidation / Rebuild / Retest / Retrospective | governance/ARTIFACT-AND-CHANGE-GOVERNANCE.md；contracts/change-record.yaml | partial | 迁移变更已登记；仍需完成全量验证和新专家复审 |

## 人工决定与三道不可跨越的基线

- C08 是人工阅读、讨论、纠正和决定，不是基线，也不能被“文档已生成”或 AI 自检替代。
- C09 是业务范围基线：固化 C08 对目标、角色、范围、非目标、核心需求、硬约束和阻断 Unknown 的决定；只授权继续展开场景、功能和行为。
- C18 是完整需求、行为和验收基线：固化全类型需求、场景、功能、行为、状态、时序、恢复和验收；建立后才允许方案与架构设计。
- C22 是方案与架构基线：固化方案、边界、组件、I/O、Data、State、Permission、Error、Quality Realization 和 Verification Design；建立后才允许正式 Spec 和工程任务。

如果机器契约以后需要稳定标识，只使用 `baseline-scope`、`baseline-requirements` 和 `baseline-architecture`；人类文件始终使用 C09、C18、C22 的语义名称。

## C 主链与 U01—U24 横向检查域

- C01—C40 回答“对象在生命周期的哪个节点形成、由哪个门解锁、失败后返回哪里”。
- U01—U24 回答“跨节点检查什么、证据在哪里、是否完整、缺口应返回哪个 C 节点”。
- 一个 U 域可以检查多个 C 节点，一个 C 节点也可以同时受多个 U 域检查；二者是多对多关系，不是两条互相竞争的 Workflow。
- U 域不创建第二业务权威，也不保存项目答案；项目化检查规则、证据位置和返回节点应由当前项目治理文件承载。
- U 检查失败时返回最早受影响的 C 节点，不能只在发现问题的下游节点补文件来掩盖上游缺口。

## 五类结构不得互相替代

| 结构 | 唯一回答的问题 | 不能替代 |
|---|---|---|
| 项目树 | 文件和对象放在哪里、当前有什么 | 需求关系、任务依赖、运行状态 |
| 需求树 | 已有需求怎样按目标和主题组织 | 工程任务、执行顺序、Workflow 状态 |
| 工程任务树 | 批准任务怎样依赖、排序、并行和汇合 | 业务语义、运行时状态 |
| Workflow 状态图 | 运行怎样流转、分支、失败、取消和恢复 | 文件导航、需求组织、任务权威 |
| 追溯矩阵 | Requirement 到 Claim 之间有哪些多对多依据和失效边 | 任一对象的正式正文或执行顺序 |

## Evidence 到 Claim 的限制链

    Controlled Run
    → Evidence
    → Independent Verdict
    → Delivery
    → Bounded Claim

- Run 完成只说明执行事件结束，不证明 Evidence 完整。
- Evidence 必须保留来源身份、新鲜度和未支持的结论，不能由摘要替代原始证据。
- Verdict 必须引用验收判据和 Evidence，并保持相对于被验对象的独立性。
- Delivery 只能包含 Verdict 明确允许的对象。
- Claim 的范围不得超过 Verdict；上游对象或 Evidence 失效时，下游 Verdict、Delivery 和 Claim 必须同步失效或进入复核。

需求阶段的通用操作方法、AI 默认推进范围、人工治理点和停止条件，统一以 [governance/REQUIREMENTS-GOVERNANCE.md](governance/REQUIREMENTS-GOVERNANCE.md) 为准；它回答未来新项目怎样从 C01 推到 C18，而不是当前样板项目本身想做什么。

## P01–P16 的保留边界

P01–P16 只作为机器合同中的压缩导航投影，不是第二条权威流程：

| P 导航 | C 主链范围 |
|---|---|
| P01 | C01–C03 |
| P02 | C02–C04 |
| P03 | C04 |
| P04 | C05 |
| P05 | C06–C07 |
| P06 | C08–C09 |
| P07 | C10–C18 |
| P08 | C20 |
| P09 | C21–C23 |
| P10 | C24–C27 |
| P11 | C28 |
| P12 | C29 |
| P13 | C33 |
| P14 | C32、C35–C36 |
| P15 | C34、C37–C39 |
| P16 | C40 |

C19 Brownfield 分支由项目条件决定，不被压缩成通用起点。

## 推进不变量

- 工程文件、Workflow、代码、测试和历史 Run 不得反向确认 C01–C18。
- AI 可以起草、分类、比较和检查，不能自我建立 C09、C18、C22 基线，也不能自我签发 Verdict 或 Claim。
- observed order、causal、dependency、priority、reading、workflow 和 run order 分开。
- 上游变化必须使受影响下游 review_required、stale 或 invalidated，并从最早受影响门重建。
- Run 成功不等于 Evidence 完整，不等于独立 Verdict，不等于 Delivery 或 Claim。
