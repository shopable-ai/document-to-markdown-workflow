---
name: pdf-layout-extraction
title: "PDF 版面提取"
description: 从 PDF 提取文本块、字体、边界框、阅读顺序、图片引用并保留页面关系。
order: 10
---

# PDF 版面提取

## 职责

从 PDF 提取文本块、字体、边界框、阅读顺序、图片引用并保留页面关系。

## 输入与输出

输入：PDF。输出：native-content-model。规则：文本顺序不确定时保留坐标和 needs-review。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

除 `source_fingerprint`、`status` 和 `diagnostics[]` 外，必须保存 `pages[]`、`text_blocks[]`、`bbox`、`reading_order`、`image_refs[]` 和 `source_locations[]`。阅读顺序不确定时 `reading_order.status=needs-review`，不得按字符串拼接掩盖坐标。
