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

当前 active 入口是来源无关的 `adapters/workflow_a_source_runner.py`，单一来源默认直接执行确定性转换；
`--plan-only` 只是显式的无读取诊断模式：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "<SOURCE>" \
  --output-root "<OUTPUT_ROOT>" \
  --pages all \
  --execute
```

执行器只加载自身、注册的固定 preflight helper、generic profile 和当前 source；不会为了普通单源转换读取或调用
dispatcher、batch、legacy runner、optimized runner 或其他历史脚本。视觉模型、OCR、语言修复和质量审计
仍是独立的显式后续门，不会被隐式串入本入口。
本轮只对 `tests/fixtures/` source 做了转换和完整测试；fixture 输出仍是测试证据，不是生产业务结果。
旧 document-profile runner 已删除并从当前 Workflow 排除；其固定文档标题、页眉过滤、OCR 标题区域、PDF DPI、
mac96/SSH/Ollama 和旧 Markdown 模板不得复制到新 runner。
详细迁移表见 [来源无关 Runner 设计](../../references/source-runner-design.zh.md) 和
[遗留代码状态](../../references/legacy-code-status.zh.md)。

正式入口在候选比较和转换前先执行横向 `execution_preflight`。它只检查可廉价确定的
风险（来源、格式、页码、容器、渲染器、输出碰撞和候选一致性），并写入
预检只作为内部执行门禁，不会写入默认输出目录；默认转换完成以 `index.md` 与其截图链接可用为准。规则和专家推进方式见
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
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

普通单一来源不要调用 dispatcher 或 batch；它们只用于显式候选比较/多文件编排实验。单源固定入口就是上面的
`workflow_a_source_runner.py --execute`。只有在已经准备好候选比较证据、确实需要 A1-A7 envelope 时，才使用下面的
bounded dispatcher：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

这里的机器关系是：`workflow_a_dispatcher.py` 只负责显式 bounded envelope，不是普通转换的前置步骤；
`workflow_a_candidate_compare.py` 只在显式声明同一逻辑文档候选时生成比较证据；
`workflow_a_source_runner.py` 是唯一 active source runner，负责生成来源无关 package；旧 document-profile runner
已删除，不能从 active 路径或批处理入口恢复调用。

dispatcher 要求显式页码，阻止 bounded 验证意外扩展为 `--pages all`。PDF、PPTX
和图片必须分别调用，不能因为文件名相近就合并。generic runner 通过 profile 选择
各格式方案；旧 runner 参数不再接受，不得作为新 source 的默认入口。

多个来源当前仍不支持生产批量执行；`adapters/workflow_a_batch.py` 会 fail-closed，不得把它当成普通单源入口。
它是上层队列编排器，不改变
单文件 runner 的来源隔离：每个 source 都有独立 package 和 `index.md`；只有需要语义图片时才有读者层 `assets/`，只有显式工程模式才有 manifest 和报告。批次根目录另写 `run-record.yaml`。默认串行，只有有
明确 mac96/Ollama 容量证据时才配置有限并发；长文档按页请求，使用 `--context-mode`
控制严格隔离或受限邻页提示。完整配置、恢复和判定见根目录的
[多文件队列配置](../../references/workflow-a-batch-config.zh.md)。

### 长文档 smoke、分批与恢复

长 PDF 的生产批量路线仍要求 smoke/checkpoint；普通单源 deterministic execute 会按用户的页码选择直接生成 package，
并在报告中明确视觉/OCR/质量门的 `not-run` 或 `needs-review`，不会伪造 smoke 通过。

PDF 的默认生产入口现在是页面级混合路线。文档级预检只决定是否需要进入混合控制器；控制器对每一页固定生成
`native-only`、`native-plus-visual` 或 `visual-only` 路线记录。原生正文总是先提取或复用；“如图”“表 1”
“矩阵”“流程图”等文字只建立候选，不会单独触发模型，必须结合 `pdfimages -list` 图片对象及面积、原生 bbox
大块空白、`pdfplumber.find_tables()` 或候选页本地渲染结构。纯文字页不渲染、不调用视觉模型；“如图”但
没有图片对象的页保留原生正文并标记 `needs-review`，不得据此臆造图片内容。

原生正文完整且视觉信号得到佐证时，路线为 `native-plus-visual`：同一 `page-units/pNNN.md` 先写原生正文，
再追加图片说明、表格结构或图示关系，视觉输出不能覆盖原生正文。原生文字为空/不足且有图片、表格或渲染结构
佐证时才进入 `visual-only`。单页失败会登记到路线记录与 `reports/page-completion.json`，然后继续后续页；最终只有
所选页无缺页、重页、额外页且每页路线所需证据完整时才能为 `passed`。同一来源断点续跑必须校验 SHA-256、
页码、已通过路线记录、路线证据与唯一页面标记，不能只凭文件存在复用。

运行期间只在阶段边界追加 `evidence/progress.jsonl`；它是短的阶段账本，不再重复写入每一页的完整证据路径。mac96 路径把最新页状态覆盖写入 `evidence/progress-current.json`，把逐页详情写入 `evidence/progress-pNNN.json`，并把当前阶段、页码、状态、耗时和下一步打印到 stderr。读取运行状态时优先读取 `progress-current.json`；只有调查某一页时才读取对应的 `progress-pNNN.json`，不要把整个 `progress.jsonl` 载入上下文。这不等于已经证明 15 秒实时心跳或无进展硬停止。PDF 页面图只为视觉路线页和需要本地结构佐证的候选页生成；某页图片可读后立即进入该页视觉请求、转写和 page-unit 投影，纯文字页直接保留原生 Markdown。全部选定 page-unit 或失败占位就绪后，执行器先完成全页覆盖报告，再跨页清洗明确噪声。已有且经来源与路线校验的页面图/页面单元直接复用，不需要另起脚本或重复渲染/调用模型。调度器只对当前失败页停止该页的下游视觉步骤，不阻断后续页；允许的 OCR/视觉降级必须作为当前页面路线的明确 fallback 记录。旧恢复脚本和夹具脚本的边界见根目录 [`references/legacy-code-status.zh.md`](../../references/legacy-code-status.zh.md)。

smoke 通过后，用 page-batch 编排器按小批次运行。每批都使用新 package 目录并写 checkpoint，不能覆盖既有真实运行证据：

注意：smoke 通过只证明页面资产、提取候选和 Markdown 结构门禁通过；语言质量修复现在已经有局域网优先适配器，
只需保留每页 `evidence/markdown-content-repair-pNNN.json` 和结构守卫结果，页面修复不另起 pytest
或独立 validator 测试阶段，才能声称文字已按当前页边界整理。
局域网不可用或结构守卫未通过时，package 仍是 `needs-review`，不能把未执行的在线回退或人工修复写成完成。

```bash
python3 adapters/workflow_a_batch.py \
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
2. 第 2–5 页小批量必须经过人工查看和页面结构守卫，之后才允许第 6–total_pages 页。
3. 页面图可以在页面修复前批量生成；修复一页时只读取该页页面单元和该页页面图。OCR JSON、bbox、置信度、逐页 Vision JSON、页面图和原始 `manifest.yaml` 保持只读。
4. 页面单元按固定字段组织：语义标题、正文、列表/图示文字、表格与公式、解读、举例与思考、模型结构、已确认的关系说明、可选原始页面图和待核对项。
5. 合并器按页面编号升序逐字拼接页面单元，保留 reader image 处置和已有页面图链接，不重新推断、摘要、翻译或补写文字。

`document-temp.md` 是当前已通过页面单元的可审阅派生预览，不能覆盖或替代最终
`index.md`，也不能成为新的事实来源。未确认的箭头、层级、流程、矩阵或颜色关系只能保留在
`Unknown`/`needs-review` 待核对项中。

### 页面视觉理解

A4 使用 [`vision-page-analysis`](../../skills/vision-page-analysis/SKILL.md)，通过 `ssh mac96`
读取当前一张页面图或区域图。默认 `mode=full`、`pass_strategy=single`，一次请求同时输出：

- `text.markdown`：图片中实际出现的文字、公式和标签；
- `visual.visual_analysis`：图表、区域、箭头、分组、层级、比较、颜色编码和未知形式。
- `visual.visual_analysis.page_content_type/layout_dependency/semantic_visuals/ignored_elements/reader_image_policy`：决定读者 Markdown 是否还需要页面图。纯文本且 Markdown 足够时为 `omit`；二维码只进入 ignored elements，不解码、不裁剪、不作为图片。

关系密集或高风险页面可以显式传 `pass_strategy=dual`，分别执行 `text` 与 `layout` 两个专门 pass。
适配器是 [`adapters/mac96_vision_page_analysis.py`](../../adapters/mac96_vision_page_analysis.py)，
统一证据写入 `evidence/vision-page-analysis.json`，记录输入指纹、模式、请求策略、实际请求次数、
模型、transport、请求时间、文字层、视觉层和诊断。文字层失败时才进入当前对话视觉模型或
Apple Vision OCR；这些 fallback 不能伪造视觉结构完成。旧的 `vision-text-transcription` 和
`mac96-visual-review` 仅作为兼容别名保留。

分页执行器会为每页写入 `evidence/page-routes/pNNN.json` 和 `page-units/pNNN.md`；只有
`native-plus-visual`/`visual-only` 才要求对应视觉证据。页面只有在来源、页码、路线记录、路线所需证据和
页面标记全部通过后才可复用；缺失 Markdown 可从当前已验证证据重建，只有路线确实需要视觉且视觉证据无效时
才重试当前页模型。因此日常提示词只需说明来源、输出根目录和页码，不需要重复内部路由或 page-unit 规则。

`mac96_pdf_vision_runner.py` 和 `mac96_pdf_vision_resume.py` 都接受
`--noise-profile <profile.yaml>`。两条路径会等待全部选定 page-unit 就绪，保存独立 scan 证据，确认输入哈希和删除计划未变化后再 apply，最后才逐页调用语言修复。

### 页面页眉页脚噪声清洗

`page-units/pNNN.md` 生成后，先使用
[`clean-page-markdown-noise`](../../skills/clean-page-markdown-noise/SKILL.md) 和
[`page-markdown-noise-cleanup.yaml`](../../contracts/page-markdown-noise-cleanup.yaml)，再进入语言模型内容修复。

这一步是本地确定性文本处理，不打开页面图，也不读取 OCR JSON、bbox、置信度、Vision JSON 或
`manifest.yaml`。默认先 scan：统计跨页重复的页首/页尾短行、OCR 拼写变体、原始页面图链接前页码和
首尾 `---`，记录每个文件输入哈希并输出逐行拟删除清单。不同文档的页眉文字通过运行级 exact、regex、
fuzzy profile 扩展，不把某本书或某个品牌的固定字符串写入通用 Workflow。

只有页首/页尾安全区或由至少两个独立模板规则组成的相邻模板块可以自动删除。profile 命中但落在正文中的
独立同名行必须保留为 `suspects`；页码只在原始页面图链接前删除，正文编号保持不变；`---` 只在其他
允许删除项移除后成为第一个或最后一个非空行时删除。apply 前重新检查全部输入哈希，只原子重写实际命中
文件，写后必须证明所有文件非空、保留行可逐字重建且页面图路径行没有变化。

```bash
python3 adapters/page_markdown_noise_cleanup.py \
  --page-units "/absolute/path/page-units" --pages all \
  --profile references/reader-noise-profile.yaml \
  --evidence "/absolute/path/evidence/page-markdown-noise-cleanup.json"

python3 adapters/page_markdown_noise_cleanup.py \
  --page-units "/absolute/path/page-units" --pages all \
  --profile references/reader-noise-profile.yaml --apply \
  --evidence "/absolute/path/evidence/page-markdown-noise-cleanup.json"
```

### 页面 Markdown 修复

确定性页眉页脚清洗完成后，A5 才进入
[`markdown-content-repair`](../../skills/markdown-content-repair/SKILL.md)。这一步不是再次看图：
它只把当前页 Markdown 发送给局域网 `mac96` 上的语言模型，修复断词、错误换行、段落粘连、
列表/标题结构和局部标点。默认模型标识为 `qwen3.6:latest`，实际可由运行配置覆盖。

局域网结果先通过页面编号、reader image 处置、已有页面图链接、读者层字段和 `Unknown/needs-review` 关系边界检查，
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

默认输出契约见 [reader-markdown-output-layout.yaml](../../contracts/reader-markdown-output-layout.yaml)。纯文本结果只有 `index.md`；确有语义图片时目录为：

```text
<output_root>/<来源名称>-<时间>/
├── index.md
├── assets/
│   ├── <short-semantic-slug>.png
│   ├── <short-semantic-slug>-02.png
│   └── ...             # 仅保留 index.md 实际引用的语义图片
```

默认运行不会生成 `manifest.yaml`、`evidence/`、`reports/`、`page-units/`、`document-temp.md`、JSON 或其他工程过程文件；也不会提取未被 Markdown 引用的嵌入媒体或区域图。若要保留完整工程登记，显式加 `--engineering-output`，其输出改由 [markdown-asset-package.yaml](../../contracts/markdown-asset-package.yaml) 约束。
目录名优先保留来源标题，并追加本地时间戳；来源哈希仍保存在工程元数据里，不再默认暴露到目录名。

### Workflow A 与集合阅读包的命名边界

Workflow A 的默认转换包使用：

```text
index.md = 转换后的关键点 Markdown
assets/ = 可选；index.md 实际引用且 Markdown 无法替代的语义图片
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

### 可选的电子书知识库低价值页清洗

电子书用于 AI 检索知识库时，可在全部 `page-units/` 生成、分组复制或并发结果汇合完成后，
调用 [`prune-low-value-ebook-pages`](../../skills/prune-low-value-ebook-pages/SKILL.md)。它先生成
哈希锁定的预览计划，再按已明确批准的页码整页删除目录、版权/出版说明、推广领取、书目推荐
或空白装饰页。前言、导读、术语表、附录、参考文献和主题索引默认进入人工复核，不按位置自动删除。

该步骤是知识库/读者导出的可选后置环节，不属于 Workflow A 的 A1–A7 验收，也不改变原运行
状态。优先对复制后的知识库工作副本执行；若直接清理 engineering package 的 `page-units/`，
该目录不再代表全页工程输出。清洗只删除选定 `pNNN.md`，不会同步修改已存在的 `index.md`、
`total.md` 或集合包，因此这些下游文件必须从保留页重新生成。

### 读者层与工程层

| 层 | 读什么 | 不放什么 |
|---|---|---|
| 读者层 | `index.md`：标题、主题、正文、列表/表格、已裁决的关系说明和必要页面图链接 | bbox、对象 ID、模型调用、token、fallback、OCR 诊断或其他运行细节 |
| 工程层 | 默认不落盘；显式 `--engineering-output` 时才写入 `evidence/`、`manifest.yaml` 和 `reports/` | 不把工程证据当成读者正文 |

工程登记文件只在显式诊断模式下生成，不应复制进 `index.md`。

### Markdown 默认阅读顺序与形式选择

常见图文页面按“标题 → 主题/对比 → 内容要点、列表、表格或分区 → 图示说明（有已裁决关系时） → 原始页面图”输出。这个顺序是默认阅读 profile，不是所有 PDF、图片或其他文档的统一模板；文字型、表格/图表型、流程/矩阵型、低质量扫描、多栏或跨页内容应按实际结构选择 Markdown 表达。来源指纹、bbox、对象 ID、字体、渲染命令、OCR 候选、模型调用和未决诊断不得混入默认读者层；它们仅在显式 `--engineering-output` 模式下进入工程文件。

资产文件采用双名制：`file_name` 使用最短英文语义组成的稳定 ASCII 名称，
例如 `time-quadrants.png`；页码、稳定 ID、
来源指纹、SHA-256、asset kind、用途和 ordinal 默认不进入读者包，也不作为随机前缀或技术字段
暴露在文件名中。需要工程追溯时显式启用 `--engineering-output`。

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
4. **A4 文字识别、Markdown 结构重建与广告策略**：原生结构提取后，将 Apple Vision OCR 加
   确定性结构重建、mac96 小视觉模型和 mac96 30B 作为候选路线，用同一真实页面集比较后选择。
   每页依次保留不可变的原始文字证据、重建标题/自然段/目录/列表的 Markdown 候选，以及独立广告
   策略结果；提取和结构重建不得删除微信号、推广语或二维码附近说明。复杂结构或明确重建意图的
   资产，额外保存 visual reconstruction profile。
5. **A5 Markdown 组装与视觉资产语义筛选**：建立页面/章节结构、关系说明和视觉链接；只从读者层移除已被可靠文字覆盖且没有视觉增量的封面、目录或纯文字页面图，保留复杂图示和关系布局及其来源资产登记。
6. **A6 命名与来源登记**：处理语义命名、来源出现记录、内容去重和筛选后的资产登记。
7. **A7 资产包验收**：执行链接、完整性、追溯性和不确定性检查。

每个阶段的 Skill、路由、输出和证据文件以同目录的 [workflow.yaml](workflow.yaml) 为准。
PPTX 页面图必须来自 PPTX 自身的 Office 渲染；PDF 页面图必须来自原始 PDF 渲染。

如果沿用早期的 `$knowledge-map-image-to-md` 名称，仍然执行本 Workflow 的 A1-A7；
仅额外加载 [知识地图差异策略](../../references/knowledge-map-profile.zh.md)。该入口
不复制本说明中的阶段，也不改变 Workflow A 的正式输出布局。

## 成本与外部调用边界

- 默认使用本地解析和渲染；扫描图片文字没有固定识别优先级。Apple Vision OCR 加确定性结构重建、
  mac96 小视觉模型和 mac96 30B 必须按真实页面的标题层级、正文分段、目录、列表、页眉页脚、标点、
  跨页连续性、异常内容保留、耗时和资源证据比较后选路由。
- 每次尝试都必须写入运行证据；不可用、超时、不可解析或质量守卫失败时，只能升级到下一条已经
  校准且获得授权的候选路线。所有候选均失败时是 `failed/unavailable` 或有明确证据的 `needs-review`。
  非空 Markdown、SSH/HTTP 成功或模型清单不能代替结构质量证明。
- 当前对话在线视觉模型只允许读取当前这一张图片，不创建独立 HTTP/SSH 适配器；其他外部在线视觉模型
  仍不是默认兜底，只有用户另行明确授权时才可调用，并必须单独记录成本、输入范围和结果。
- 本 Workflow 不调用 GPT-image-2，不生成 PPTX、PDF 或 SVG。

## 失败处理

- 输入损坏或不支持：`source-invalid` / `unsupported-format`；
- 本地解析或渲染暂时失败：`extract-timeout` / `render-failed`，允许重试；
- Apple Vision OCR、mac96 小模型、mac96 30B 或明确授权的当前对话在线视觉模型不可用：保留原生结果和视觉资产，分别标记
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
