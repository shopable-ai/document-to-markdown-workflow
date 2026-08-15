---
name: page-rendering
title: "页面渲染"
description: 把来源文档页面渲染为可复核的整页视觉资产。
order: 10
---

# 页面渲染

## 职责

把来源文档页面渲染为可复核的整页视觉资产。

## 输入与输出

输入：来源文档。输出：page-render source-visual-asset。规则：记录渲染器、尺寸、页码；失败必须可诊断。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每个页面资产必须登记 `asset_id`、`source_fingerprint`、原始页码、渲染器/版本、`mime_type`、`width`、`height`、`sha256`、`status` 和 `diagnostics[]`。渲染失败不得生成空 PNG 或 `available` 记录。
