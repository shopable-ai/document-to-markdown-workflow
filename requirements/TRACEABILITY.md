---
title: "09 需求内部追溯"
description: "记录需求到 Claim 的类型化关系、影响闭包和当前迁移影响。"
order: 100
---

# 文档资产化需求到 Claim 的类型化追溯

> **Brownfield 参考：** 当前尚未解锁正式追溯文件，本文件不能证明 Requirement→Claim 已闭合。当前业务语义见[项目需求说明](../项目需求/说明.md)。

## 对象链

Requirement → Scenario → Function → Behavior → Acceptance → Design → Spec → Task → Workflow → Code → Test → Run → Evidence → Verdict → Delivery → Claim。

上游业务对象当前由 `项目需求/` 中文 Markdown 定义；本文件和 Design 之后的现有文件在 V2/V3 前都只是 Brownfield 候选或参考。

## 边类型

| edge_type | from | to | 语义 |
|---|---|---|---|
| exercised_by | Requirement | Scenario | 哪个情境触发需求 |
| realized_by | Requirement/Scenario | Function | 哪个用户可观察功能承担 |
| specified_by | Function | Behavior | 正常、异常和恢复行为 |
| accepted_by | Requirement/Function/Behavior | Acceptance | 事前通过方向 |
| designed_by | Requirement/Acceptance | Design | 确认需求如何实现 |
| constrained_by | Design | Spec | 精确字段和不变量 |
| decomposed_to | Spec | Task | 可实现工作包 |
| orchestrated_by | Task | Workflow | 执行编排 |
| implemented_by | Task/Spec | Code | 代码位置 |
| verified_by | Requirement/Spec/Code | Test | 静态或动态验证 |
| executed_as | Workflow/Projection | Run | 一次受控运行 |
| evidenced_by | Run/Acceptance | Evidence | 可重读证明 |
| judged_by | Evidence | Verdict | 独立判断 |
| delivered_as | Verdict | Delivery | 实际交付 |
| supports | Verdict/Delivery | Claim | 在边界内支持声明 |

## 当前需求映射

| requirement_refs | scenario_refs | function_refs | behavior_refs | acceptance_refs | design_refs | spec/task/workflow/code/test | run/evidence/verdict/claim | status |
|---|---|---|---|---|---|---|---|---|
| REQ-DOC-001、008、022 | SCN-DOC-001、002 | FUN-DOC-001、FUN-DOC-004 | BHV-DOC-001、BHV-DOC-002 | AC-DOC-001、AC-DOC-008、AC-DOC-022 | candidate: source and boundary design | Brownfield only; blocked before V3 | none-current | draft-no-baseline |
| REQ-DOC-002..004、009、012 | SCN-DOC-001、004 | FUN-DOC-001、FUN-DOC-002、FUN-DOC-005、FUN-DOC-006 | BHV-DOC-003..005、BHV-DOC-008、BHV-DOC-009、BHV-DOC-011 | AC-DOC-002..004、AC-DOC-009、AC-DOC-012 | candidate: content and reader design | Brownfield only; blocked before V3 | none-current | draft-no-baseline |
| REQ-DOC-005..007、010、011 | SCN-DOC-003 | FUN-DOC-003、FUN-DOC-008、FUN-DOC-009 | BHV-DOC-006、BHV-DOC-007、BHV-DOC-010、BHV-DOC-011 | AC-DOC-005..007、AC-DOC-010、AC-DOC-011 | candidate: visual asset design | Brownfield only; blocked before V3 | none-current | draft-no-baseline |
| REQ-DOC-013、014、021 | SCN-DOC-006 | FUN-DOC-007、FUN-DOC-008 | BHV-DOC-013 | AC-DOC-013、AC-DOC-014、AC-DOC-021 | candidate: route and permission design | Brownfield only; blocked before V3 | none-current | draft-no-baseline |
| REQ-DOC-015..018 | SCN-DOC-004、005 | FUN-DOC-005、FUN-DOC-010、FUN-DOC-011 | BHV-DOC-012、BHV-DOC-014、BHV-DOC-015 | AC-DOC-015..018 | candidate: execution and verification design | Brownfield only; blocked before V3 | none-current | draft-no-baseline |
| REQ-DOC-019、REQ-DOC-020 | SCN-DOC-007、SCN-DOC-008 | FUN-DOC-012、non-goal-boundary | BHV-DOC-016、BHV-DOC-017 | AC-DOC-019、AC-DOC-020 | candidate: optional downstream boundary | Brownfield only; blocked before V3 | none-current | draft-optional |

范围符号 .. 仅聚合连续、已在 REQUIREMENTS/FUNCTIONS/BEHAVIOR/ACCEPTANCE 中定义的 ID，不生成对象。

## 影响闭包

1. 需求内容、状态或版本变化时，先找所有 exercised_by、realized_by、specified_by 和 accepted_by 下游。
2. 未受影响必须有明确理由；不能默认 unaffected。
3. 关系不完整或影响不确定时标记 review_required。
4. 与新上游不再一致的设计、Spec、Task、Workflow、Test 为 stale 或 invalidated。
5. 受影响的 Run/Evidence/Verdict/Claim 不能继续支持当前结论；从最早受影响门重建。
6. 新结果使用新 Run 和 Evidence，不覆盖历史。

## 当前迁移影响

迁移前 GOAL/TASK-TREE 是人类需求来源，但职责混载；其原文已归档。新文件未获 V1/V2 确认，因此现有 Workflow、代码和测试仅被登记为 Brownfield 候选，不被写入业务功能卡当作已批准实现。
