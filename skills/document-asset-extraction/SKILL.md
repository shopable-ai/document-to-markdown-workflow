---
name: document-asset-extraction
title: "文档资产提取"
description: 将 PPTX、PDF、DOCX 或图片中的原生文字、版面关系和视觉来源资产提取为可追溯的 Markdown 资产包；当需要保留复杂图形、图片和来源定位，而不是只做扁平 OCR 时使用。
order: 10
---

# 文档资产提取

## 概览

将来源文档转换为“Markdown 语义层 + 来源视觉资产层 + manifest 追溯层”。原生解析优先，OCR 与明确授权的在线视觉逐字转写只用于缺失或冲突复核；复杂结构必须保留页面图或局部图，不能用候选文字替代视觉证据。

## 输入与边界

- 支持：PPTX、PDF、DOCX、PNG/JPEG 等图片。
- 输出：Markdown、来源页面图、嵌入图片、复杂区域图、资产 manifest、处理报告；需要读者层内容修复时另输出 `reviews/markdown-content-repair.yaml`。
- 最终交付目录：由 Workflow 的 `output_root` 指定，至少包含 `index.md`、`manifest.yaml`、`reports/processing-report.yaml`、`reports/acceptance-report.yaml`、`reports/failure-report.yaml` 和 `assets/`；阶段证据不得替代这些文件。
- 不负责：调用 GPT-image-2、自动把来源图升级为通用模板、生成 PPTX/PDF。
- 原则：不得覆盖来源文件；每个结论都能回到页码、节点或图像资产。
- 通用入口：由 `adapters/workflow_a_source_runner.py` 根据 source、profile/config 和页码选择路由；默认是 `--plan-only`，不读取真实 source。
- 兼容入口：`archive/legacy-adapters/workflow_a_runner.py` 仅用于旧证据/旧 profile 兼容，不得作为新来源的默认入口。

## 执行链路

1. `workflow_a_source_runner`：解析来源类型、profile、页码范围和执行边界；source fingerprint 与 preflight 属于后续 execute 阶段。
2. `source-inventory`：识别文件类型、页数、尺寸、文字层、图片层和可解析性。
3. 原生提取：
   - PPTX 使用 XML 提取文字、形状、分组、连线、坐标和媒体引用。
   - PDF 使用文本块、字体、边界框、阅读顺序和页面渲染。
   - DOCX 使用 OOXML 段落、表格、图片和关系。
4. `embedded-media-extraction`：按 PPT/PPTX、PDF、独立图片分别复制嵌入/原图并建立来源关系。
5. `page-rendering` 与 `complex-region-detection`：保留整页图；对流程图、矩阵、组合图、图文混排区域按需生成局部图。
6. `local-ocr`：仅对无文字层或原生提取冲突的区域做 OCR；记录引擎、语言和置信度。
7. `text-source-reconciliation`：以原生文本为主，OCR/视觉结果仅为候选；冲突不得静默覆盖。
8. 独立 Markdown 内容修复：以当前 Markdown 和显式提供的提取文字候选生成 raw/draft/diff；只能做来源支持的局部结构修复，不能把 OCR 或视觉候选直接当成优化完成。
9. `markdown-structure-builder`：按当前 source 动态结构构造读者 Markdown，不使用固定标题或旧术语模板；OCR 候选和工程状态不得进入正文。
10. `semantic-asset-naming` 与 `source-occurrence-registration`：生成可读文件名，并用 manifest 中的稳定 ID、哈希和来源位置处理跨文件重名。
11. 验收：Markdown 组装后才运行独立 validator 和质量审计。

## 来源页面转换为 Markdown 的正文格式

本节适用于将 PPTX、PDF、DOCX 或独立图片的单页/内容单元转换为
`index.md`；它描述的是“来源文档 → Markdown”的页面输出，不是把多个已经完成的
Markdown 文件合并成一篇新文档。

每个页面或内容单元按实际存在的内容输出，默认顺序如下：

```text
## 标题（确认存在时）
→ 可选的 `> 关系：...` 特殊文字关系说明
→ 从来源提取的文字正文、列表、表格或分区
→ 必要的原始页面图、复杂区域图或嵌入图片
```

其中：

- 标题只使用当前来源中可确认的语义标题，排除系列名、页码、幻灯片编号和内部定位；没有可靠标题时省略标题或使用同源正文交叉佐证的中性标题，不能使用“第 x 页”占位。
- 当复杂图片包含递进、包含、因果、对比、时间线、树状或矩阵等已确认文字关系时，可在正文前增加一条简短的 `> 关系：...` 说明；普通正文和工程诊断不使用引用块。关系不确定时保留工程候选和视觉证据，不强行下结论。
- 正文应忠实转写来源文字。允许修正明显错字、统一标点、拆分段落、列表、表格和标题层级，但不能擅自总结、扩写、删减或改变语义。
- OCR 或视觉模型生成的文字只能在来源层级和置信度清楚记录后进入正文；无法确认的内容写入 `evidence/`、`manifest.yaml` 或 `reviews/markdown-content-repair.yaml`，不能以 `Unknown`、“需要核对”、“原文疑似”或 OCR 旁注出现在正文，更不能因为已经生成 Markdown 就当作确认事实。
- 图片是来源证据，必须放在语义文字和关系说明之后；图片不能替代 Markdown 中可读、可检索的正文。自动阶段不得删除页面图片；仅人工审核确认“页面仅含已提取文字且没有额外图示”后，才能从 `index.md` 移除图片链接，同时保留 `assets/` 和 manifest 的来源记录。

最小格式示例（标题和关系说明均按来源实际情况可选）：

```markdown
## 标题

> 关系：可选，复杂图片内文字呈递进关系。

提取的文字正文
```

没有可靠标题时，不输出“标题”占位文本或页码标题；没有特殊文字关系时，不输出引用块，直接从提取的文字正文开始。必要的原始页面图、复杂区域图或嵌入图片应在正文和关系说明之后追加，不能置于正文之前。候选标题、页码、`来源页`、`A4 状态`、`来源与原图`、审核状态和 OCR 解释都只能进入工程工件。

## 决策规则

- 有可用原生结构时，不先 OCR。
- 结构关系无法由文字可靠表达时，必须保留视觉资产并在 Markdown 中链接。
- 视觉模型、mac96/SSH 和 OCR 都只能由当前 profile 的已验证能力显式启用；本轮不调用任何视觉模型或 OCR。
- 在线视觉逐字转写仅在用户显式授权时，对当前一张页面图或区域图生成候选；不读取其他文件或相邻页，不能覆盖原生层或直接写入 `index.md`。
- 高质量在线基准 → 本地视觉模型复现 → 质量比较 → 达标后降本是后期 calibration 路线，默认关闭且不属于本轮验收。
- 不确定内容使用工程层 `Unknown` 或 `needs-review`，不能伪装成确定事实，也不能泄漏为读者正文旁注。

## 最小验收

- Markdown 可读、可检索，且每个页面/区域的视觉链接存在。
- 原生文字、OCR 候选和冲突状态在工程工件中可区分，且不污染读者正文。
- 复杂图没有被扁平化丢失；来源位置可回溯。
- 落盘文件名使用 A6 规定的最短英文语义 `file_name`，例如 `time-quadrants.png`；
  中文阅读语义放在 `display_name`；稳定 ID、来源指纹、哈希、asset kind 和 ordinal 只用于
  manifest 和程序关联。正式包的所有资产直接落在扁平 `assets/`；类型由 manifest 区分，
  `markdown-asset-copy` 只需复制实际引用并重写链接。
- 失败时输出失败态和可重试边界，而不是返回空成功。
- 测试目录中的日志、断言和测试报告只能证明测试状态，不属于本 Skill 的业务结果。

## 相关契约

读取包内 `contracts/workflow-a-source-runner.yaml`、`contracts/markdown-asset-package.yaml`、
`contracts/source-visual-asset.yaml` 和 `references/naming-policy.zh.md`。它们定义 runner
边界、输出字段、来源关系、命名与去重规则；不要在本 Skill 中复制一套冲突 schema。
