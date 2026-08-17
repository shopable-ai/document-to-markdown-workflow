# Workflow A：文档资产化与 Markdown 转换

## 这个 Workflow 解决什么问题

把 PPT、PPTX、PDF 或图片转换为一个可检索、可追溯的 Markdown 资产包，同时保留页面图、嵌入图片，并在整页图不足以支撑复核时生成复杂结构区域图。

它的目标不是“把文档 OCR 成一段文字”，而是同时保存：

- 语义层：Markdown、标题、列表、表格、节点和关系；
- 视觉层：原始页面、嵌入媒体、复杂区域和局部裁剪图；
- 追溯层：来源页码、对象坐标、出现记录、哈希、状态和不确定性。

## 什么时候使用

适合：

- 需要把 PPT/PDF 纳入知识库或 Markdown 资产库；
- 页面包含流程图、矩阵、连线、分组、图文混排或复杂层级；
- 后续可能把来源视觉资产作为 GPT-image-2 的可选参考图；
- 需要保留“原生提取结果、OCR 候选和视觉证据”的差异。

不适合：

- 只想快速复制纯文本；
- 需要直接生成新的 PPTX/PDF；
- 想在一次调用中自动串接到 GPT-image-2。

日常触发只需说明来源和输出位置：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

内部入口、阶段和输出格式由注册表、Workflow 和 contracts 自动解析，不需要在提示词中重复填写。

## 本轮执行边界

当前 active 入口是来源无关的 `adapters/workflow_a_source_runner.py`，默认只生成执行计划；
对单一 fixture/source 可显式使用 `--execute` 生成确定性 package：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "<SOURCE>" \
  --output-root "<OUTPUT_ROOT>" \
  --pages all \
  --plan-only
```

本轮只对 `tests/fixtures/` source 做了转换和完整测试；不调用在线或局域网视觉模型、不调用 OCR。
fixture 输出仍是测试证据，不是生产业务结果。
`archive/legacy-adapters/workflow_a_runner.py` 暂时保留为 `legacy/document-profile` 兼容入口；其固定文档标题、
页眉过滤、OCR 标题区域、PDF DPI、mac96/SSH/Ollama 和旧 Markdown 模板不得复制到新 runner。
详细迁移表见 [来源无关 Runner 设计](../../references/source-runner-design.zh.md) 和
[遗留代码状态](../../references/legacy-code-status.zh.md)。

正式入口在候选比较和转换前先执行横向 `execution_preflight`。它只检查可廉价确定的
风险（来源、格式、页码、容器、渲染器、输出碰撞和候选一致性），并写入
`evidence/preflight.json`；预检通过不等于转换通过，最终仍以独立
`reports/validation-result.json` 为准。规则和专家推进方式见
[执行前风险预检](../../references/execution-preflight.zh.md)。

## 输入

必需：

- `source_document`：PPT、PPTX、PDF、PNG、JPG 或 JPEG；
- `output_root`：输出资产包的目录。

### 输入边界与单一来源原则

单文件 runner 一次只接收一个 `source_document`，并为这个来源生成一个独立 package。PPTX、PDF
或图片即使文件名相近，也不能在同一次转换中混合文字层、页面图或资产；只有用户
明确声明它们是同一逻辑文档的候选版本时，才先运行 candidate comparator，比较代表页
并选择一个 `preferred_source`，随后仍只转换这个来源。候选比较证据不等于第二个业务
来源，也不改变 `source_isolation: one-source-per-run`；运行报告仍记录
`source_selection: single-source`。

需要一次处理多个 source 时，使用下面的批处理编排器；它不改变上述单包边界：

```bash
python3 adapters/workflow_a_batch.py --legacy-compat \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

后续 execute 阶段仍由 dispatcher/batch 编排；本轮它们不启动真实转换。单一来源的未来 bounded 入口示例：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

这里的机器关系是：`workflow_a_dispatcher.py` 负责读取
`workflow-registry.yaml`/`workflow.yaml`、限制 bounded 页码并记录 A1-A7 执行 envelope；
`workflow_a_candidate_compare.py` 只在显式声明同一逻辑文档候选时生成比较证据；
`workflow_a_source_runner.py` 是 dispatcher 调用的 active plan-only source runner，只输出来源无关执行计划，
不能声称 Markdown 转换完成。`archive/legacy-adapters/workflow_a_runner.py` 不属于 active 路径，仅保留为明确指定的
legacy/document-profile 兼容入口。

dispatcher 要求显式页码，阻止 bounded 验证意外扩展为 `--pages all`。PDF、PPTX
和图片必须分别调用，不能因为文件名相近就合并。generic runner 通过 profile 选择
各格式方案；legacy runner 的旧参数只用于兼容/恢复，不得作为新 source 的默认入口。

多个来源如需恢复旧包请显式调用 `python3 adapters/workflow_a_batch.py --legacy-compat`。
它是上层队列编排器，不改变
单文件 runner 的来源隔离：每个 source 都有独立 package、manifest、index.md 和
`reports/validation-result.json`，批次根目录另写 `run-record.yaml`。默认串行，只有有
明确 mac96/Ollama 容量证据时才配置有限并发；长文档按页请求，使用 `--context-mode`
控制严格隔离或受限邻页提示。完整配置、恢复和判定见根目录的
[多文件队列配置](../../references/workflow-a-batch-config.zh.md)。

### 长文档 smoke、分批与恢复

长 PDF 不允许直接把 `--pages all` 当成一次不可观测运行。promoted dispatcher/batch 路径必须先处理所选页的前 1–2 页（可用 `--smoke-pages 1-2` 显式指定），写入 `evidence/smoke-report.json`；标题仍是页码、正文和 OCR 候选为空、页面图/Markdown 链接缺失、relation-only 或空语义占位任一关键门禁失败，stdout 会返回失败页、阶段、证据路径和修复建议，并停止后续页。当前 generic source runner 的 deterministic execute 只保留 `smoke-report.json: not-run`，不得把它当成 smoke 通过。

运行期间会在阶段边界追加 `evidence/progress.jsonl`，并把当前阶段、页码、状态、耗时和下一步打印到 stderr；这不等于已经证明 15 秒实时心跳或无进展硬停止。调度器在前置证据未达到终态时不会启动后续脚本；允许的 OCR/视觉降级必须作为当前 Workflow 的明确 fallback 记录。旧恢复脚本和夹具脚本的边界见根目录 [`references/legacy-code-status.zh.md`](../../references/legacy-code-status.zh.md)。

smoke 通过后，用 page-batch 编排器按小批次运行。每批都使用新 package 目录并写 checkpoint，不能覆盖既有真实运行证据：

注意：smoke 通过只证明页面资产、提取候选和 Markdown 结构门禁通过；语言质量修复现在已经有局域网优先适配器，
只需保留每页 `evidence/markdown-content-repair-pNNN.json` 和结构守卫结果，页面修复不另起 pytest
或独立 validator 测试阶段，才能声称文字已按当前页边界整理。
局域网不可用或结构守卫未通过时，package 仍是 `needs-review`，不能把未执行的在线回退或人工修复写成完成。

```bash
python3 adapters/workflow_a_batch.py --legacy-compat \
  --source "/absolute/path/long.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all --smoke-pages 1-2 --batch-size 8
```

checkpoint 位于 `page-batch-*/checkpoint.json`。恢复时使用
`--resume-pages <checkpoint.json>`，只从最近一个通过批次继续；失败批次会用新的 attempt 目录重跑，旧证据保留。每个页面状态分别记录 native、OCR、visual、标题来源和候选数量，不能把 mac96 关系复核当成文字成功。

### 读者层页面单元与阶段门

读者层 Markdown 修复使用 [`page-markdown-unit`](../../skills/page-markdown-unit/SKILL.md) 和
[`page-markdown-unit.yaml`](../../contracts/page-markdown-unit.yaml)：

1. 先只拆分和修复第 1 页，页面结构守卫通过后才解锁第 2–5 页。
2. 第 2–5 页小批量必须经过人工查看和页面结构守卫，之后才允许第 6–100 页。
3. 修复一页时只读取该页页面单元和该页页面图；OCR JSON、bbox、置信度、逐页 Vision JSON、页面图和原始 `manifest.yaml` 保持只读。
4. 页面单元按固定字段组织：语义标题、正文、列表/图示文字、表格与公式、解读、举例与思考、模型结构、已确认的关系说明、原始页面图和待核对项。
5. 合并器按页面编号升序逐字拼接页面单元，保留原始页面图链接，不重新推断、摘要、翻译或补写文字。

`document-temp.md` 是当前已通过页面单元的可审阅派生预览，不能覆盖或替代最终
`index.md`，也不能成为新的事实来源。未确认的箭头、层级、流程、矩阵或颜色关系只能保留在
`Unknown`/`needs-review` 待核对项中。

### 页面视觉理解

A4 使用 [`vision-page-analysis`](../../skills/vision-page-analysis/SKILL.md)，通过 `ssh mac96`
读取当前一张页面图或区域图。默认 `mode=full`、`pass_strategy=single`，一次请求同时输出：

- `text.markdown`：图片中实际出现的文字、公式和标签；
- `visual.visual_analysis`：图表、区域、箭头、分组、层级、比较、颜色编码和未知形式。

关系密集或高风险页面可以显式传 `pass_strategy=dual`，分别执行 `text` 与 `layout` 两个专门 pass。
适配器是 [`adapters/mac96_vision_page_analysis.py`](../../adapters/mac96_vision_page_analysis.py)，
统一证据写入 `evidence/vision-page-analysis.json`，记录输入指纹、模式、请求策略、实际请求次数、
模型、transport、请求时间、文字层、视觉层和诊断。文字层失败时才进入当前对话视觉模型或
Apple Vision OCR；这些 fallback 不能伪造视觉结构完成。旧的 `vision-text-transcription` 和
`mac96-visual-review` 仅作为兼容别名保留。

分页执行器会在每页 pass 完成后自动写入当前页的
`evidence/vision-page-analysis-pNNN.json`、`evidence/vision-text-transcription-pNNN.json` 和
`page-units/pNNN.md`。只有这三类产物都存在且可读取，页面才会被 resume 判定为已完成；
如果只剩 JSON，resume 会从已有证据补写 Markdown，不会重新调用模型。因此日常提示词只需说明
来源、输出根目录和页码，不需要重复写“生成 page-units/pNNN.md”。

### 页面 Markdown 修复

`page-units/pNNN.md` 生成后，A5 立即进入
[`markdown-content-repair`](../../skills/markdown-content-repair/SKILL.md)。这一步不是再次看图：
它只把当前页 Markdown 发送给局域网 `mac96` 上的语言模型，修复断词、错误换行、段落粘连、
列表/标题结构和局部标点。默认模型标识为 `qwen3.6:latest`，实际可由运行配置覆盖。

局域网结果先通过页面编号、原始页面图链接、读者层字段和 `Unknown/needs-review` 关系边界检查，
通过后才原子写回当前 `page-units/pNNN.md`，并写入
`evidence/markdown-content-repair-pNNN.json` 与 `reviews/markdown-content-repair.yaml`。
这组检查就是页面修复的完整验证，不再额外运行 pytest 或页面独立 validator；完整资产包的
链接、完整性、来源和证据链验收仍由 A7 负责。
局域网修复成功后不再调用在线模型；只有局域网不可用、超时、不可解析或未通过守卫，且本次任务
明确授权时，才允许当前在线模型对同一页回退一次。其他页面、图片、OCR JSON、bbox、manifest
和整批 evidence 都不属于这一步的输入。

当同名 PPTX 与 PDF 明确声明为同一逻辑文档候选时，先只比较代表页，再选择一个主来源：

```bash
python3 adapters/workflow_a_candidate_compare.py \
  --source-a "/absolute/path/input.pptx" \
  --source-b "/absolute/path/input.pdf" \
  --pages 3,4 \
  --output "/absolute/path/candidate-comparison.json"
```

同名且页数一致时，规则默认优先 PDF 原始页面截图内容；PPTX 仍必须用自身 Office
渲染并写入候选差异证据。确定 `preferred_source` 后，只对该来源执行完整转换：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

## 输出资产包

具体字段由 [markdown-asset-package.yaml](../../contracts/markdown-asset-package.yaml) 和 [source-visual-asset.yaml](../../contracts/source-visual-asset.yaml) 定义。典型目录如下：

```text
<output_root>/<package-id>/
├── index.md
├── manifest.yaml
├── assets/
│   ├── <short-semantic-slug>.png
│   ├── <short-semantic-slug>-02.png
│   └── ...             # page-render/embedded-media/complex-region 由 manifest 区分
├── evidence/
│   ├── source-inventory.json
│   ├── native-content-model.json
│   ├── reconciliation.json
│   └── visual-reconstruction-profile.yaml  # 复杂资产适用时；结构 + 可复用提示词
├── reports/
│   ├── processing-report.yaml
│   ├── acceptance-report.yaml
│   ├── failure-report.yaml
│   └── validation-result.json  # runner 自动写入的独立验收结果
```

`index.md`、`manifest.yaml`、`assets/` 和 `reports/` 才是正式业务结果；`evidence/` 是辅助证据，不能替代主结果。对复杂结构或明确需要重新生成的图片，`visual-reconstruction-profile.yaml` 是随资产包保留的下游重建输入；它不能替代原图，也不能证明生成图片与来源等价。视觉资产语义筛选报告则记录为什么某些页面图不再进入读者层，并保留被移除文件的哈希和来源页码；筛选只移除读者层引用，`assets/`、manifest 资产和 `source_occurrences` 必须保留。未获批准的页码由 `classify_visual_assets_mac96.py` 生成 `candidate-only` 报告，不能自动删除。

### Workflow A 与集合阅读包的命名边界

Workflow A 的正式转换包继续使用：

```text
index.md = 转换后的完整正文
page-units/ = 工程过程中的逐页工作单元
```

当已有页面单元被另行整理为读者层集合时，才使用
[`markdown-collection-output-layout.yaml`](../../contracts/markdown-collection-output-layout.yaml)：

```text
index.md = 新增导航索引
total.md = 原 index.md 所承载的完整合并正文
单页内容/ = 中文读者层的逐页 Markdown 副本
```

因此，不能把 Workflow A 的 `index.md` 全局替换成 `total.md`；只有集合阅读包内部才采用
“`index.md` 索引 + `total.md` 合并全文”的双文件结构。

### 读者层与工程层

| 层 | 读什么 | 不放什么 |
|---|---|---|
| 读者层 | `index.md`：标题、主题、正文、列表/表格、已裁决的关系说明和必要页面图链接 | bbox、对象 ID、模型调用、token、fallback、OCR 诊断或其他运行细节 |
| 工程层 | `evidence/`：原生模型、mac96/Apple Vision 请求响应、渲染和复核范围、复杂资产的视觉重建 profile；`manifest.yaml`：资产 ID、哈希、来源出现记录和 profile 回指；`reports/`：处理、验收、失败、独立 validator 状态 | 不把工程证据当成读者正文，不用 runner 自报状态替代独立验收 |

`manifest.yaml` 连接文件名、资产和来源指纹；`reports/` 解释本次 package 是否可用；
`evidence/` 允许维护者重读输入、复核降级路径和定位问题。三者都服务工程追溯，
不应复制进 `index.md`。

### Markdown 默认阅读顺序与形式选择

常见图文页面按“标题 → 主题/对比 → 内容要点、列表、表格或分区 → 图示说明（有已裁决关系时） → 原始页面图”输出。这个顺序是默认阅读 profile，不是所有 PDF、图片或其他文档的统一模板；文字型、表格/图表型、流程/矩阵型、低质量扫描、多栏或跨页内容应按实际结构选择 Markdown 表达。来源指纹、bbox、对象 ID、字体、渲染命令、OCR 候选、模型调用和未决诊断只进入 `evidence/`、`manifest.yaml` 或 `reports/`，不得混入读者层。

资产文件采用双名制：`file_name` 使用最短英文语义组成的稳定 ASCII 名称，
例如 `time-quadrants.png`；页码、稳定 ID、
来源指纹、SHA-256、asset kind、用途和 ordinal 只进入 manifest，不作为随机前缀或技术字段
暴露在文件名中。原始文件名只作为 `original_filename` 证据保存。

正式 Workflow 包和读者导出都使用扁平的 `assets/`。页面图、嵌入媒体和复杂区域图由
manifest 的 `asset_kind`、来源位置和 occurrence 区分；已有 Markdown 交给
`markdown-asset-copy` 时只需复制实际引用的文件，并同步重写到目标 `assets/`，不再穿过
`pages/` 这一层，也不会把来源哈希带入新文件名。

## 执行阶段

A1–A7 是当前 Workflow 的运行阶段，不是完整的工程任务树。工程任务先按
[`EXECUTION-TASK-TREE.zh.md`](../../EXECUTION-TASK-TREE.zh.md) 先以 ET0 的可观测 smoke 和 ET1 单页内容质量纵向切片验证，再以 ET2/ET3 扩展到多页和多文件；ET1.5 的语义/视觉质量审计与 A7 的 package validator 是互补门，不能用某个阶段或某个 Skill 的成功替代整条切片验收。

1. **A1 来源预检**：识别文件类型、页数、文字层和可解析性。
2. **A2 原生内容提取**：当前 active runner 优先解析 PPTX XML 或 PDF 布局；DOCX OOXML 仍是未接入生产入口的扩展路线。
3. **A3 视觉来源资产保留**：保留整页图和嵌入媒体；只有整页图无法支撑复杂关系复核时才生成区域图，不能为了满足目录而重复截图。
4. **A4 页面视觉理解与文字层降级**：原生结构提取后，默认通过 `ssh mac96` 的
   `vision-page-analysis` 以 `full + single` 读取当前单图，一次生成 Markdown 和视觉结构；
   关系密集或高风险页面才显式使用 `dual`。文字层不可用时才调用当前对话视觉模型或本机
   Apple Vision OCR；文字候选和视觉结构必须分层写入 `evidence/`，不得覆盖原生事实，也不得
   用 OCR 伪造视觉关系。复杂结构或明确重建意图的资产，额外保存 visual reconstruction profile。
5. **A5 Markdown 组装与视觉资产语义筛选**：建立页面/章节结构、关系说明和视觉链接；只从读者层移除已被可靠文字覆盖且没有视觉增量的封面、目录或纯文字页面图，保留复杂图示和关系布局及其来源资产登记。
6. **A6 命名与来源登记**：处理语义命名、来源出现记录、内容去重和筛选后的资产登记。
7. **A7 资产包验收**：执行链接、完整性、追溯性和不确定性检查。

每个阶段的 Skill、路由、输出和证据文件以同目录的 [workflow.yaml](workflow.yaml) 为准。
PPTX 页面图必须来自 PPTX 自身的 Office 渲染；PDF 页面图必须来自原始 PDF 渲染。

如果沿用早期的 `$knowledge-map-image-to-md` 名称，仍然执行本 Workflow 的 A1-A7；
仅额外加载 [知识地图差异策略](../../references/knowledge-map-profile.zh.md)。该入口
不复制本说明中的阶段，也不改变 Workflow A 的正式输出布局。

## 成本与外部调用边界

- 默认使用本地解析和渲染；图片文字优先走 `ssh mac96` 视觉转写，不先调用 Apple Vision OCR。
- 每次 mac96 尝试都必须写入运行证据；不可用、超时或不可解析时先降级到当前对话在线视觉模型，
  再在当前对话模型不可用时降级到 Apple Vision OCR。三者均失败时是 `failed/unavailable`，不是自动
  `needs-review`。mac96 成功返回非空可解析 Markdown 后，不再调用后续路径，也不能因为没有可选
  `unknown_forms` 字段而强制降级。
- 当前对话在线视觉模型只允许读取当前这一张图片，不创建独立 HTTP/SSH 适配器；其他外部在线视觉模型
  仍不是默认兜底，只有用户另行明确授权时才可调用，并必须单独记录成本、输入范围和结果。
- 本 Workflow 不调用 GPT-image-2，不生成 PPTX、PDF 或 SVG。

## 失败处理

- 输入损坏或不支持：`source-invalid` / `unsupported-format`；
- 本地解析或渲染暂时失败：`extract-timeout` / `render-failed`，允许重试；
- mac96、当前对话在线视觉模型或 Apple Vision OCR 不可用：保留原生结果和视觉资产，分别标记
  `visual-transcription-unavailable`、`conversation-vision-unavailable`、`ocr-unavailable` 或 `failed`，
  不得声称图片内容已完成提取；
- 真实失败、冲突和未裁决关系必须进入 `Unknown`、`needs-review` 或失败报告，不能静默删除；未执行开放集合扫描本身不是失败，必须在 evidence 中写明实际复核范围。

完整失败态见 [failure-states.zh.md](../../references/failure-states.zh.md)，验收门禁见 [ACCEPTANCE.zh.md](ACCEPTANCE.zh.md)。

## 验证方式

以下命令只用于维护者修改规格包后的可选检查，不是每次页面 Markdown 修复的运行步骤；
单页修复只执行适配器内的结构守卫、当前页写回和规定阶段的人工查看。

从规格包根目录执行：

```bash
python3 tests/validate_workflow_package.py
RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

这些命令验证规格和 fixture 契约，不代表所有 `planned` 的解析器、OCR 或 mac96 适配器已经实现。

### bounded 第 3、4 页入口

对已明确授权的第 3、4 页，先生成候选比较证据，再用 dispatcher 对选定的单一来源
执行；本轮文档维护、规格校验或普通 fixture 测试不应因此启动真实转换：

```bash
python3 adapters/workflow_a_candidate_compare.py \
  --source-a "/absolute/path/input.pptx" \
  --source-b "/absolute/path/input.pdf" \
  --pages 3,4 \
  --output "/absolute/path/candidate-comparison.json"

python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

对已经生成的真实 package，独立验收命令是：

```bash
python3 tests/validate_real_output_root.py \
  "/absolute/path/output-root/<package-name>"
```

`validate_workflow_package.py`、fixture contract 和 pytest 证明的是规格/fixture 边界；
`validate_real_output_root.py` 才是对真实 package 的独立重读。任何 validator 通过只
说明契约、完整性和证据链满足门槛，不自动等于视觉/语义质量已经达到 95 分；质量分数
仍须查看 package 内的质量报告、页面资产和人工复核结论。
