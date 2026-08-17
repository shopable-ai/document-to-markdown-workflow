---
name: visual-prompt-builder
title: "视觉提示词构建"
description: 将内容、结构、画布、文字保真、风格、参考图和禁用项组装为单图提示词。
order: 10
---

# 视觉提示词构建

## 职责

将内容、结构、画布、文字保真、风格、参考图和禁用项组装为单图提示词。

## 输入与输出

输入：selected unit、visual plan、references，以及可选的 visual-reconstruction-profile。输出：generation-request。
规则：优先从 profile 的结构字段组装复杂图片提示词，保留 profile_id、prompt/schema 版本和来源
字段；不把整份 Markdown 原样塞入提示词，不把 profile 中的 Unknown/候选改写成 confirmed，不写入秘密。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
