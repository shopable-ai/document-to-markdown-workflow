# 设计层入口

design 只消费 confirmed V2 需求，不产生业务需求。当前 V2/V3 均 blocked，因此本目录内容统一是 Brownfield candidate，供差距对账和人工审查，不能作为架构基线。

| 文件 | 唯一职责 | 当前状态 |
|---|---|---|
| [SOLUTION-AND-ARCHITECTURE.md](SOLUTION-AND-ARCHITECTURE.md) | 候选方案、系统边界、组件和关键选择 | candidate-blocked-by-V2 |
| [ENGINEERING-DESIGN.md](ENGINEERING-DESIGN.md) | I/O、Data、State、Permission、Error、Quality Realization | candidate-blocked-by-V3 |
| [VERIFICATION-DESIGN.md](VERIFICATION-DESIGN.md) | 验收判据如何投影为验证层和证据 | candidate-blocked-by-V3 |
| [WORKFLOW-ARCHITECTURE.md](WORKFLOW-ARCHITECTURE.md) | Workflow、Stage、Skill、Tool、Model、Context 的层级 | Brownfield description |
| [WORKFLOW-MAP.md](WORKFLOW-MAP.md) | 业务功能到现有工程资产的候选映射和缺口 | derived Brownfield view |

阅读顺序：先 requirements/BASELINES 确认 V2 状态；未通过时只能做候选差距对账。V3 通过后才能把本目录对应版本标为 authoritative，并生成正式 Spec 和任务。
