---
name: uncertainty-marking
title: "不确定性标记"
description: 将 unknown、候选、冲突、待复核状态显式写入 Markdown 和 manifest。
order: 10
---

# 不确定性标记

## 职责

将 unknown、候选、冲突、待复核状态显式写入 Markdown 和 manifest。

## 输入与输出

输入：提取/复核结果。输出：标注后的语义层。规则：不确定性不可静默删除。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
