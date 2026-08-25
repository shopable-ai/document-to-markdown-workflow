---
name: complex-region-detection
title: "复杂区域识别"
description: 识别流程图、矩阵、组合图、图文混排等不能由纯文本可靠表达的区域。
order: 10
---

# 复杂区域识别

## 职责

识别流程图、矩阵、组合图、图文混排等不能由纯文本可靠表达的区域。

## 输入与输出

输入：页面图、原生结构、当前最小内容单元中的图号/图注/邻近正文和现有 Manifest。输出：候选区域与开放集合覆盖记录。规则：宁可标记候选和 needs-review，不静默丢弃结构；除了流程图、矩阵、组合图和图文混排，还要登记无法归入预设类型的嵌套容器、颜色/线型编码、图例、跨区连线、符号和跨页关系。

## 候选区域产生顺序

1. 用当前内容单元中的图号、图注、正文引用、原生对象/bbox 和可见图形结构建立定位锚点。
2. 明确列出必须保留的节点、文字、箭头、括号、边注、图例、结构空白和必要上下文。
3. 明确列出允许排除的页眉、页码、无关正文；只有图注已由 Markdown 独立保存时才允许排除图注。
4. 生成保守候选 geometry；候选只供 `region-cropping` 消费，不直接等于已接受 crop。
5. 同一 page-render 含多个图时复用 page parent，但为每图分配不同 `figure_id`、`region_id`、
   `source_occurrence_id`、ordinal 和 candidate geometry；不得按页码或图像哈希合并。
6. 候选依据冲突、来源锚点不足或无法判断边界时，保留整页图并返回 `needs-review`。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

候选区域必须带 `figure_id`、`region_id`、`source_occurrence_id`、ordinal、父页面资产 ID、candidate
geometry、定位锚点、触发依据、必须保留元素、允许排除元素、覆盖对象 ID、`confidence`、`status`
和诊断。没有检测到候选也要记录 `coverage.status=checked`，不得把未执行写成空数组成功。该 Skill
不得把候选区域标成最终 crop；最终接受状态由 `region-cropping` 的四边复核产生。
