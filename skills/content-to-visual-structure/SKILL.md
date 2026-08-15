---
name: content-to-visual-structure
title: "内容到视觉结构"
description: 将文字事实和关系映射为节点、层级、方向、分组和视觉重点。
order: 10
---

# 内容到视觉结构

## 职责

将文字事实和关系映射为节点、层级、方向、分组和视觉重点。

## 输入与输出

输入：selected-knowledge-unit、visual-intent。输出：visual-structure-plan。规则：不添加 Markdown 中不存在的事实。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
