---
name: page-markdown-unit
title: "单页 Markdown 工作单元"
description: "把已存在的读者层 Markdown 按页面独立拆分、结构守卫、人工复核并确定性合并，保持来源文字、页面图链接和未决关系边界。"
order: 11
---

# 单页 Markdown 工作单元

## 职责

为 Markdown 读者层修复建立可独立读取的页面文件。每个页面单元只处理一个页面，之后由确定性的合并器按页面编号生成派生预览或最终 Markdown。

## 正式格式来源

普通 Markdown 的标题、正文、列表、表格、关系和页面图阅读顺序沿用 [`markdown-structure-builder`](../markdown-structure-builder/SKILL.md)。本 Skill 只补充单页边界、阶段门、合并和预览规则，不替代该 Skill。

## 单页输入边界

- 修复输入是当前页面的 `p<page-3-digits>.md`；只有 `reader_image_action` 不为 `omit` 时才同时使用同一页面的原始页面图。
- 可以读取当前页面已有 Markdown 和当前页面图，不能为了修复一页加载其他页面正文。
- OCR JSON、bbox、置信度、逐页 Vision JSON、原始 `manifest.yaml` 和页面图是只读来源证据；本 Skill 不运行 OCR、Apple Vision、mac96 或在线模型。
- 不能跨页、跨列、跨视觉容器或跨未确认图示关系拼接文字。

## 页面单元格式

页面单元必须遵循 [`contracts/page-markdown-unit.yaml`](../../contracts/page-markdown-unit.yaml)，并按固定顺序提供：

1. 页面编号元数据
2. 语义标题
3. 正文
4. 列表/图示文字
5. 表格与公式
6. 解读
7. 举例与思考
8. 模型结构
9. 已确认的关系说明
10. 原始页面图（仅需要保留时）
11. 待核对项

`实用公式` 和公式本身各自单独成行。举例不能与公式或解读粘成同一个列表项。图示标签应留在列表/图示文字或模型结构中，不能伪装成普通正文。

只有当页面本身只是章节分隔标题、仍需保留页码或来源追溯时，允许使用结构例外：frontmatter 只写 `type: divider`，正文只保留第一个 Markdown 标题作为唯一标题源。此类单元不填写 `title`/`semantic_title`，不保留原始页面图链接，也不进入正文路由。来源图片默认仍作为工程证据保留；用户明确要求物理删除时，必须留下包含原 SHA-256、页码、资产 ID 和删除授权边界的 `evidence/visual-asset-pruning.json`，原始 manifest 不改写，由独立 validator 把该报告作为覆盖层校验。

## 修复和关系边界

- 页面单元生成后先调用 [`clean-page-markdown-noise`](../clean-page-markdown-noise/SKILL.md) 做确定性页眉页脚清洗；先 scan，再按同一 profile apply，之后才进入语言模型内容修复。
- 批量清洗只能跨页统计边界短行。疑似正文、内部横线、页面图链接和 `Unknown`/`needs-review` 必须保留；具体规则以 [`page-markdown-noise-cleanup.yaml`](../../contracts/page-markdown-noise-cleanup.yaml) 为准。
- 只允许连接同一页面、同一阅读流中由现有文字直接证明的断句或断词。
- 未经视觉确认的箭头、层级、流程、矩阵、颜色或列关系不写入已确认关系说明。
- 无法确认的片段进入紧凑的待核对项，保留 `Unknown`/`needs-review`；已有原始页面图链接和 `reader_image_action` 不得更改。
- `unit_status: passed` 只表示页面结构和文字边界通过；`relation_status: needs-review` 可以同时存在，不能被解释为视觉关系已通过。

## 阶段门

严格按“第 1 页单页 → 第 2–5 页小批量 → 第 6–total_pages 页全量”推进。页面修复不单独运行
pytest 或独立 validator；下一阶段只要求上一阶段的页面结构守卫通过，并在规定阶段完成
人工查看证据。失败或 `needs-review` 不能解锁下一阶段。

页面修复的结构守卫由修复适配器在写回前完成，检查当前页非空、页面图链接、页面编号、
`Unknown`/`needs-review` 边界、来源文字原子和当前页写回范围。完整资产包的独立验收仍由
Workflow A 的 A7 负责，不属于单页 Markdown 修复步骤。

## 拆分、合并和预览

- 拆分使用显式页码，输出只包含选定页的页面单元。
- 合并按页面编号升序，不重新推断、摘要、翻译或补写来源文字；`reader_image_action` 与已有原始页面图链接逐字保留。
- `document-temp.md` 是当前页面单元的可审阅派生文件，不得覆盖或替代最终 `index.md`，也不得成为事实来源。
- 合并失败时不得输出成功预览。

## 边界

本 Skill 不负责重新生成 OCR、视觉证据、manifest 或页面图，也不决定 Workflow A 的 A1–A7 全局验收。
