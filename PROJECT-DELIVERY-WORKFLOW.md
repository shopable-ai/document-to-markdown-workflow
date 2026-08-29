# 人工治理、AI 推导、Markdown 权威、工程投影、证据闭环

| 字段 | 值 |
|---|---|
| document_id | PROJECT-DELIVERY-WORKFLOW |
| version | 0.3.0 |
| authority | canonical-human-project-chain |
| status | active-governance-model |

这是项目需求到工程交付的唯一人类主链。物理文档可以合并，但 C01–C40 的逻辑节点不能删除、重编号或被 P 导航、Workflow 阶段、代码和测试替代。

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

## C01–C40 文件映射与样板投影

状态含义：existing 表示对象和职责已存在；partial 表示有内容但未确认或未闭合；missing 表示当前没有所需对象；not_applicable 表示本阶段明确不执行，但逻辑节点仍保留。

| ID | 逻辑节点 | 人类权威或主要文件 | 样板状态 | 当前证据与缺口 |
|---|---|---|---|---|
| C01 | 用户原始表达 | 对话原文；requirements/discovery/RAW-REQUESTS.md 登记 | existing | 原始文本在对话，仓库登记来源和边界 |
| C02 | 原始需求记录 | requirements/discovery/RAW-REQUESTS.md | existing | 有 source ID、历史快照和哈希 |
| C03 | 项目目标与范围 | requirements/PROJECT-GOAL-AND-SCOPE.md | partial | 文档资产化目标已迁移，V1 尚未重新确认 |
| C04 | 事实、样例、反例、冲突与 Unknown | requirements/discovery/REQUIREMENT-DISCOVERY.md；requirements/REQUIREMENTS.md | partial | 有实例和 Unknown 台账，仍有八项未决 |
| C05 | AI需求发现与对象分类 | requirements/discovery/REQUIREMENT-DISCOVERY.md；governance/COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md | partial | 候选与替代解释存在，未晋升 |
| C06 | 业务需求叙事 | requirements/PROJECT-GOAL-AND-SCOPE.md | partial | 当前项目叙事可读，等待产品负责人确认 |
| C07 | 草稿需求列表与需求树 | requirements/REQUIREMENTS.md；requirements/REQUIREMENT-TREE.md | existing | 22 条稳定 draft 和派生树；树不生成需求 |
| C08 | 人工业务确认 | requirements/BASELINES.md | missing | 没有合法 V1 确认记录 |
| C09 | 业务范围基线 | requirements/BASELINES.md | missing | V1=pending-human-reconfirmation |
| C10 | 场景与触发 | requirements/BUSINESS-SCENARIOS-AND-CHAINS.md | partial | 8 个 draft 场景 |
| C11 | 业务链路 | requirements/BUSINESS-SCENARIOS-AND-CHAINS.md | partial | 4 条业务链，未进 V2 |
| C12 | 业务能力 | requirements/FUNCTIONS.md | partial | 能力视角已重建为 5 组人类可读能力视角，等待 V2 确认 |
| C13 | 功能列表 | requirements/FUNCTIONS.md | partial | 12 项人类可读功能说明已建立，未进 V2 |
| C14 | 全类型需求细化 | requirements/REQUIREMENTS.md | partial | 22 条需求已改写为自然语言控制面，仍待 V1/V2 确认 |
| C15 | 行为规格与行为案例 | requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md | partial | 17 条行为草稿 |
| C16 | 状态、时序与因果模型 | requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md | partial | 状态机、7 类关系和 3 个时序案例存在 |
| C17 | 验收判据与测试空间 | requirements/ACCEPTANCE-AND-TEST-SPACE.md | partial | 22 条判据和测试维度存在，未进 V2 |
| C18 | 完整需求基线 | requirements/BASELINES.md | missing | V2 被 V1 阻断 |
| C19 | 存量实现盘点与差距对账 | references/legacy-code-status.zh.md；design/WORKFLOW-MAP.md | partial | 样板为 Brownfield；只能对账，不能回写需求 |
| C20 | 候选方案比较 / Research / ADR | references/；design/SOLUTION-AND-ARCHITECTURE.md | partial | 有历史选择资料，尚无基于 V2 的正式 ADR 集 |
| C21 | Solution / Architecture Design | design/SOLUTION-AND-ARCHITECTURE.md | partial | 作为 Brownfield candidate，不是批准架构 |
| C22 | 架构基线 | requirements/BASELINES.md | missing | V3 被 V2 阻断 |
| C23 | 详细工程设计 | design/ENGINEERING-DESIGN.md | partial | 既有设计待 V3 对账 |
| C24 | 验证设计 | design/VERIFICATION-DESIGN.md | partial | 静态、契约和真实包层次已区分；未获 V3 |
| C25 | Spec | contracts/；workflows/*/workflow.yaml | partial | 现有精确契约是 Brownfield 资产，不反向成为批准 Spec |
| C26 | 追溯闭合门 | requirements/TRACEABILITY.md | partial | 类型化链存在；V2/V3 后段边仍 blocked |
| C27 | 工程任务列表与任务树 | execution/ENGINEERING-TASKS.md；execution/ENGINEERING-TASK-TREE.md | partial | 既有 ET 投影待 V3；无来源任务不得晋升 |
| C28 | Workflow | workflows/；workflow-registry.yaml | existing | 现有 Workflow 可定位，但不是需求权威 |
| C29 | Skill / Tool / Model / Context 投影 | skill-registry.yaml；tool-registry.yaml；contracts/projection-record.yaml | partial | 注册资产存在；当前无基于 confirmed V3 的新投影 |
| C30 | Implementation | adapters/；skills/*/scripts | existing | Brownfield 实现存在，不证明需求正确 |
| C31 | Build / Static / Unit / Contract / Integration | tests/；tests/validate_workflow_package.py | existing | 静态基线可执行；不能证明生产能力 |
| C32 | Preflight | references/execution-preflight.zh.md；contracts/workflow-a-preflight.yaml | existing | 执行前投影存在；本轮未运行真实来源 preflight |
| C33 | Smoke / 代表样例 | tests/fixtures；tests/runs；路线 benchmark 规格 | partial | 历史 fixture/smoke 只证明限定测试，不外推生产 |
| C34 | Release Readiness | references/quality-targets.zh.md；workflows/*/ACCEPTANCE.zh.md | partial | 有质量门，无当前 V1–V3 和生产 readiness Verdict |
| C35 | Controlled Run | contracts/workflow-run-record.yaml；runs/ | not_applicable | 本轮明确禁止真实转换 |
| C36 | Evidence | contracts/stage-evidence.yaml；runs/ | not_applicable | 本轮只产生静态迁移证据，不产生真实转换 Evidence |
| C37 | Independent Verdict | contracts/acceptance-scorecard.yaml；独立 validator | not_applicable | 没有当前真实包，不签发业务 Verdict |
| C38 | Delivery | 未来当前范围交付记录 | not_applicable | 本轮只交付治理文档变更 |
| C39 | Bounded Claim | requirements/TRACEABILITY.md；governance/ARTIFACT-AND-CHANGE-GOVERNANCE.md | not_applicable | 不声明真实转换或生产能力 |
| C40 | Feedback / Change / Invalidation / Rebuild / Retest / Retrospective | governance/ARTIFACT-AND-CHANGE-GOVERNANCE.md；contracts/change-record.yaml | partial | 迁移变更已登记；仍需完成全量验证和新专家复审 |

## 三道不可跨越的基线

1. V1 业务范围基线：固定目标、角色、范围、非目标、核心需求、硬约束和阻断 Unknown；只允许继续展开场景、功能和行为。
2. V2 完整需求基线：固定全类型需求、场景、功能、行为、状态、时序、恢复和验收；通过后才允许方案与架构设计。
3. V3 架构基线：固定方案、边界、组件、I/O、Data、State、Permission、Error、Quality Realization 和 Verification Design；通过后才允许正式 Spec 和工程任务。

样板当前为 V1 pending、V2 blocked、V3 blocked。

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
- AI 可以起草、分类、比较和检查，不能自我确认 V1/V2/V3、Verdict 或 Claim。
- observed order、causal、dependency、priority、reading、workflow 和 run order 分开。
- 上游变化必须使受影响下游 review_required、stale 或 invalidated，并从最早受影响门重建。
- Run 成功不等于 Evidence 完整，不等于独立 Verdict，不等于 Delivery 或 Claim。
