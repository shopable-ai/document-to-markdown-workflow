# Workflow B：Markdown 局部内容到 GPT-image-2 单图

## 这个 Workflow 解决什么问题

把已有 Markdown 中的一个局部知识单元，规划为一张可读、可记忆、可复核的视觉图片。

它不是 PPT/PDF 重新制作器，也不是把整份 Markdown 原样塞给图像模型；它先选择内容边界，再决定视觉结构、信息密度和可选参考资产，最后只调用一次单图生成链路。

## 什么时候使用

适合：

- 已经有 Markdown，希望把其中一节变成流程图、因果图、对比图、矩阵、时间线或概念图；
- 需要一张知识传播图、记忆图或阅读辅助图；
- 可选使用 Workflow A 产生的页面图或复杂区域图作为结构/风格参考；
- 需要保留提示词、来源片段、参考资产和图片验收记录。

不适合：

- 要生成一整套 PPTX、PDF 或多张图片；
- Markdown 片段边界不明确；
- 信息密度过高却拒绝拆分或缩减；
- 希望未经判断地复制来源图版式。

## 输入

必需：

- `markdown_source`：Markdown 或文本文件；
- `section_selector`：标题、锚点、行范围或语义选择器。

可选：

- `visual_intent`：流程、因果、对比、矩阵、时间线、概念图等；
- `reference_assets`：Workflow A 的来源视觉资产或其他参考图片。

## 输出

主输出必须恰好是一张 raster 图片。请求和复核记录应保存在同一运行目录中：

```text
<output_root>/<run-id>/
├── selected-section.md
├── visual-intent.yaml
├── visual-structure-plan.yaml
├── selected-references.yaml
├── generation-request.yaml
├── generated-image.png
└── visual-review-report.yaml
```

请求字段和秘密禁止字段由 [gpt-image-generation-request.yaml](../../contracts/gpt-image-generation-request.yaml) 定义。请求 manifest 不得写入 API key、Authorization、Cookie、订阅 URL 或其他秘密。

## 执行阶段

1. **B1 Markdown 内容选择**：选出一个自洽知识单元，保留必要限定。
2. **B2 视觉用途判断**：选择一个主视觉结构；不确定时返回 `visual-intent-ambiguous`。
3. **B3 内容与视觉结构规划**：映射节点、层级、方向和信息密度；过密时返回 `needs-split`。
4. **B4 参考资产选择**：记录每个参考资产的来源和用途；没有参考图也可以继续。
5. **B5 提示词组装**：明确事实、结构、画布、文字保真、风格和禁用项。
6. **B6 GPT-image-2 调用**：成功时只返回一张图片，并记录调用使用量摘要。
7. **B7 单图验收**：检查文字可读性、结构完整性、事实一致性和视觉层级。

每个阶段的 Skill、Tool、输出和证据文件以同目录的 [workflow.yaml](workflow.yaml) 为准。

## 成本与外部调用边界

- B1–B5 默认在本地完成，不发送整份 Markdown；只发送经过选择和规划的单个知识单元。
- B6 是唯一的在线图像生成阶段，基线是每个生成请求一次图像调用。
- 参考资产是证据或风格输入，不自动升级为通用模板，也不默认复制其版式。
- 本 Workflow 不生成 PPTX、PDF、SVG 或图片批次。

## 失败处理

- 找不到片段：`markdown-section-missing`；
- 无法选择主结构：`visual-intent-ambiguous`；
- 信息密度过高：`needs-split`，不得强行调用模型；
- 供应商超时或限流：`provider-timeout` / `provider-rate-limit`，保留 `request_id` 后重试；
- 图片生成但验收失败：`review-failed` 或 `needs-revision`，只调整结构规划和提示词后重试；
- 策略限制：`policy-blocked`，不得绕过限制。

完整失败态见 [failure-states.zh.md](../../references/failure-states.zh.md)，验收门禁见 [ACCEPTANCE.zh.md](ACCEPTANCE.zh.md)。

## 验证方式

从规格包根目录执行：

```bash
python3 tests/validate_workflow_package.py
```

这只验证 Workflow、Skill、Tool、契约和文档路由；真实 GPT-image-2 调用必须在明确启用在线适配器和预算后单独记录，不能用静态验证冒充生成成功。
