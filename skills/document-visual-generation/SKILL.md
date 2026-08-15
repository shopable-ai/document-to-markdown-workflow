---
name: document-visual-generation
title: "文档视觉生成"
description: 从已有 Markdown 中选择局部内容，规划信息结构并调用 GPT-image-2 生成一张可读、可记忆的视觉图片；当目标是单张图片而不是重新制作 PPTX/PDF 时使用。
order: 10
---

# 文档视觉生成

## 概览

把 Markdown 的一个局部知识单元编译成一次单图生成请求。先判断视觉用途、信息密度和参考资产，再生成提示词；结果只接受一张图片和可追溯请求记录，不把模型输出误称为可编辑幻灯片。

## 输入与边界

- 必需输入：Markdown 片段、目标受众、使用场景和期望画布比例。
- 可选输入：Workflow A 的来源视觉资产、品牌色、禁用元素和参考风格。
- 输出：单张 GPT-image-2 图片、请求 manifest、人工/自动复核结果。
- 不负责：重新解析 PPT/PDF、批量生成多文件、生成 PPTX/PDF/SVG、把来源图片直接当作可复用模板。

## 执行链路

1. `markdown-section-selector`：选择一个自洽知识单元，保留标题、核心事实、关系和必要限定。
2. `visual-use-case-classifier`：在流程图、因果图、对比图、矩阵、时间线、概念地图等用途间选择一种主结构。
3. `content-to-visual-structure`：把文字映射为节点、层级、方向、分组和视觉重点。
4. `density-gate`：限制单图信息量；过密时要求拆分或缩减，不让模型用小字堆满画布。
5. `source-visual-reference-selector`：只选择能帮助结构或风格的参考资产，记录其来源和用途。
6. `visual-prompt-builder`：生成中文语义提示词，明确画布、层级、文字保真、风格、禁用项和参考图约束。
7. `gpt-image-2-generation`：调用在线图像模型；密钥、请求头和供应商细节不得写入产物。
8. `generated-image-review`：检查文字可读性、结构完整性、视觉层级、事实忠实度和单图约束。

## 决策规则

- 生成图片前先完成结构规划；不能把原始 Markdown 原样塞进提示词。
- 参考资产是证据/风格输入，不是未经授权的复制模板。
- 文字很多、关系复杂或事实风险高时，优先减少文字并突出关系；必要时返回 `needs-split`。
- 生成失败、模型不可用或文字不可读时，返回可诊断失败态，不伪造成功。

## 最小验收

- 结果恰好为一张图片，且尺寸/比例符合请求。
- 图中核心文字可读，结构与 Markdown 事实一致。
- 参考资产来源、用途和授权边界有记录。
- 请求 manifest 不含秘密，并可复现输入片段与提示词版本。
- 失败时输出失败态和可重试边界，而不是返回空成功。

## 相关契约

读取包内 `contracts/gpt-image-generation-request.yaml` 和 `references/failure-states.zh.md`。请求 manifest 必须保留来源片段、参考资产、模型配置摘要和复核状态，但不能写入秘密。
