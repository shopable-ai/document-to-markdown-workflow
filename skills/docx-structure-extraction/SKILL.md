---
name: docx-structure-extraction
title: "DOCX 结构提取"
description: 从 DOCX OOXML 提取段落、标题、表格、图片和关系。
order: 10
---

# DOCX 结构提取

## 职责

从 DOCX OOXML 提取段落、标题、表格、图片和关系。

## 输入与输出

输入：DOCX。输出：native-content-model。规则：保留段落/表格顺序和关系来源。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

必须保留 `source_fingerprint`、段落/表格/图片关系、样式或层级定位、`status` 和 `diagnostics[]`。OOXML 缺失或不一致时写 `Unknown`/`needs-review`，不能把纯文本导出当作完整结构。
