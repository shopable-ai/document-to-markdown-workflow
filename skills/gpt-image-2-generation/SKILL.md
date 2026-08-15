---
name: gpt-image-2-generation
title: "GPT-image-2 生成"
description: 调用 GPT-image-2 生成恰好一张 raster 图片，并记录无秘密的请求结果。
order: 10
---

# GPT-image-2 生成

## 职责

调用 GPT-image-2 生成恰好一张 raster 图片，并记录无秘密的请求结果。

## 输入与输出

输入：gpt-image-generation-request。输出：single-raster-image。规则：供应商失败可重试；不生成 PPTX/PDF/SVG/批量文件。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
