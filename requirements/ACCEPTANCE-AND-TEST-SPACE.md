---
title: "04 验收问题"
description: "定义需求验收判据、测试空间以及 Run、Evidence、Verdict 和 Claim 的边界。"
order: 50
---

# 文档资产化项目验收判据与测试空间

状态：authoritative-draft。这里定义项目级“什么算满足需求”；Workflow 的 ACCEPTANCE 只能在 V3 后成为执行投影。

## 需求验收判据

| acceptance_id | requirement_refs | 判据方向 | 代表测试空间 | 当前状态 |
|---|---|---|---|---|
| AC-DOC-001 | REQ-DOC-001 | 所有正文和资产回指当前来源指纹；旧材料不进入结果 | 新旧来源切换、缓存、同名文件 | draft |
| AC-DOC-002 | REQ-DOC-002 | reader Markdown 可独立阅读；必要资产链接相对且可解析 | 纯文本包、带资产包、复制目录 | draft |
| AC-DOC-003 | REQ-DOC-003 | 文字、数字、章节、分区、列表、表格和可确认关系无无故遗漏 | 长文、多栏、表格、图文混排 | draft |
| AC-DOC-004 | REQ-DOC-004 | 轻度整理与来源语义等价；无扩写、删减或条件变化 | 错字、段落、标题、列表、关系说明 | draft |
| AC-DOC-005 | REQ-DOC-005 | 全部简单条件满足的内容没有冗余读者截图 | 纯文字、普通列表、简单表格 | draft |
| AC-DOC-006 | REQ-DOC-006 | 复杂视觉同时存在可搜索 Markdown 与可核对视觉证据 | 流程、矩阵、漏斗、时间线、统计图 | draft |
| AC-DOC-007 | REQ-DOC-007 | 遗漏、错位、遮挡、顺序和关系问题有可见记录及恢复入口 | 错位 PPTX、低清扫描、裁切不足 | draft |
| AC-DOC-008 | REQ-DOC-008 | 未授权候选保持独立；已确认候选保留差异和选择依据 | 同名 PDF/PPTX、短泛化名、不同页数 | draft |
| AC-DOC-009 | REQ-DOC-009 | 有语义标题时不带来源前缀；追溯层仍可定位页码和来源 | 有标题、无标题、临时标题 | draft |
| AC-DOC-010 | REQ-DOC-010 | reader assets 都被正文引用并说明用途；无批量未引用渲染图 | 整页图、区域图、嵌入图 | draft |
| AC-DOC-011 | REQ-DOC-011 | 文件名是最短可辨识语义名；追溯字段不污染读者名 | 同名冲突、跨文档合并 | draft |
| AC-DOC-012 | REQ-DOC-012 | 不可确认项有 Unknown/needs-review、owner、影响和入口 | 模糊文字、关系冲突、来源不明 | draft |
| AC-DOC-013 | REQ-DOC-013 | 路线选择基于相同输入和质量维度；连接成功不等于质量通过 | 原生、OCR、小模型、大模型、在线 | draft |
| AC-DOC-014 | REQ-DOC-014 | 所有外发、原图保存、在线调用和保留期限有当前授权 | 无授权、只允许本地、只允许结构化证据 | draft |
| AC-DOC-015 | REQ-DOC-015 | 每个选定页面有明确状态、进度、停止/取消和恢复证据 | 长文、超时、无进展、取消、恢复 | draft |
| AC-DOC-016 | REQ-DOC-016 | 第 1 页未过不启动 2–total；小批量未人工通过不启动全量 | 1 页失败、2–5 页失败、中断恢复 | draft |
| AC-DOC-017 | REQ-DOC-017 | 修复前后 raw OCR、bbox、Vision、页面图和 manifest 哈希不变 | 页面修复、合并、重跑 | draft |
| AC-DOC-018 | REQ-DOC-018 | 独立 validator 重读当前包，只输出 passed、needs-review 或 failed 及理由 | 自检、独立验收、旧 Evidence、缺资产 | draft |
| AC-DOC-019 | REQ-DOC-019 | 完整汇合后先预览；输入变化时不删除；应用后重建索引 | 页缺失、哈希变化、候选页、保留页 | draft-optional |
| AC-DOC-020 | REQ-DOC-020 | 派生产物标明翻译、重绘或生成，不冒充来源图或来源 Markdown | 翻译、改风格、图片生成 | draft-optional |
| AC-DOC-021 | REQ-DOC-021 | 质量、重复性、耗时、资源和成本用同一输入与评分表比较 | 路线基准、重试、资源不足 | draft |
| AC-DOC-022 | REQ-DOC-022 | 当前输出不包含 fixture 或旧示例独有内容 | fixture 污染、提示词残留、缓存复用 | draft |

## 测试空间

- 来源：单源、显式候选、误配候选、内容变化、范围变化、不可读来源。
- 页面：纯文字、简单表格、多栏、流程图、矩阵、时间线、复杂图表、低清扫描。
- 视觉：无需截图、区域图足够、必须整页、区域加整页、权限只允许结构化证据。
- 关系：observed order、causal、dependency、priority、reading、workflow、run order。
- 状态：正常、边界、拒绝、needs-review、failed、cancelled、恢复、stale、invalidated。
- 模型与权限：本地、局域网、在线、未授权、不可用、质量未过门。
- 追溯：标题污染、断链、未引用资产、哈希变化、旧 Evidence、生成者自检。
- 下游：完整汇合、页缺失、计划哈希变化、清洗后未重建、派生冒充原始。

## Verdict 与 Claim 边界

Run succeeded 只说明一次执行到达成功终态；Evidence 必须可重读；Verdict 必须独立；Delivery 只记录实际交付；Claim 不能超过当前来源、页码、版本、授权和未决项。

当前没有真实 Run、Evidence、Verdict 或生产 Claim。本文件的静态闭合不证明业务需求已经 V1/V2 确认。
