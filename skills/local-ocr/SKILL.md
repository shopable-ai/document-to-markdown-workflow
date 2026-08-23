---
name: local-ocr
title: "本机 OCR"
description: 通过本机 Apple Vision OCR 保存当前页原始文字、坐标和置信度；它是待实测的文字识别候选路线，必须搭配独立 Markdown 结构重建，不能被预设为主路线或最后兜底。
order: 10
---

# 本机 OCR

## 职责

对无文字层或冲突区域执行本机 Apple Vision OCR，并保存候选文字、坐标和置信度。是否使用它由真实页面基准与页面质量守卫决定，不依赖固定 fallback 顺序。它只提供原始文字层，必须搭配 `ocr_markdown_structure.py` 才能形成 Markdown 结构候选；它不替代页面视觉结构分析，也不替代 PPTX XML、PDF 布局或 DOCX OOXML 原生提取。

## 输入与输出

输入：图片/区域图。输出：ocr-candidate。规则：不覆盖原生文本；记录 Apple Vision 引擎、语言、版本、fallback 原因和置信度。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`ocr-candidate` 必须包含输入资产/区域 ID、引擎和版本、语言、候选文本、置信度、边界框、fallback 原因、`status` 和诊断。OCR 只能作为候选，不能覆盖 `native-content-model`。
