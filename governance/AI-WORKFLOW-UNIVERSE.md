# U01–U24 完整性域

U01–U24 是问题全集和审查覆盖域，不是 24 步 Workflow。项目推进顺序只由 [C01–C40 主链](../PROJECT-DELIVERY-WORKFLOW.md) 定义。

## 五个平面

| 平面 | 主要问题 |
|---|---|
| control | 权威、确认权、阶段门、授权、停止和失效 |
| cognition | 原始意图、事实、候选、Unknown、研究和认知负荷 |
| design | 需求、场景、功能、行为、状态、架构和验收 |
| execution | Spec、Task、Workflow、Skill、Tool、Model 和 Context |
| evidence | Run、Evidence、Verdict、Delivery 和 Claim |

## U01–U24 到主链与文件

| U ID | 覆盖域 | C 主链映射 | 主要文件 | 形态 |
|---|---|---|---|---|
| U01 | 项目与来源 | C01–C03 | requirements/discovery/RAW-REQUESTS.md；requirements/PROJECT-GOAL-AND-SCOPE.md | stage coverage |
| U02 | 原始意图 | C01–C03 | RAW-REQUESTS；PROJECT-GOAL-AND-SCOPE | stage coverage |
| U03 | 事实与未知 | C04–C05 | REQUIREMENT-DISCOVERY；REQUIREMENTS Unknown 台账 | stage coverage |
| U04 | 样例与反例 | C04 | REQUIREMENT-DISCOVERY；ACCEPTANCE-AND-TEST-SPACE | stage coverage |
| U05 | 需求发现 | C05–C07 | REQUIREMENT-DISCOVERY；REQUIREMENTS | stage coverage |
| U06 | 业务叙事 | C06 | PROJECT-GOAL-AND-SCOPE | stage coverage |
| U07 | 业务确认 | C08–C09、C18 | BASELINES | gate coverage |
| U08 | 场景与链路 | C10–C11、C15 | BUSINESS-SCENARIOS-AND-CHAINS；BEHAVIOR-STATE-AND-TEMPORAL-SPECS | stage coverage |
| U09 | 能力与功能 | C12–C13 | FUNCTIONS | stage coverage |
| U10 | 功能需求与行为 | C14–C16 | REQUIREMENTS；FUNCTIONS；BEHAVIOR-STATE-AND-TEMPORAL-SPECS | stage coverage |
| U11 | 质量与风险 | C14、C17、C20–C24、C34 | REQUIREMENTS；ACCEPTANCE-AND-TEST-SPACE；design/ | cross-stage coverage |
| U12 | 研究与决策 | C19–C22 | references/；design/SOLUTION-AND-ARCHITECTURE.md | stage coverage |
| U13 | 工程设计 | C21–C25 | design/；contracts/ | stage coverage |
| U14 | 验收与测试空间 | C17、C24、C31–C34、C37 | ACCEPTANCE-AND-TEST-SPACE；design/VERIFICATION-DESIGN.md；tests/ | cross-stage coverage |
| U15 | 执行投影 | C25–C30 | TRACEABILITY；execution/；contracts/；workflows/ | stage coverage |
| U16 | 运行控制 | C28–C35 | execution/WORKFLOW-A-EXECUTION.md；runtime contracts | cross-stage coverage |
| U17 | 运行与证据 | C35–C36 | contracts/workflow-run-record.yaml；contracts/stage-evidence.yaml；runs/ | runtime coverage |
| U18 | 独立审核与验收 | C37 | acceptance-scorecard；independent validator | gate coverage |
| U19 | 交付与声明 | C38–C39 | TRACEABILITY；delivery/claim records | outcome coverage |
| U20 | 反馈与演进 | C40 | ARTIFACT-AND-CHANGE-GOVERNANCE；change-record | lifecycle coverage |
| U21 | 时序与状态变化 | C04–C40 | BEHAVIOR-STATE-AND-TEMPORAL-SPECS；COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY；temporal-case | overlay |
| U22 | 需求推理与对象分类 | C01–C18 | REQUIREMENT-DISCOVERY；COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY | overlay |
| U23 | 认知负荷与重新进入 | C01–C40 | requirements/README；governance/README；各目录 README | overlay |
| U24 | 知识增量与长期生命周期 | C02–C40 | ARTIFACT-AND-CHANGE-GOVERNANCE；TRACEABILITY；archive/ | overlay |

## U21–U24 叠加规则

- U21 不作为 C41；任何顺序敏感对象都要区分 observed、causal、dependency、priority、reading、workflow 和 run order。
- U22 不替代需求确认；它只记录 AI 为什么提出候选、依据、替代解释和 Unknown。
- U23 通过默认加载、目录 README、摘要和稳定入口降低重入成本；压缩摘要不能删除 C/U、关键文件或样板投影。
- U24 贯穿版本、迁移、冲突、过期、失效、归档和重建；历史不被静默覆盖。

## 完整性门

validator 必须证明 U01–U24 无缺号，每个 U 至少有一个 C 映射和一个文件；U21–U24 的形态必须是 overlay。

