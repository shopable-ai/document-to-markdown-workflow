---
name: pptx-xml-extraction
title: "PPTX XML 提取"
description: 从 PPTX OOXML 提取文字片段、形状、分组、连线、坐标、层级与媒体关系。
order: 10
---

# PPTX XML 提取

## 职责

从 PPTX OOXML 提取文字片段、形状、分组、连线、坐标、层级与媒体关系。

## 输入与输出

输入：PPTX。输出：native-content-model。规则：保留 slide/object 级来源定位；未知字段标记 unknown；不以 OCR 替代 XML。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`native-content-model.json` 必须包含 `source_fingerprint`、`slides[]`、`text_blocks[]`、`nodes[]`、`relations[]`、`coordinates[]`、`source_locations[]`、`status` 和 `diagnostics[]`。每个文本/节点/关系都必须带 `slide`、原生对象 ID 或 `Unknown`；不能只返回扁平全文。

跨 PPTX/PDF/DOCX 的共同字段和失败边界以 `contracts/native-content-model.yaml` 为准；A2 stage evidence 不得只引用计划中的解析器。

## 验证与失败

验证 XML 读取、关系/连线保留、坐标单位和媒体关系；解析异常进入 `extract-timeout`、`contract-invalid` 或 `source-invalid`，不回退为 OCR 成功。
