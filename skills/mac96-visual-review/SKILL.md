---
name: mac96-visual-review
title: "mac96 视觉复核"
description: 默认通过 SSH 调用局域网 mac96 视觉模型，对复杂区域执行视觉结构复核；不可用时交给本机 OCR 兜底。
order: 10
---

# mac96 视觉复核

## 职责

在 Workflow A 的 A4 阶段，默认通过 SSH 调用局域网 `mac96` 上的视觉模型，对页面和复杂区域执行结构复核。它不是静默可选步骤：必须记录连接、模型、输入、输出和失败状态；只有 mac96 不可用或复核失败时，才进入 Apple Vision OCR 兜底。

## 输入与输出

输入：页面/区域图、原生内容模型和待核对问题。输出：visual-review-candidate。规则：优先使用 SSH/mac96；连接失败、服务不可达、模型无此能力或输出不可解析时，返回可诊断失败状态并触发 fallback，不得伪造视觉复核通过。视觉提示必须要求开放集合扫描，并返回 `unknown_forms`、`coverage_notes` 和每个候选的区域/证据；只返回预设关系不能证明覆盖完整。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 输出必须包含 `transport=ssh`、目标 `mac96`、模型标识、请求时间、输入资产和结构化结果摘要。
- mac96 成功时记录 `visual_review=complete` 或 `partial`；失败时记录 `unavailable`，并由 Workflow A 进入 Apple Vision OCR。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。
