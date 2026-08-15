---
name: asset-deduplication
title: "资产去重"
description: 基于来源指纹、出现 ID 和内容哈希处理资产去重与合并。
order: 10
---

# 资产去重

## 职责

基于来源指纹、出现 ID 和内容哈希处理资产去重与合并。

## 输入与输出

输入：asset records。输出：dedup report/manifest。规则：不按裸文件名去重；疑似相同保留候选。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 验证与失败

去重必须以来源指纹、occurrence 和内容哈希联合判断；只有裸文件名相同只能标记候选。合并后必须保留全部 `source_occurrences[]`，冲突进入 `needs-review`，不得减少来源事实。
