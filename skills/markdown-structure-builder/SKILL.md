---
name: markdown-structure-builder
title: "Markdown 结构组装"
description: 把已复核内容按页面、章节和图文关系组装成读者可读、可检索的 Markdown，并把 OCR 候选、页码和审核状态与读者正文隔离。
order: 10
---

# Markdown 结构组装

## 职责

把已复核内容按页面/章节/关系组装成可检索 Markdown。

## 输入与输出

输入：`reconciled-content-model`、结构模型、`manifest.yaml` 和 A4 复核证据。输出：最终 `index.md`；未决项写入 `evidence/`、`manifest.yaml` 或可选的 `reviews/markdown-content-repair.yaml`。不能只输出阶段日志或文本片段。

## 固定读者顺序

每个页面或章节按实际存在的内容，使用以下读者顺序：

```text
## 标题
→ 可选的 `> 关系：...` 摘要
→ 核心文字、列表、表格或分区
→ 可选 Mermaid（仅已确认节点—边结构）
→ 必要的原始页面图、复杂区域图或嵌入图片
```

`>` 只写复杂图片中已确认的文字关系，例如递进、包含、因果、对比、时间线、树状或矩阵；普通正文、来源说明和工程诊断不得混入引用块。无已确认关系时省略引用块。纯文字页直接输出 Markdown，不新增“无额外图示”等说明。

图片是来源证据，不是章节导语。不得在标题后立即放图片，再把文字放到图片下方；需要进入读者层的图片应在语义文字和关系之后。自动组装必须保留工程页面资产；视觉 JSON 明确给出 `text-only`、Markdown 可完整表达、无语义视觉/未决关系且 `reader_image_policy.action=omit` 时，不写读者层图片链接。字段缺失、冲突或不确定时保守保留；二维码只记为忽略元素，不生成图片链接。

标题与来源定位必须分层：标题只保留可确认的语义，排除系列名、页码、幻灯片编号、源文件名、哈希和对象 ID。不得输出“第 x 页”、`来源页`、`A4 状态`、`来源与原图`等工程标签；没有可靠标题时省略标题，或使用由同一来源正文交叉佐证的中性标题，不能回退到页码标题。

`index.md` 禁止出现 OCR 置信度、候选标题、`Unknown`、`needs-review`、`待复核`、`原文疑似`、处理阶段或审核理由。未决信息必须保留，但只能进入工程层：来源位置和资产 ID 在 `manifest.yaml`，提取与冲突证据在 `evidence/`，待人工处理的读者修复项在 `reviews/markdown-content-repair.yaml`。未批准的候选不得被写成确定事实。

图级回填只修改当前 page-unit、segment 或其他 Manifest 登记的最小内容单元。可靠 Mermaid 使用
fenced `mermaid` block，并紧跟同一来源链的 crop/enhanced PNG fallback 和图题；结构或文字不可靠时
只写 PNG。page-unit 使用 `../assets/...`，根 `index.md` 使用 `assets/...`；派生合并不得重新生成
Mermaid、改变边或删除 fallback。

## 未知形式覆盖

组装前必须消费 A4 的开放集合扫描结果。页面中无法归入预设流程、矩阵、树、循环或并列类型的图形、符号或颜色编码，必须进入 `unknown_forms` 或工程 `needs-review` 记录，并链接整页或局部视觉资产。不得以“提示词未列出该类型”为理由省略，也不得把该工程状态写入读者正文。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 工程输出必须包含来源、状态和可诊断错误信息；`index.md` 不承担这些字段。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`index.md` 生成前必须有 `source_package_id`、`sections[]`、每节 `source_occurrences`/`visual_asset_ids`/`confidence`、`links[]`、`unresolved[]` 和状态。未决关系必须在工程工件中可追溯并保留原始来源链接；只有已确认的关系才能进入 Markdown。
