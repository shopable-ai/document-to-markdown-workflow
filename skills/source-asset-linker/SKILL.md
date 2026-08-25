---
name: source-asset-linker
title: "来源资产链接"
description: 在 Markdown 中建立稳定的视觉资产链接与来源说明。
order: 10
---

# 来源资产链接

## 职责

在 Markdown 中建立稳定的视觉资产链接与来源说明。

## 输入与输出

输入：`index.md`、source-visual-asset manifest。输出：带链接的最终 `index.md`。规则：链接目标必须存在；不把裸文件名当唯一身份；不能只输出链接检查日志。

链接插入位置遵循 Markdown 读者顺序：先完成标题、文字和已确认关系说明，再插入可选 Mermaid、
PNG fallback/必要视觉资产。不得把页面图或复杂区域图置于正文文字之前。可靠 Mermaid 的 fallback
必须解析到同一 manifest 中的 crop 或 enhanced-raster；不可靠时只链接 PNG。若资产只用于工程
证据核对，不把工程状态写入读者正文，但其来源页/区域、哈希和 evidence-only binding 仍保留。

回填前必须先在同一 Manifest 中明确 reader asset selection：哪个 crop/enhanced 是 primary raster、
哪个资产是 PNG fallback、哪些 source page 只作为 evidence-only。回填后记录当前最小内容单元的
前后 SHA-256、插入的 asset/representation ID 和每条相对链接的解析结果；再由确定性合并/读者投影
重读一次。不得把“Markdown 中出现了链接字符串”当成链接和 fallback 已闭合。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每条图片链接必须同时能解析到 manifest 的 `asset_id` 和实际相对路径，并保留来源 occurrence；
Mermaid 还必须解析到 derived representation、Markdown target 和 `fallback_asset_id`。目标不存在、
路径越界或 manifest 缺项进入 `integrity-check-failed`，不能只报告链接字符串。Markdown 引用缺失
不得触发来源资产删除，必须先定位投影、写出或合并环节。完成证据还必须包含 content unit 的
before/after SHA-256、link checks 和 merge/projection check。
