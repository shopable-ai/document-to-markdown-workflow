---
name: density-gate
title: "信息密度门禁"
description: 评估单图信息密度、文字长度、节点数量和可读性风险。
order: 10
---

# 信息密度门禁

## 职责

评估单图信息密度、文字长度、节点数量和可读性风险。

## 输入与输出

输入：visual-structure-plan。输出：accepted/reduced/needs-split。规则：过密不得强行调用图像模型。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
