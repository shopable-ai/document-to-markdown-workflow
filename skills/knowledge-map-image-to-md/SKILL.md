---
name: knowledge-map-image-to-md
title: "知识地图图片转 Markdown（兼容入口）"
description: 早期知识地图图片转 Markdown 技能的兼容入口；将知识地图、图谱图片或其已解压来源路由到 document-to-markdown-assets Workflow，并加载知识地图差异策略。
order: 10
---

# 知识地图图片转 Markdown（兼容入口）

这是早期 `$knowledge-map-image-to-md` 名称的兼容入口，不再维护一套独立的
OCR、视觉复核、Markdown 组装或 ledger 流程。当前唯一的执行主链路是
`document-to-markdown-assets` 的 A1-A7；知识地图只通过
[知识地图差异策略](../../references/knowledge-map-profile.zh.md) 增加领域门禁。

## 路由规则

1. 将单张图片、PPTX、PDF 或 DOCX 交给 Workflow A 的 A1-A3，保留原生结构和来源视觉资产。
2. 在进入昂贵的视觉复核前，应用 profile 的完成态、重复预检和语义资格门禁。
3. 让 A4-A7 负责视觉复核/OCR、来源保真、Markdown、manifest 和资产包验收；不要在本入口复制这些阶段。
4. 批处理需要的 append-only ledger、来源 occurrence 和版本仲裁只作为 profile 的增量证据；没有对应实现时必须标记 `needs-review`，不能伪造成功。

## 边界

- 这个入口不创建新的 Workflow，也不加入 A1-A7 的 `stages` 列表。
- `document.md`、`manifest.yaml`、`assets/` 和 `reports/` 的正式布局以 Workflow A 契约为准；知识地图批处理的 `_过程文件/` 与 `运行记录/` 只能是额外过程层。
- ZIP/RAR/7z 是来源发现容器，不代表当前 Workflow A 已提供安全解压实现。未完成安全解压时先停在 `needs-review` 或显式失败态。
- 不调用 GPT-image-2，不把来源图自动升级为生成模板。

## 迁移原则

旧技能中的通用步骤只保留一个权威来源：阶段顺序看 Workflow A，判断与操作看各阶段 Skill，数据字段看 contracts。旧调用名只负责路由；任何新规则应先落到 profile 或现有契约，再由入口引用。
