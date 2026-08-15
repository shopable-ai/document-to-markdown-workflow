---
name: generated-image-review
title: "生成图片复核"
description: 复核生成图片的文字可读性、结构完整性、层级、事实忠实度和单图约束。
order: 10
---

# 生成图片复核

## 职责

复核生成图片的文字可读性、结构完整性、层级、事实忠实度和单图约束。

## 输入与输出

输入：图片、selected unit、visual plan。输出：visual-review-report。规则：失败返回 needs-revision/review-failed，不伪造通过。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
