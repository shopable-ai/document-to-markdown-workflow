---
name: visual-reconstruction-profile
title: "视觉重建信息提取"
description: 从复杂视觉资产中提取可核对的结构、文字、样式和图像模型重建提示词。
order: 10
---

# 视觉重建信息提取

## 职责

从页面图、嵌入媒体或复杂区域中提取后续图像模型或可选 Mermaid 重建所需的信息。输出结构版、
提示词版和可选派生表达；不覆盖原始资产，也不把泛化图像描述当成来源事实。

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
- Mermaid 只用于结构清晰的流程、推理链、层级、时间线或明确节点—边关系。每个节点、标签和边
  都必须回指当前资产 evidence；影响结构的 `uncertainty` 非空时不得写入读者 Markdown。
- 可用 Mermaid 必须记录 `.mmd`/profile 路径、状态、置信度、Markdown targets 和 PNG
  `fallback_asset_id`。fallback 必须是同一 Manifest 中可用的 crop 或 enhanced-raster。
- Mermaid 状态为 `available` 前必须依次完成：语法解析、实际渲染、来源语义逐项对照和 fallback
  链接解析。来源语义对照分别检查节点、标签、边、方向和层级；语法能解析不代表结构正确。
- 邻近正文只允许在保存精确 source span 且与图中可见证据一致时，用于标签拼写或消歧；不得据正文
  新增图中未确认的节点、边、方向、层级或文字。正文、OCR/视觉候选与图片冲突时保存双方证据和
  `text_corroboration.conflicts`，相关关系及 Mermaid 保持 `needs-review`。
- 任一验证未运行、失败或仍需复核时，derived representation 状态最多为 `needs-review`，不得写入
  读者 Markdown；PNG fallback 和来源资产继续保留。
- 文字太小或噪声过重时可以从现有来源/crop 派生放大图、节点子图或 OCR probe，但每个诊断文件
  必须记录稳定 ID、父资产、目的、路径、SHA-256、保留状态和 `reader_projection=evidence-only`。
  诊断子裁切还必须保存方法、参数和相对父资产的坐标变换。诊断识别只作候选，不得据此补猜
  节点、箭头、方向、层级或文字，也不得复制到读者 `assets/`。
- `structure-only`、`prompt-only` 和 `insufficient-evidence` 必须如实标记，不能伪装成可直接
  生成的完整 profile。
- 只保存 provider/model 的非秘密摘要、schema/prompt 版本和输入哈希；禁止写入密钥、Cookie、
  Authorization 或订阅 URL。

## 边界

这是可复用能力 Skill，不负责决定 Workflow 顺序、外部模型授权、生成图片或最终图片验收；
这些由所属 Workflow、契约和独立验收器负责。

## 失败状态

证据不足返回 `needs-review` 或 `insufficient-evidence`；Mermaid 不可靠时不强行生成，只保留
PNG fallback 和必要诊断证据；纯装饰且没有可复用语义返回
`not-applicable`；工具失败返回可诊断的 `unavailable`，同时保留原始资产。
