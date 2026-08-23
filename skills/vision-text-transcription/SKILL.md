---
name: vision-text-transcription
title: "页面视觉理解（文字模式兼容别名）"
description: 兼容别名；使用 vision-page-analysis 的 mode=text，实际路线由真实页面基准和页面质量守卫选择，不保留历史固定 fallback 顺序。
order: 9
---

# 页面视觉理解（文字模式兼容别名）

此 Skill ID 仅为旧调用方保留，正式入口是 [`vision-page-analysis`](../vision-page-analysis/SKILL.md)。
文字模式没有固定 fallback 顺序。Apple Vision OCR 加确定性 Markdown 结构重建、mac96 小视觉模型、
mac96 30B 视觉模型必须先在同一批真实页面上比较，再按页面质量守卫选择和升级。在线路径只有
当前任务明确授权时才参与，不创建新的 HTTP/SSH 适配器。不读取其他图片、文件、OCR JSON、bbox 或前后页面。

无论使用哪条路线，文字识别必须保留页面全部可见文字；Markdown 结构重建不得臆造标题、段落、
目录或列表；微信号、推广语和二维码附近说明不得在提取阶段静默删除。广告分类及处理使用独立
策略，默认 `preserve-only`。

旧适配器 [`adapters/mac96_vision_text_transcription.py`](../../adapters/mac96_vision_text_transcription.py)
只是新适配器的 `mode=text` 兼容包装，不应再新增独立请求逻辑。`needs-review` 不是自动
fallback，只用于真实冲突或未裁决关系。
