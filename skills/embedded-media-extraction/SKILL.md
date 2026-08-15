---
name: embedded-media-extraction
title: "内嵌媒体提取"
description: 提取来源文档内嵌图片、媒体关系和原始格式。
order: 10
---

# 内嵌媒体提取

## 职责

提取来源文档内嵌图片、媒体关系和原始格式。

## 输入与输出

输入：native-content-model/source document。输出：source-visual-asset。规则：不重新压缩；保存来源关系和哈希。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每个嵌入媒体必须保留原始关系 ID、父页面/节点、原始 MIME、尺寸、SHA-256、`source_occurrences[]`、`status` 和诊断。无法读取或重新编码时必须 `unavailable`/`needs-review`，不能静默丢媒体。
