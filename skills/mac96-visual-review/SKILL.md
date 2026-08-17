---
name: mac96-visual-review
title: "页面视觉理解（结构模式兼容别名）"
description: 兼容别名；请使用 vision-page-analysis 的 mode=layout 或 mode=full。原有 mac96 视觉复核能力已合并到统一页面视觉理解 Skill。
order: 10
---

# 页面视觉理解（结构模式兼容别名）

此 Skill ID 仅为旧调用方和历史证据保留，不再作为独立视觉能力。正式入口是
[`vision-page-analysis`](../vision-page-analysis/SKILL.md)。

旧调用方应把此 ID 映射到统一适配器的 `mode=layout`，需要同时输出 Markdown 和结构时使用
`mode=full`。视觉层失败时不得用 Apple Vision OCR 伪造结构复核。
