---
name: text-source-reconciliation
title: "文字来源复核"
description: 合并原生文字与 OCR 候选，保持来源、差异、置信度和未决项。
order: 10
---

# 文字来源复核

## 职责

合并原生文字与 OCR 候选，保持来源、差异、置信度和未决项。

## 输入与输出

输入：native-content-model、ocr-candidate。输出：reconciled-content-model。规则：原生结果优先；冲突进入 needs-review。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每个 reconciled item 必须引用 native/OCR/视觉输入 ID、决策（native、candidate、unresolved）、理由、置信度和 `needs-review` 标记；`conflicts[]` 和 `unresolved[]` 不得为空数组伪装为已检查。
