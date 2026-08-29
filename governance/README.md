# 治理层入口

本目录定义跨项目、跨基线、跨设计和跨运行的控制规则。它不保存样板项目业务需求，也不记录一次 Run 的状态。

## 文件职责

| 文件 | 唯一职责 | 状态 | 默认加载 |
|---|---|---|---|
| [AI-WORKFLOW-UNIVERSE.md](AI-WORKFLOW-UNIVERSE.md) | U01–U24 完整性域及到 C/文件的映射 | authoritative-governance | 架构设计、覆盖审查、重大变更 |
| [REQUIREMENTS-GOVERNANCE.md](REQUIREMENTS-GOVERNANCE.md) | 未来新项目在 C01–C18 的 AI 默认推进、人类治理点、基线门与停止条件 | authoritative-governance | 需求阶段、迁移前、需求纠偏时 |
| [COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md](COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md) | AI 推理对象、认知负荷和时序关系叠加层 | reference-candidate + normative guardrails | 需求发现、复杂关系、失败重入时 |
| [ARTIFACT-AND-CHANGE-GOVERNANCE.md](ARTIFACT-AND-CHANGE-GOVERNANCE.md) | 唯一权威、生命周期、迁移、漂移和失效传播 | authoritative-governance | 任何修改、验收和回退时 |

根目录 [PROJECT-DELIVERY-WORKFLOW.md](../PROJECT-DELIVERY-WORKFLOW.md) 是 C01–C40 唯一推进主链；本目录不维护第二套顺序。

## 目录生命周期与默认上下文

| 目录 | 内容状态 | 前期 | V2 后 | 执行期 | 运行后 |
|---|---|---|---|---|---|
| requirements/ | business authoritative/derived | 默认加载 | 权威需求按需加载 | 只读，不进最小生产上下文 | 变更时重载 |
| requirements/discovery/ | candidate/reference | 默认或按需 | 退出默认上下文 | 不加载 | 变更、冲突、审计时加载 |
| governance/ | authoritative/reference | 选择性加载 | 设计门加载 | 默认不加载全文 | 失效、复盘时加载 |
| design/ | candidate 或 architecture authoritative | V2 前只读候选 | V2/V3 阶段加载 | 默认不加载全文 | 设计变更时加载 |
| execution/ | engineering authoritative/derived | V3 前 blocked | V3 后生成和审查 | 人工执行时加载 | 任务复盘时加载 |
| contracts/ | machine authoritative | 不定义业务 | V3 后精确投影 | 运行按需加载精简契约 | schema 复盘时加载 |
| workflows/ | execution projection | 不参与需求确认 | V3 后投影 | 按选定 Workflow 加载 | Run 复盘时定位 |
| runs/ | runtime evidence | 不加载 | 不加载 | 当前 run 只加载当前范围 | 只读历史 |
| archive/ | archived | 不加载 | 不加载 | 不加载 | 审计或迁移追溯时加载 |
| .audit/ | local review snapshot | 不加载 | 审查时生成 | 不参与运行 | 复盘时按需 |

生产运行继续遵循精简 runtime context，不能因为本架构文件更完整就让执行器默认读取需求史、推理史或实现源码。
