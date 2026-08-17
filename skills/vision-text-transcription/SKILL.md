---
name: vision-text-transcription
title: "页面视觉理解（文字模式兼容别名）"
description: 兼容别名；使用 vision-page-analysis 的 mode=text，优先 SSH 调用 mac96，mac96 不可用时直接使用当前对话在线视觉模型，最后才使用 Apple Vision OCR。
order: 9
---

# 页面视觉理解（文字模式兼容别名）

此 Skill ID 仅为旧调用方保留，正式入口是 [`vision-page-analysis`](../vision-page-analysis/SKILL.md)。
文字模式的固定 fallback 顺序是：

`SSH mac96 → 当前对话在线视觉模型 → Apple Vision OCR`

mac96 不可用、超时或输出不可解析时，直接把同一张当前图片交给当前对话在线视觉模型；
只有当前对话模型也不可用、超时或输出不可解析时，才调用 Apple Vision OCR。在线路径不创建
新的 HTTP/SSH 适配器，不读取其他图片、文件、OCR JSON、bbox 或前后页面。

旧适配器 [`adapters/mac96_vision_text_transcription.py`](../../adapters/mac96_vision_text_transcription.py)
只是新适配器的 `mode=text` 兼容包装，不应再新增独立请求逻辑。`needs-review` 不是自动
fallback，只用于真实冲突或未裁决关系。
