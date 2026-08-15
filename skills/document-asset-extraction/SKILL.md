---
name: document-asset-extraction
title: "文档资产提取"
description: 将 PPTX、PDF、DOCX 或图片中的原生文字、版面关系和视觉来源资产提取为可追溯的 Markdown 资产包；当需要保留复杂图形、图片和来源定位，而不是只做扁平 OCR 时使用。
order: 10
---

# 文档资产提取

## 概览

将来源文档转换为“Markdown 语义层 + 来源视觉资产层 + manifest 追溯层”。原生解析优先，OCR 只用于缺失或冲突复核；复杂结构必须保留页面图或局部图，不能用 OCR 文本替代视觉证据。

## 输入与边界

- 支持：PPTX、PDF、DOCX、PNG/JPEG 等图片。
- 输出：Markdown、来源页面图、嵌入图片、复杂区域图、资产 manifest、处理报告。
- 最终交付目录：由 Workflow 的 `output_root` 指定，至少包含 `document.md`、`manifest.yaml`、`reports/processing-report.yaml`、`reports/acceptance-report.yaml`、`reports/failure-report.yaml` 和 `assets/`；阶段证据不得替代这些文件。
- 不负责：调用 GPT-image-2、自动把来源图升级为通用模板、生成 PPTX/PDF。
- 原则：不得覆盖来源文件；每个结论都能回到页码、节点或图像资产。

## 执行链路

1. `source-inventory`：识别文件类型、页数、尺寸、文字层、图片层和可解析性。
2. 原生提取：
   - PPTX 使用 XML 提取文字、形状、分组、连线、坐标和媒体引用。
   - PDF 使用文本块、字体、边界框、阅读顺序和页面渲染。
   - DOCX 使用 OOXML 段落、表格、图片和关系。
3. `embedded-media-extraction`：复制嵌入媒体并建立来源关系。
4. `page-rendering` 与 `complex-region-detection`：保留整页图；对流程图、矩阵、组合图、图文混排区域生成局部图。
5. `local-ocr`：仅对无文字层或原生提取冲突的区域做 OCR；记录引擎、语言和置信度。
6. `text-source-reconciliation`：以原生文本为主，OCR 作为候选；冲突不得静默覆盖。
7. `markdown-structure-builder`：按页面/章节构造 Markdown，保留列表、表格、关系描述和资产链接。
8. `semantic-asset-naming` 与 `source-occurrence-registration`：生成可读文件名，并用 manifest 中的稳定 ID、哈希和来源位置处理跨文件重名。
9. 验收：运行链接、完整性、可追溯性和不确定性检查。

## 决策规则

- 有可用原生结构时，不先 OCR。
- 结构关系无法由文字可靠表达时，必须保留视觉资产并在 Markdown 中链接。
- 原生结构提取之后，mac96 视觉大模型是默认视觉复核路径，应通过 SSH 记录实际尝试；不可用时才降级到本机 Apple Vision OCR，仍无法确认必须标记 `needs-review`。
- 不确定内容使用 `Unknown` 或 `needs-review`，不能伪装成确定事实。

## 最小验收

- Markdown 可读、可检索，且每个页面/区域的视觉链接存在。
- 原生文字、OCR 候选和冲突状态可区分。
- 复杂图没有被扁平化丢失；来源位置可回溯。
- 落盘文件名使用 A6 规定的稳定英文 `file_name`；中文阅读语义放在 `display_name`；稳定 ID 只用于 manifest 和程序关联。
- 失败时输出失败态和可重试边界，而不是返回空成功。
- 测试目录中的日志、断言和测试报告只能证明测试状态，不属于本 Skill 的业务结果。

## 相关契约

读取包内 `contracts/markdown-asset-package.yaml`、`contracts/source-visual-asset.yaml` 和 `references/naming-policy.zh.md`。它们定义输出字段、来源关系、命名与去重规则；不要在本 Skill 中复制一套冲突 schema。
