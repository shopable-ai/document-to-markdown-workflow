---
name: visual-reconstruction-profile
title: "视觉重建信息提取"
description: 从复杂视觉资产中提取可核对的结构、文字、样式和图像模型重建提示词。
order: 10
---

# 视觉重建信息提取

## 职责

从页面图、嵌入媒体或复杂区域中提取后续图像模型重建所需的信息。输出结构版与提示词版
的独立 profile；不生成图片，不覆盖原始资产，也不把泛化图像描述当成来源事实。

## 输入与输出

输入：原始/页面/区域视觉资产、原生结构、OCR/视觉复核候选和来源定位。输出：符合
`contracts/visual-reconstruction-profile.yaml` 的 `visual-reconstruction-profile`。

结构版至少覆盖画布、区域、元素、层级、关系、文字保真和样式；提示词版必须明确事实、
构图、文字、风格、画布、禁用项和版本。每个关键字段都要带资产、区域或文字 span 证据。

## 规则

- 先保留原图，再做理解；profile 只能引用或整理当前资产中可见、可回溯的内容。
- 复杂结构优先保存结构化字段；不能确认的节点、关系、文字和风格写 `Unknown` 或进入
  `uncertainty`，不得用模型常识补齐。
- 提示词是结构证据的下游表达，不是独立事实层；提示词缺证据时状态最多为 `needs-review`。
- `structure-only`、`prompt-only` 和 `insufficient-evidence` 必须如实标记，不能伪装成可直接
  生成的完整 profile。
- 只保存 provider/model 的非秘密摘要、schema/prompt 版本和输入哈希；禁止写入密钥、Cookie、
  Authorization 或订阅 URL。

## 边界

这是可复用能力 Skill，不负责决定 Workflow 顺序、外部模型授权、生成图片或最终图片验收；
这些由所属 Workflow、契约和独立验收器负责。

## 失败状态

证据不足返回 `needs-review` 或 `insufficient-evidence`；纯装饰且没有可复用语义返回
`not-applicable`；工具失败返回可诊断的 `unavailable`，同时保留原始资产。
