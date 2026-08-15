---
name: visual-use-case-classifier
title: "视觉用途分类"
description: 判断内容适合流程、因果、对比、矩阵、时间线或概念地图等主视觉结构。
order: 10
---

# 视觉用途分类

## 职责

判断内容适合流程、因果、对比、矩阵、时间线或概念地图等主视觉结构。

## 输入与输出

输入：selected-knowledge-unit、可选用户意图。输出：visual-intent。规则：只能选一个主结构；不确定返回 visual-intent-ambiguous。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
