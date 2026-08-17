---
name: source-visual-reference-selector
title: "来源视觉参考选择"
description: 从可选来源资产中挑选能帮助结构或风格的参考图，并说明用途。
order: 10
---

# 来源视觉参考选择

## 职责

从可选来源资产中挑选能帮助结构或风格的参考图，并说明用途。

## 输入与输出

输入：reference_assets、visual-structure-plan，以及可选的 visual-reconstruction-profile。输出：selected-references。
规则：参考图和 profile 是证据或结构输入，不自动成为复制模板；选择 profile 时必须保留
profile_id、来源资产、用途、版本和未决项。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
