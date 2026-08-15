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

输入：`document.md`、source-visual-asset manifest。输出：带链接的最终 `document.md`。规则：链接目标必须存在；不把裸文件名当唯一身份；不能只输出链接检查日志。

链接插入位置遵循 Markdown 读者顺序：先完成标题、文字、关系说明、来源/不确定性说明，再插入“【需要保留的】原始视觉资产”小节。不得把页面图或复杂区域图置于正文文字之前。若资产只用于证据核对，仍需保留链接，但必须写明其用途和来源页/区域。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每条图片链接必须同时能解析到 manifest 的 `asset_id` 和实际相对路径，并保留来源 occurrence；目标不存在、路径越界或 manifest 缺项进入 `integrity-check-failed`，不能只报告链接字符串。
