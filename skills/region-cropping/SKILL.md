---
name: region-cropping
title: "区域裁剪"
description: 按已确认或候选区域裁剪局部来源图。
order: 10
---

# 区域裁剪

## 职责

按已确认或候选区域裁剪局部来源图。

## 输入与输出

输入：页面图、区域坐标。输出：complex-region source-visual-asset。规则：保留父页面关系、坐标和裁剪参数。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

裁剪资产必须保留 `parent_asset_id`、来源页/区域、裁剪坐标和参数、MIME、尺寸、SHA-256、`status` 和诊断；坐标越界、空裁剪或父资产缺失进入 `integrity-check-failed`。
