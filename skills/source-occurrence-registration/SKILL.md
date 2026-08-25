---
name: source-occurrence-registration
title: "来源出现登记"
description: 登记同一资产在来源中的每次出现、页码、节点、区域和父资产。
order: 10
---

# 来源出现登记

## 职责

登记同一资产在来源中的每次出现、页码、节点、区域和父资产。

## 输入与输出

输入：资产与来源定位。输出：`source_occurrences[]` record。规则：出现记录与文件名解耦，文件名变更不能改变来源出现 ID。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 每次出现至少登记 `source_occurrence_id`、`source_file`、`page`/`region`、`object_ids`、`parent_asset_id`、`ordinal` 和状态。
- 同一内容在不同页面出现时保留多条 occurrence；同一 occurrence 的重命名、搬迁或去重不得新造来源事实。
- 同一页面含多个图时允许它们共享 page-render parent，但每个图必须有不同 figure/region/occurrence
  ID 和稳定 ordinal；页面相同、哈希相同或图形相似都不能把多个来源出现合并为一个。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 验证与失败

`source_occurrence_id` 必须由来源指纹、定位和稳定 ordinal 派生且与文件名解耦；同一出现重命名不得新造 occurrence。缺页码/区域/对象定位进入 `needs-review` 或 `unknown`，不能伪造 page 0。
