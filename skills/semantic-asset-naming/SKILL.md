---
name: semantic-asset-naming
title: "语义资产命名"
description: 根据来源、位置、内容语义和用途生成稳定英文文件名与中文显示名。
order: 10
---

# 语义资产命名

## 职责

根据来源、位置、内容语义和用途生成稳定英文文件名与中文显示名。

## 输入与输出

输入：结构化资产元数据、来源英文别名、位置、语义标签和用途。输出：`file_name`、`display_name`、`naming` 和命名状态。规则：`file_name` 使用
`<source-slug>__p<page-3-digits>__<asset-kind>__<semantic-slug>__<purpose-slug>__o<ordinal>.<ext>`；只允许 ASCII 小写英文和数字；禁止随机哈希前缀、中文、空格和直接清洗原始文件名；冲突通过结构化字段和 ordinal 解决，哈希只进入 manifest。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 必须同时输出机器字段 `file_name` 与读者字段 `display_name`，不能用一个字段兼任两种用途。
- `naming.source_slug`、`naming.semantic_slug` 无法可靠确认时返回 `needs-review`/`unknown`，不得臆译或回退到 `original_filename`。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 验证与失败

命名生成后必须按 `references/naming-policy.zh.md` 的正则、ASCII、稳定字段和 ordinal 做校验；同一输入重跑必须得到相同 `file_name`。语义不确定时输出 `needs-review`，不以 `original_filename` 回退。
