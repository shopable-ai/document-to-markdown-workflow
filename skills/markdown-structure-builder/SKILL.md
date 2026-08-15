---
name: markdown-structure-builder
title: "Markdown 结构组装"
description: 把已复核内容按页面/章节/关系组装成可检索 Markdown。
order: 10
---

# Markdown 结构组装

## 职责

把已复核内容按页面/章节/关系组装成可检索 Markdown。

## 输入与输出

输入：reconciled-content-model、layout model。输出：最终 `document.md`。规则：保留标题、列表、表格、关系说明和不确定性；不能只输出阶段日志或文本片段。

## 固定读者顺序

每个页面或章节必须按以下顺序组装：

```text
标题 → 一句话主题/范围 → 核心文字/列表/表格/分区
→ 图示关系说明 → 场景/限制/来源与不确定性
→ 【需要保留的】原始页面图、复杂区域图、嵌入图片
→ 待核对与未知形式清单
```

图片是来源证据，不是章节导语。不得在标题后立即放图片，再把文字放到图片下方；这会破坏先读语义、后回看原图的阅读路径。图片仍必须保留并通过 manifest、`source_occurrences` 和相对链接追溯。

标题与来源定位必须分层：有可确认语义标题时，不得在标题前拼接源文件名、页码、哈希或对象 ID；页码、原始文件名和 occurrence 放入 manifest/evidence，或在正文之后作为独立来源说明。只有没有可确认语义标题时才允许使用页码 fallback，并同时标记 `needs-review`。

## 未知形式覆盖

组装前必须消费 A4 的开放集合扫描结果。页面中无法归入预设流程、矩阵、树、循环或并列类型的图形、符号或颜色编码，必须进入 `unknown_forms` 或 `needs-review`，并链接整页或局部视觉资产。不得以“提示词未列出该类型”为理由省略。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`document.md` 生成前必须有 `source_package_id`、`sections[]`、每节 `source_occurrences`/`visual_asset_ids`/`confidence`、`links[]`、`unresolved[]` 和状态。任何未决关系都要在 Markdown 可见并保留原始来源链接。
