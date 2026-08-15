---
name: source-inventory
title: "来源盘点"
description: 盘点来源文件的类型、页数、尺寸、文字层、图片层、损坏状态与可解析性。
order: 10
---

# 来源盘点

## 职责

盘点来源文件的类型、页数、尺寸、文字层、图片层、损坏状态与可解析性。

## 输入与输出

输入：来源文件。输出：source-inventory。规则：只读；不修改来源；不可识别时返回 source-invalid。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`source-inventory.json` 必须包含 `source_fingerprint`、`media_type`、`status`、`page_count_or_dimensions`、`text_layer`、`image_layer`、`readability` 和 `diagnostics[]`。`source_fingerprint` 在后续 A2–A7 中只读传递；缺页数/尺寸时写 `Unknown`，不能写 0 伪造成功。

字段与状态以 `contracts/source-inventory.yaml` 为准；Workflow A 的 A1 stage evidence 还必须引用实际生成的该文件。

## 验证与失败

验证来源未被修改、指纹稳定、未知类型进入 `source-invalid`，损坏输入包含可操作诊断。推荐命令：`python3 tests/validate_workflow_package.py`；真实运行还必须保存 A1 evidence 和输入摘要。
