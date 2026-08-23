---
name: vision-page-analysis
title: "页面视觉理解"
description: 读取当前单张页面图或区域图，输出可追溯的 Markdown 文字与视觉结构候选；Apple Vision OCR、小视觉模型和 mac96 30B 的主次由真实页面基准决定，不预设固定顺序。
order: 9
---

# 页面视觉理解

## 职责

这是 Workflow A 的统一视觉能力入口，负责从当前一张页面图或区域图中提取两类互补结果：

- `text`：图片中实际出现的文字、公式、标签和可直接确认的 Markdown 内容。
- `visual`：原生文字/layout 无法充分表达的图表、区域、箭头、分组、层级、比较、颜色编码和其他视觉关系。
- `reader_image_policy`：区分模型输入用页面渲染图与读者真正需要的语义图片；纯文本页可直接只交付 Markdown。

它不替代 PPTX XML、PDF 布局或其他原生内容提取；原生内容始终是事实层，视觉模型结果是候选/复核层。对扫描文字页，Workflow 必须明确区分：

1. `text-recognition`：忠实保留页面实际可见文字和定位证据；
2. `markdown-structure-reconstruction`：重建标题、自然段、目录和列表，但不得补写页面不存在的结构；
3. `advertising-classification-and-treatment`：依据独立策略分类、标记或提出处理建议，默认 `preserve-only`。

本 Skill 是视觉模型候选路线。mac96 30B、mac96 小模型和 Apple Vision OCR 加确定性结构重建的选择，必须来自同一真实页面集的质量、耗时与资源证据；`ssh → mac96 → Ollama` 只是当前视觉适配器的传输方式，不代表 Workflow 默认优先选择 mac96。外部在线 provider、批量上传或独立 API 仍需另行明确授权。

## 输出模式

调用方必须声明 `mode`；未声明时使用 `full`：

- `text`：只输出 Markdown 文字，适合文字覆盖不足但没有视觉关系需求的页面。
- `layout`：只输出结构化视觉分析，适合原生文字/layout 已足够、只需补充图表关系的页面。
- `full`：一次请求同时输出 Markdown 和结构化视觉分析，是默认模式，避免同一页面无必要地调用两次模型。

`pass_strategy` 默认是 `single`。对关系密集、图表复杂、颜色/空间编码重要或需要更高可靠性的页面，调用方可以显式使用 `dual`：先做专门的 `text` 请求，再做专门的 `layout` 请求。两次请求是单个视觉模型内部的质量策略，不代表视觉模型一定是文档级主路线。

## 文字转写固定提示词

对 `mode=text`，使用下面的固定提示词并只输出 Markdown。对 `mode=full`，`markdown` 字段必须遵守相同的文字转写规则；外层仍保留契约要求的 JSON，供调用方同时保存视觉结构。`mode=layout` 不输出正文转写。

```text
请只根据当前上传的这一张图片提取文字内容。

处理要求：
直接使用当前视觉模型阅读图片，不使用 OCR。
不读取其他文件，不参考上一张或下一张图片。
只提取图片中实际出现的文字，不补写、不解释、不总结、不改写原意。
允许进行必要的 Markdown 排版、分段和标点整理，但不要改变原文措辞。
不要新增“定义”“选择关系”“解读”“总结”等图片中没有的标题。
图片中的坐标信息不要写入正文，不要出现“左侧、右侧、上方、下方、中心、箭头指向”等描述。
如果原来是列表，则转换成md的列表；也包含其他格式，如表格
如果图片表达的是多个选项之间的关系，用紧凑的一行一项表达，例如：
选择 A：……
选择 B：……
如果图示关系可以直接确认，只保留它表达的实际内容，用图片中出现的标签和关系类型紧凑表达；不保留视觉位置或箭头描述。关系无法确认时标记为“〔无法确认〕”，不要自行猜测。
只有图片中确实出现的标题，才能作为 Markdown 标题。
文字无法确认时标记为“〔无法确认〕”，不要自行猜测。

Markdown 结构保真要求：
只有视觉证据支持时才使用 `#`/`##`/`###`，层级不确定时保留原文并登记 `needs-review`。同一句的软换行应合并，不同自然段之间留空行，不得把整页正文平铺成一个段落。列表只保留页面已有的项目符号或编号，目录保留条目与可见页码的对应关系，目录续页不得新增图片中不存在的“目录”标题。页眉、页脚和页码在提取阶段仍须转写，只能登记为候选，不能删除。单页不得自行续写跨页句子。

微信号、联系方式、二维码附近说明、推广语和其他疑似广告文字必须逐字保留。不得在提取阶段判断、隐藏、删除、改写或归纳广告；广告分类和处理属于独立后置策略。二维码不得作为图片资产提取，也不得解码，但二维码附近实际可见的文字仍必须转写。

`full.markdown` 与 `structure_inventory` 必须逐项一致：先完成 `markdown` 字符串，再只根据该字符串反向统计 `structure_inventory`。`title_levels` 只允许使用字面量 `#`、`##`、`###`、`####`、`#####`、`######`，不得写 `1`/`2`/`3`；`list_count` 等于实际带 `- ` 或有序标记的列表项数量。不得在清单中声称存在 Markdown 没有写出的标题、列表或目录项；否则结构守卫失败，适配器保留原始响应，不得返回 `complete`。

视觉与读者图片判定：
如果页面只有可由 Markdown 完整表达的标题、段落、列表或简单目录，返回 `page_content_type=text-only`、`layout_dependency=none|markdown-representable`、空 `semantic_visuals`，并在 `reader_image_policy` 中返回 `"action": "omit"`。照片、插图、图表、流程、矩阵、关系图或依赖颜色/空间位置才能理解的内容必须 `retain`；不确定时 `needs-review`。二维码不得作为图片资产提取，不解码、不裁剪、不写入 Markdown 图片，只在 `ignored_elements` 记录 `type=qr-code` 和 `action=ignore`。

输出格式：
先输出图片中实际出现的标题；没有标题时不要新增标题。
接着输出可直接确认的正文关系；没有可确认关系时不要新增关系标题或占位。
然后输出图片中的正文、其他标签、公式、结论或例子；只输出图片中实际出现的内容。
只输出整理后的 Markdown，不要附加说明。
```

## 输入边界

- 每个 pass 只能包含一张当前页面图或当前区域图。
- 不附带 OCR JSON、bbox、置信度、其他页面正文、前后页图片或整份文档。
- 模型不得猜测图片外的信息；无法确认的文字或关系必须保留为 `〔无法确认〕`、`Unknown` 或 `needs-review`。
- 原生内容模型可以由 Workflow 在本地用于选择 `mode`/`pass_strategy`，但不得作为图片视觉模型的隐藏上下文上传。
- 同一已选视觉路线返回 `complete` 后不得无理由重复调用其他模型；若质量守卫失败或正在执行登记过的对照基准，可以升级到下一条已授权候选路线，并保留全部尝试证据。

## 失败与降级

- 路线选择前先读取 `recognition-route-benchmark` 及其质量评审，不允许把模型大小、非空输出或一次成功请求当成固定优先级。
- 当前路线返回可解析结果：分别记录 `text.status`、`visual.status`、结构清单和资源指标，再运行页面结构守卫。
- 原始文字覆盖失败、结构守卫失败、低置信、复杂布局、阅读顺序未决、静默遗漏或臆造结构时，升级到下一条已通过校准且已授权的候选路线。
- Apple Vision OCR 只能产生原始文字和坐标候选；必须再经过独立 Markdown 结构重建，不能补视觉关系，也不能直接宣称 Markdown 合格。
- 当前对话在线视觉模型只有在当前任务明确授权时才可参与基准或回退；记录 `transport: conversation`、实际模型和输入范围。
- 所有路径都失败时保留 `failed/unavailable` 证据；`needs-review` 只表示真实未裁决关系，不是自动 fallback。
- `full` 只有在文字层和视觉层都成功时才是 `complete`；只有一层成功时为 `partial`。
- SSH 可达、HTTP 200、模型列表、队列接收或非空但不可解析的响应，都不能单独证明视觉理解成功。

## 证据

适配器输出 `evidence/vision-page-analysis.json`，记录：

- 输入资产 ID、来源指纹、图片 SHA-256、页码/区域；
- `mode`、`pass_strategy`、实际模型调用次数和每个 pass 的状态；
- `text.markdown` 与 `visual.visual_analysis` 的分层结果；
- provider、transport、target、model、请求时间、提示词版本和诊断；
- 标题层级、自然段、列表、目录、页眉页脚和跨页连续性的结构清单；
- 路线升级原因、fallback 建议与未决关系。

证据不覆盖原始图片、原生文字、布局、OCR 或 manifest，不写入密码、Token、Cookie、Authorization 或订阅地址。

## 每页落盘不变量

当调用方对 PDF 或其他分页来源执行页面转换时，`text`/`layout`/`full` pass 的结果必须由
Workflow 适配器立即投影为当前页的成对产物；调用提示词不需要另行声明这一要求：

- `evidence/vision-page-analysis-pNNN.json`：当前页的完整视觉证据；
- `evidence/vision-text-transcription-pNNN.json`：当前页文字层候选及其来源证据；
- `page-units/pNNN.md`：当前页读者层 Markdown；只有 `reader_image_policy` 要求保留时才附当前页原始图链接。

页面只有在上述 JSON 证据和 Markdown 文件都存在、可读取且页码/来源/图片处置一致时，才算
`resumable-complete`。模型分析 JSON 已存在但 Markdown 缺失时，resume 必须直接从已有 JSON
补写 `page-units/pNNN.md`，不得重新调用模型；只有有效视觉证据缺失或不完整时才重新处理该页。
页面写入应先完成当前页三类投影，再记录该页 `progress-pNNN.json` 的完成状态。不得依赖用户在
每次执行提示词中重复说明“请生成 page-units/pNNN.md”。

## 文件投影

本 Skill 输出的是候选层，不直接把模型回答当成最终读者文档：

- `text.markdown` 由 A4 reconciliation 和 A5 Markdown 组装后进入 `index.md`；
- `visual.visual_analysis` 保留在 `evidence/vision-page-analysis.json`，用于图表、关系和未决项复核；
- 分页执行器必须同时生成当前页 `page-units/pNNN.md`，它是 `index.md` 的逐页读者层投影，不是额外提示词任务；
- 整页渲染图始终可作为工程证据保留，但 `text-only` 且 Markdown 足够时不得自动写入 `page-units` 或 `index.md`；
- 复杂图表或明确的图片重建需求，再按需生成 `evidence/visual-reconstruction-profile.yaml`。

这样可以同时得到读者可读的 Markdown 和可审计的结构信息，又不会让结构化视觉字段污染正文。
