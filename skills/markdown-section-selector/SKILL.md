---
name: markdown-section-selector
title: "Markdown 章节选择"
description: 从 Markdown 中选择一个自洽、边界明确的知识单元。
order: 10
---

# Markdown 章节选择

## 职责

从 Markdown 中选择一个自洽、边界明确的知识单元。

## 输入与输出

输入：Markdown、heading/anchor/line-range/semantic-query。输出：selected-knowledge-unit。规则：保留必要限定；找不到则 markdown-section-missing。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
