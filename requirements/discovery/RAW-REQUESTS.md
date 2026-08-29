---
title: "A2 原始请求与来源登记"
description: "登记本轮请求、历史来源、原始约束及其可支持和不可支持的边界。"
order: 20
---

# 原始请求与来源登记

| source_id | 来源 | 状态 | 可支持什么 | 不可支持什么 |
|---|---|---|---|---|
| SRC-20260828-01 | 2026-08-28 用户给出的本轮治理、迁移、验证与禁止项 | authoritative-input | 本轮治理需求、目录约束、C/U 模型、验收边界 | 自动确认历史业务需求或生产能力 |
| SRC-LEGACY-GOAL | [迁移前目标与需求快照](../../archive/governance-migration-20260828/GOAL.zh.md)，SHA-256 2ec1dcc6a6c2ca9c93c0009f2d9fdae4170bf4c99b736c67937d189f5d378c9b | historical-human-draft | 形成 DOC 类需求草稿和迁移来源 | 证明当前用户重新确认了全部业务细节 |
| SRC-LEGACY-TREE | [迁移前需求树快照](../../archive/governance-migration-20260828/TASK-TREE.zh.md)，SHA-256 10fb67349b379ec6dabd9acb3e3572812748d1e3f2472c7158cb549379999b35 | historical-derived-view | 保留旧分组、反例和验收问题 | 产生正式需求 ID |
| SRC-LEGACY-MAP | [迁移前工程映射快照](../../archive/governance-migration-20260828/WORKFLOW-MAP.zh.md)，SHA-256 1181975e7e8869da77778c8ec92c6efe2709f224fce29877da5e8a7ab9c7242e | historical-engineering-view | 辅助迁移既有投影 | 反向定义业务需求 |
| SRC-LEGACY-TASKS | [迁移前工程任务树快照](../../archive/governance-migration-20260828/EXECUTION-TASK-TREE.zh.md)，SHA-256 c79dc73d5bad64dccd8de76ed1262fd087572ce75650af6ecc8a4f1de00ca632 | historical-engineering-view | 保留 ET0–ET5 任务历史 | 证明任务已获当前基线授权 |

## 本轮原始约束

- 通用链从用户目标和需求开始；Brownfield 盘点只是 C19 条件分支。
- 前期 Markdown 是业务语义权威；机器索引、Contract、Spec 和 Workflow 后置。
- C01–C40 不得因物理文件合并而丢失；U01–U24 是覆盖域而非另一条线性流程。
- 当前不修改 ai-project-os，不运行真实文档转换，不提交或推送 Git，不产生生产 Claim。
- 历史实现、Workflow、测试、Run 和 fixture 只能作为 Brownfield 或工程证据，不能反向批准需求。

原始请求的完整文本保留在本次对话记录；本文件只登记可追溯摘要，不把摘要冒充逐字原文。
