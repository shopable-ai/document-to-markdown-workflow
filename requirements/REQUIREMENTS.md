---
title: "02 需求说明"
description: "用人类自然语言说明当前项目必须满足什么、为什么重要、哪些结果不能接受、还有什么未确认。"
order: 30
---

# 文档资产化需求说明

> **Brownfield 参考：** 本文件不再是当前业务需求控制面。当前需求只在[中文需求说明](../项目需求/需求说明.md)维护；旧条目留待迁移和 C19 差距对账。

这份文件曾是旧样板项目的业务需求控制面。它的目标是让人不用先理解 YAML、Contract、Workflow 或代码，也能判断 AI 是否理解对了需求。

稳定 ID 只是为了追溯和讨论，不表示需求已经自动获批。当前所有条目仍处于 `draft-awaiting-V1-reconfirmation`。

## 当前要先确认的几件大事

1. 当前直接交付，是否仍然是“一个当前来源对应一个可独立阅读的 Markdown 包”。
2. 复杂视觉内容是否仍然要求“Markdown 与可核对视觉证据同时存在”。
3. 多格式候选、模型授权、原图保留和下游清洗的边界是否正确。
4. 下列 22 条需求是否完整代表当前用户意图，还是仍需增删或改写。

## A. 当前文件必须被正确理解

### REQ-DOC-001 当前结果必须只绑定当前来源

我们希望：

- 每次处理都明确绑定当前来源和当前范围。
- 历史材料、旧示例和旧上下文不能污染当前结果。

为什么重要：

- 一旦来源错了，后面所有结构、图片、验收和结论都会跟着错。

不能接受什么：

- 从旧文档把当前文件没有的内容补进来。
- 来源不明却继续输出看似完整的结果。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-001
- 影响范围：`source-selection-to-claim`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-022 测试材料和旧结果不能变成当前答案

我们希望：

- fixture、旧示例和历史结果只用于测试与对账，不进入当前输出内容。

为什么重要：

- AI 很容易把“见过的内容”误当“当前来源里就有的内容”。

不能接受什么：

- 当前输出包含旧示例独有的标题、结论或内容。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-022
- 影响范围：`prompts-context-output`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

## B. 交付必须是人能直接阅读的 Markdown

### REQ-DOC-002 直接交付必须是可独立阅读的 Markdown

我们希望：

- 交付物脱离原文件仍然可阅读和搜索。
- 必要图片通过相对路径随包携带。

为什么重要：

- 用户要的不是工程中间产物，而是可以直接读、直接审查、直接复用的结果。

不能接受什么：

- 只有一个空壳 `.md` 文件。
- 正文离开原目录后图片全部断链。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-002
- 影响范围：`output-layout`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-009 读者标题必须和来源追溯分层

我们希望：

- 有语义标题时，正文只呈现主题本身。
- 页码、文件名、哈希和内部定位留在追溯层。

为什么重要：

- 读者层被来源元数据污染后，人很难直接判断内容语义。

不能接受什么：

- 标题前面默认拼接文件名、页码、哈希或对象 ID。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-009
- 影响范围：`reader-provenance`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-011 读者图片要用短而稳定的语义名

我们希望：

- 图片文件名先表达读者能理解的主题。
- 页码、哈希和资产类型保留在追溯层。

为什么重要：

- 多个 Markdown 合并时，读者层文件名不应全是工程噪声。

不能接受什么：

- 图片名主要由哈希、页码、内部类型或来源文件名堆出来。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-011
- 影响范围：`naming-manifest`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

## C. 内容、结构和关系要尽量保真

### REQ-DOC-003 文字、数字、结构和可确认关系必须尽量保留

我们希望：

- 文字、数字、章节、分区、列表、表格和可确认关系尽量被保留。

为什么重要：

- 转换不是为了“得到一段差不多的话”，而是为了保住原始材料的可读内容和可核对结构。

不能接受什么：

- 无故漏掉关键数字、层级、表格或关系。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-003
- 影响范围：`extraction-assembly`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-004 轻度整理只能改善可读性，不能改写语义

我们希望：

- 允许修正标点、段落、标题层级、列表和简单排版。
- 不能改变原文的条件、范围、语气、因果或结论。

为什么重要：

- AI 很容易在“帮忙整理”的过程中偷偷替用户重新表述需求。

不能接受什么：

- 为了更顺畅而扩写、删减、改因果或改结论。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-004
- 影响范围：`repair-assembly`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-012 无法确认的内容必须保留 Unknown 或 needs-review

我们希望：

- 看不清、来源不明、关系不稳或裁切不足时，明确保留不确定性。

为什么重要：

- 不确定性一旦被伪装成确定答案，后续所有设计和验收都会建立在假前提上。

不能接受什么：

- 为了完整或好看，把猜测写成事实。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-012
- 影响范围：`all-content-and-claims`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

## D. 复杂视觉内容既要可搜索，也要可核对

### REQ-DOC-005 简单内容不默认保留读者截图

我们希望：

- 如果文字和结构已经完整可表达，而且没有待核对项，就不要保留冗余读者截图。

为什么重要：

- 读者层不该被无意义的截图淹没。

不能接受什么：

- 明明只是简单文字，也默认塞进一堆读者截图。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-005
- 影响范围：`asset-selection`
- Unknown：UNK-DOC-002
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-006 复杂视觉内容必须同时保留 Markdown 和视觉证据

我们希望：

- 流程图、矩阵、漏斗、时间线、图表等复杂视觉内容，同时有可搜索 Markdown 和可核对图片。

为什么重要：

- 文字整理和视觉证据承担的是不同职责，谁也不能静默替代谁。

不能接受什么：

- 因为已经提取了图片里的字，就删除复杂视觉截图。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-006
- 影响范围：`asset-selection`
- Unknown：UNK-DOC-002
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-007 图文问题必须能被发现、复核和修正

我们希望：

- 遗漏、遮挡、错位、顺序错误或关系错误，都能被人看见并回查。

为什么重要：

- 看起来完整但无法发现问题，是最危险的失败形式。

不能接受什么：

- 问题被隐藏，或只能看到“转换完成”而不知道哪里还不可信。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-007
- 影响范围：`visual-review-recovery`
- Unknown：UNK-DOC-002
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-010 最终资产只保留正文真正需要的图片

我们希望：

- reader assets 中每张图都被正文实际引用，而且确实承担语义或复核作用。

为什么重要：

- 读者包不是工程缓存，不该堆满未引用的页面渲染图。

不能接受什么：

- 未引用、无用途的图片被批量复制进最终读者包。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-010
- 影响范围：`asset-package`
- Unknown：UNK-DOC-002
- 确认人：ROLE-OWNER
- 状态：draft

## E. 候选来源、授权与路线选择必须受控

### REQ-DOC-008 多格式候选不得仅凭相似名称自动合并

我们希望：

- 候选关系、差异和选择依据都被显式表达。

为什么重要：

- 同名或近名文件可能是同一材料，也可能完全不是。

不能接受什么：

- 没有确认就把多个来源静默合并成一份结果。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-008
- 影响范围：`candidate-selection`
- Unknown：UNK-DOC-003
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-013 路线选择必须基于授权、同输入校准和当前证据

我们希望：

- OCR、视觉模型和其他路线都在同一输入、同一质量维度下比较。

为什么重要：

- “能连接”不等于“能正确识别内容和关系”。

不能接受什么：

- 用 HTTP 200、模型列表或队列接收代替质量证明。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-013
- 影响范围：`model-route`
- Unknown：UNK-DOC-004
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-014 原图保留、外发和在线调用必须受当前授权约束

我们希望：

- 原图保存、外发、在线模型调用和保留期限都服从当前授权。

为什么重要：

- 没有授权边界，再好的路线也不能直接使用。

不能接受什么：

- 未经确认就外发图片，或擅自长期保留不该保留的原图。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL
- 验收方向：AC-DOC-014
- 影响范围：`assets-models-retention`
- Unknown：UNK-DOC-004
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-021 质量、重复性、耗时、资源和成本必须放在同一比较面上

我们希望：

- 路线选择不是只看某一个维度，而是在相同输入和评分表下综合比较。

为什么重要：

- 只比较成本或只比较延迟，会让“先成功再降本”失真。

不能接受什么：

- 用不同输入、不同提示、不同裁切条件得出的结果直接横比。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-021
- 影响范围：`route-selection`
- Unknown：UNK-DOC-004
- 确认人：ROLE-OWNER
- 状态：draft

## F. 过程、证据和验收必须闭环

### REQ-DOC-015 长任务必须可观察、可停止、可恢复

我们希望：

- 每个选定页面都有明确状态、停止点、恢复点和当前进度。

为什么重要：

- 人类必须能治理 AI 的推进，而不是被一次长任务绑架。

不能接受什么：

- 只知道“还在跑”，却不知道做到哪里、为什么卡住、怎么恢复。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-015
- 影响范围：`run-control`
- Unknown：UNK-DOC-005
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-016 页面修复必须先第 1 页，再 2–5 页，再全量

我们希望：

- 修复范围按阶段门推进，前一阶段没过就不能扩大。

为什么重要：

- 页面修复很容易在小问题还没搞清时就全量扩散错误。

不能接受什么：

- 前一门没过就直接全量修复。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-016
- 影响范围：`page-repair`
- Unknown：UNK-DOC-006
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-017 原始证据必须只读保留

我们希望：

- 原始 OCR、bbox、置信度、Vision JSON、页面图和原始 manifest 全部只读保留。

为什么重要：

- 没有原始证据，就无法独立复核，也无法在争议时回查。

不能接受什么：

- 修复、合并或重跑覆盖原始证据。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-017
- 影响范围：`raw-evidence`
- Unknown：UNK-DOC-001
- 确认人：ROLE-OWNER
- 状态：draft

### REQ-DOC-018 交付必须经过独立验收，而不是生成者自证

我们希望：

- 最终状态由独立 validator 重读当前包后给出 `passed`、`needs-review` 或 `failed`。

为什么重要：

- Run 成功只说明执行过，不能自己证明自己满足需求。

不能接受什么：

- 生成者自检通过，就把结果当成最终验收结论。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-018
- 影响范围：`evidence-verdict-claim`
- Unknown：UNK-DOC-001
- 确认人：ROLE-VALIDATOR
- 状态：draft

## G. 可选下游边界必须说清楚

### REQ-DOC-019 知识库低价值整页清洗只能晚于完整汇合

我们希望：

- 只有在完整页集合汇合后，才对知识库工作副本做可审计清洗。

为什么重要：

- 提前删页会让来源转换、验收和后续重建都失真。

不能接受什么：

- 一边转换一边直接删除“看起来不重要”的页面。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL、SRC-LEGACY-TREE
- 验收方向：AC-DOC-019
- 影响范围：`downstream-kb`
- Unknown：UNK-DOC-007
- 确认人：ROLE-OWNER
- 状态：draft-optional

### REQ-DOC-020 翻译、重绘和图片生成属于独立派生处理

我们希望：

- 翻译版、风格改写版、重绘版和生成图片都明确标记为派生产物，不冒充来源转换结果。

为什么重要：

- 下游派生一旦混入来源转换，就会破坏原始语义权威。

不能接受什么：

- 把视觉生成结果或改写结果当成原始来源内容。

当前状态与追溯：

- 来源：SRC-LEGACY-GOAL
- 验收方向：AC-DOC-020
- 影响范围：`downstream-visual`
- Unknown：UNK-DOC-007
- 确认人：ROLE-OWNER
- 状态：draft-optional

## Unknown 台账

| Unknown | 还没确认什么 | 为什么重要 | 谁来决定 | 当前状态 |
|---|---|---|---|---|
| UNK-DOC-001 | 这 22 条需求是否完整代表当前意图 | 阻断 V1、V2 和正式工程扩张 | ROLE-OWNER | open-blocking |
| UNK-DOC-002 | 简单内容、复杂视觉和最小充分裁切的边界 | 影响图片保留和验收 | ROLE-OWNER | open |
| UNK-DOC-003 | 多格式候选关系的默认规则与授权形式 | 影响来源选择 | ROLE-OWNER | open |
| UNK-DOC-004 | 当前允许的局域网、在线模型、原图外发和保留策略 | 影响路线和权限 | ROLE-OWNER | runtime-open |
| UNK-DOC-005 | 长任务的可见进度和恢复粒度 | 影响运行控制 | ROLE-OWNER | open |
| UNK-DOC-006 | 严格页面阶段门适用哪些文档类型和修复模式 | 影响页面修复推进 | ROLE-OWNER | open |
| UNK-DOC-007 | 知识库清洗和视觉派生是否属于长期产品范围 | 影响可选边界 | ROLE-OWNER | open-nonblocking |
| UNK-DOC-008 | 未来还支持哪些新来源类型 | 影响后续扩展和测试空间 | ROLE-OWNER | open-nonblocking |

## 与原十二类需求的对应

| 原始问题 | 需求 ID |
|---|---|
| 当前文件必须能转换成 Markdown | REQ-DOC-001、REQ-DOC-002、REQ-DOC-022 |
| Markdown 必须尽量保留原文内容和关系 | REQ-DOC-003、REQ-DOC-004、REQ-DOC-012 |
| 复杂图文导致的问题必须能够被看见和纠正 | REQ-DOC-006、REQ-DOC-007、REQ-DOC-012、REQ-DOC-014 |
| 不同来源不能被无意混成一份材料 | REQ-DOC-001、REQ-DOC-008、REQ-DOC-012 |
| 人必须看得懂结果边界 | REQ-DOC-007、REQ-DOC-012、REQ-DOC-018 |
| 读者层不能被来源元数据污染 | REQ-DOC-009、REQ-DOC-011、REQ-DOC-017 |
| 文字识别和视觉复核要使用可验证候选资源 | REQ-DOC-013、REQ-DOC-014、REQ-DOC-021 |
| 图片资产必须有多种提取方案，并且先成功再降本 | REQ-DOC-005、REQ-DOC-006、REQ-DOC-010、REQ-DOC-013、REQ-DOC-014、REQ-DOC-021 |
| 读者层图片复制必须去除工程噪声 | REQ-DOC-002、REQ-DOC-009、REQ-DOC-010、REQ-DOC-011、REQ-DOC-017 |
| 过程必须可观察、可停止、可恢复 | REQ-DOC-015、REQ-DOC-017、REQ-DOC-018 |
| 页面修复必须先单页、再小批量、最后才允许扩大 | REQ-DOC-004、REQ-DOC-012、REQ-DOC-016、REQ-DOC-017 |
| 知识库低价值页清洗必须晚于完整汇合，并且可以审计和重建 | REQ-DOC-019 |

## 当前阶段提醒

- 这里是样板项目业务需求，不是通用治理规则总表。
- 这里的稳定 ID 只帮助追溯，不能替代人工确认。
- 如果这些自然语言表述和人的理解不一致，应先改这里，再谈设计、Spec、Task、Workflow 或代码。
