---
title: "08 需求确认与三次基线"
description: "定义 V1 业务范围、V2 完整需求和 V3 架构基线的门、状态与确认记录。"
order: 90
---

# 文档资产化项目三次基线

## V1 业务范围基线

固定：直接交付、目标角色、来源类型、范围、非目标、核心需求、授权边界和阻断型 Unknown。

V1 必须回答：

- 直接交付是否仍为“当前来源对应的独立 Markdown”；
- 22 条需求哪些 confirmed、rejected 或需要修订；
- 复杂视觉、模型授权、多格式候选和可选下游的范围；
- 哪些 Unknown 阻断后续。

V1 只授权继续展开场景、功能、行为和验收方向；不授权正式工程设计。

## V2 完整需求基线

固定：需求列表、场景、功能卡、行为、数据、权限、隐私、安全、质量、恢复、时序、状态、验收和追溯。

V2 必须证明：

- 每个功能回指 confirmed 需求和场景；
- 正常、边界、拒绝、异常、恢复和 Unknown 行为都可定位；
- 简单/复杂视觉、候选来源、页面阶段门和独立验收边界已被人工确认；
- 测试空间覆盖输入、内容、关系、授权、失败、恢复和追溯。

V2 通过后，discovery 降为 reference；之后才可进入方案和架构设计。

## V3 架构基线

固定：方案选择、系统边界、关键组件、I/O、Data、State、Permission、Error、Quality Realization 和 Verification Design。

V3 必须证明设计消费 V2，而不是从现有 Workflow、代码或测试反向定义需求。V3 通过后才允许生成正式 Spec、Task 和 Workflow 投影。

## 当前状态

| baseline_id | 当前状态 | 阻断项 | 允许动作 |
|---|---|---|---|
| V1 | pending-human-reconfirmation | UNK-DOC-001..008 | 人工阅读、修订和确认目标及需求 |
| V2 | blocked-by-V1 | V1 未通过；功能、行为、验收均为 draft | 作为架构样板审阅，不批准工程 |
| V3 | blocked-by-V2 | V2 未通过 | 只允许迁移既有 Brownfield 设计为候选投影 |

## 确认记录必备字段

baseline_id、baseline_version、input_refs_and_hashes、included_ids、excluded_ids、blocking_unknowns、confirmed_by、confirmed_at、supersedes、status。

当前没有 confirmed 的 V1、V2 或 V3 记录；静态测试不能替代人工基线。
