---
name: page-rendering
title: "页面渲染"
description: 把来源文档页面渲染为可复核的整页视觉资产。
order: 10
---

# 页面渲染

## 职责

把来源文档页面渲染为可复核的整页视觉资产。

## 输入与输出

输入：来源文档。输出：page-render source-visual-asset。规则：记录渲染器、尺寸、页码；失败必须可诊断。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

每个页面资产必须登记 `asset_id`、`source_fingerprint`、原始页码、渲染器/版本、`mime_type`、`width`、`height`、`sha256`、`status` 和 `diagnostics[]`。渲染失败不得生成空 PNG 或 `available` 记录。

图级生命周期复用 legacy 页图时还要登记 `source_equivalence`：原路径/哈希、工程 page-render
路径/哈希、复制或转换方法和等价状态。只有双方 SHA-256 相等才能写 `byte-identical`；字节不同
必须保存渲染/转换工具与参数，不能只用“已复制”作为来源证明。

文字层未命中或页映射不确定时，仅对候选页做定点探查。探查 evidence 保存触发原因、候选页、
页面图、明确页序、contact sheet 路径/SHA-256、发现和选择结论。contact sheet 只用于工程复核，
不替代选中的 page-render，也不投影到读者 `assets/`。contact sheet/thumbnail 只能用于选页；如果
在预览图标注候选区域，必须记录 preview/full-resolution 尺寸和 scale/rotation transform，并回到
带哈希的全分辨率 parent 重新定位和逐边复核，不能直接复用预览坐标执行 crop。
