# 业务功能到现有工程资产的 Brownfield 候选映射

状态：derived Brownfield view。它只回答“现有资产可能怎样承接 draft 功能、还缺什么”，不能确认需求或批准实现。

| function_id | 候选阶段/能力 | 候选 Contract/Skill/Tool | 当前状态 | 主要缺口 |
|---|---|---|---|---|
| FUN-DOC-001 | A1 来源预检 | source-inventory、workflow-a-source-runner | partial-existing | 等 V1/V3 后核对来源规则 |
| FUN-DOC-002 | 显式候选比较 | candidate compare adapter、reconciliation | partial-existing | 不得默认配对；需人工授权记录 |
| FUN-DOC-003 | A2 原生结构 + A4 候选 | native-content-model、解析 Skills | existing-candidate | 不能证明所有来源保真 |
| FUN-DOC-004 | A4 结构/关系复核 | reconciliation、vision-page-analysis | partial-existing | causal/Unknown 和人工复核闭合 |
| FUN-DOC-005 | A3/A4 视觉判定 | visual-asset-classification/pruning | partial-existing | 简单/复杂阈值待 V2 |
| FUN-DOC-006 | A3 资产提取/裁切 | source-visual-asset、asset-extraction-strategy | partial-existing | Permission 和最小充分裁切证据 |
| FUN-DOC-007 | A5 Markdown 组装 | page-markdown-unit、markdown-content-repair | existing-candidate | 需求确认与跨格式覆盖 |
| FUN-DOC-008 | A6 命名/来源登记 | source-visual-asset、output layouts | partial-existing | reader/provenance 同步漂移检查 |
| FUN-DOC-009 | A1–A7 横向 Unknown | failure-report、reconciliation、stage-evidence | partial-existing | owner/impact/resolution 实例闭合 |
| FUN-DOC-010 | A4 路线校准 | recognition benchmark/quality assessment | partial-existing | 当前来源的真实授权和基准 |
| FUN-DOC-011 | A1/A5/A7 和后台任务 | long-task-interaction、workflow-run-record | partial-existing | 业务确认后的 stage gate 对账 |
| FUN-DOC-012 | A7 独立验收 | Workflow A ACCEPTANCE、real output validator | partial-existing | 项目 AC-DOC 与 Workflow 投影闭合 |
| FUN-DOC-013 | 可选 post-complete 清洗 | ebook-page-pruning | existing-optional-candidate | V1 是否保留为项目范围 |
| FUN-DOC-014 | 可选 A→B handoff/Workflow B | workflow-handoff、Workflow B | existing-optional-candidate | 派生标签、授权和独立 Verdict |

## 文件关系

requirements → design candidate → contracts/spec candidate → execution tasks → workflow → skills/tools → implementation/tests → Run/Evidence/Verdict/Claim。

当前只有 requirements 到 acceptance 的 draft 追溯已经实例化；V2/V3 未通过，因此后半段边一律是 candidate 或 blocked。

## Brownfield 对账规则

1. 只读盘点 existing、partial、missing 和 incompatible。
2. 每个现有工程对象回指 draft requirement/function，但不得改变它们。
3. V2 确认后重新计算差距；V3 后才批准 Spec、Task 和 Workflow。
4. 历史测试和 Run 只证明其当时范围，不能填补需求确认。

