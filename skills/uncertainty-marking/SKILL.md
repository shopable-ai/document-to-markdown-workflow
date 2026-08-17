---
name: uncertainty-marking
title: "不确定性标记"
description: 将 unknown、候选、冲突和待复核状态显式写入工程证据、manifest 与人工审核记录，不污染读者 Markdown。
order: 10
---

# 不确定性标记

## 职责

将 unknown、候选、冲突、待复核状态显式写入工程层；读者 Markdown 只保留已确认的语义内容和必要图片链接。

## 输入与输出

输入：提取/复核结果。输出：`evidence/`、`manifest.yaml` 与可选 `reviews/markdown-content-repair.yaml`。规则：不确定性不可静默删除，也不得写成 `index.md` 的候选标题、OCR 注释、页码或“待复核”旁注。

## 分层规则

- 把来源页、source occurrence、置信度、OCR 原值/候选、冲突理由和阶段状态写入 `evidence/` 或 `manifest.yaml`。
- 把无法直接确认、需要保留追溯的读者层文字和结构修复写入 `reviews/markdown-content-repair.yaml`；状态为 `applied`、`rejected` 或 `deferred`。图文关系和页面图筛选由对应的视觉复核与资产筛选流程记录。
- `index.md` 禁止出现 `Unknown`、`needs-review`、`待复核`、`原文疑似`、`OCR` 解释、`第 x 页`、`来源页`、`A4 状态` 和 `来源与原图`等工程文字。
- `markdown-content-repair` 对确定的文字结构问题可以直接修改正文，并记录 `applied` 差异；页面图链接的移除必须由视觉资产筛选流程明确批准，被移除的页面图仍保留在 `assets/` 与 `manifest.yaml` 中。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
