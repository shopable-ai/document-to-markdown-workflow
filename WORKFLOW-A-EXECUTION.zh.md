# Workflow A 执行优先级与兜底

这份说明是给人和执行器看的运行入口。它补充 `workflows/document-to-markdown-assets/workflow.yaml`，不替代契约。

## 先说结论

Workflow A 默认是读者层转换：真实运行产生 `index.md`（关键点 Markdown），并只在页面存在 Markdown 无法替代的语义视觉时附带读者图片。纯文本结果允许没有 `assets/`；不会默认产生 `manifest.yaml`、JSON/YAML 证据、`reports/`、`page-units/` 或其他工程文件。

需要完整资产登记、处理报告和独立工程证据时，必须显式加 `--engineering-output`；这是一条诊断/追溯路线，不是默认交付。

当前轮次是已有 `index.md` 的页面级读者层修复试运行，不是 Workflow A source runner 的重新执行。
第 1 页已形成独立页面单元和 `document-temp.md` 派生预览；页面结构守卫与人工可读性检查通过，
但关系状态仍为 `needs-review`。第 2–5 页尚未处理，第 6-total_pages 页尚未处理。

本轮不调用在线/局域网视觉模型、OCR、Apple Vision、mac96、Workflow A runner、smoke 或真实
package validator，也不生成新的真实运行 package。原始 OCR/Vision/bbox/置信度、页面图和
原始 manifest 保持只读。`adapters/page_markdown_units.py` 仅负责页面单元拆分、验证和确定性合并；
详细格式以页面契约和 Skill 为准。

日常触发只需要提供来源、输出目录和页码，不需要填写内部 Workflow 名称或重复输出格式：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

执行器会在 stdout 给出 JSON 摘要；默认输出目录包含 `index.md`，必要时再包含 `assets/` 中被引用的语义图片。

## 稳定入口与优先级

在候选比较重跑和 A1–A7 转换前，入口会先执行 `execution_preflight` 横向门禁。它会
把可廉价确定的失败（来源/格式/页码/容器/渲染器/输出冲突/候选一致性）挡在高成本阶段
之前，并保留 `evidence/preflight.json`；它不能替代 smoke、阶段证据或独立验收。
详见 [执行前风险预检](references/execution-preflight.zh.md)。

普通单一来源 execute 不经过 dispatcher；固定入口只加载当前 source 和 generic profile，直接生成一个独立 package：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all \
  --execute
```

需要先检查参数和路由但不读取 source 时，才显式加 `--plan-only`。它不会启动 dispatcher、batch、legacy
或 optimized runner。只有显式候选比较或 bounded A1-A7 envelope 需求才使用 dispatcher：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

dispatcher 只接受显式页码；`--pages all` 在 bounded 验证中会被拒绝。旧 document-profile runner 已删除；
active Workflow 只允许来源无关的 source runner，不存在隐藏兼容入口。

### 多文件生产入口

多个 PDF/PPTX/图片不要逐个手工启动，也不要把多个来源合并为一个 package。使用批处理编排器：

```bash
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

编排器当前只做参数和契约检查并 fail-closed；普通单源不经过它。未来若提升 batch execute，每个 source 才能调用 active
source runner 生成 `<batch>/packages/<来源名称>-<时间>/` 独立包，并在 `<batch>/run-record.yaml`
汇总状态、attempt、命令、包路径和独立验收结果。本轮不启动真实转换。默认串行；`auto` 在没有可验证的
mac96/Ollama 容量信号时仍解析为一个 worker。只有明确配置
`WORKFLOW_A_MAC96_CONCURRENCY=N` 或 `execution.mac96_capacity_hint: N` 后，才会放大到
有限并发。HTTP 可达、模型列表存在或队列已接收都不等于并发容量证明。

长 PDF 的程序性渲染与页面理解在同一个脚本内并行：脚本启动后由渲染生产者用 `pdftoppm`
按受控批次持续生成页面图；某一页图片可读并复制到 `assets/` 后，立即交给该页的视觉请求、
文字转写和 Markdown 修复，后续页面继续在后台生成。`evidence/rendering.json` 必须记录
`rendering_mode=producer-consumer-incremental-assets` 和
`page_processing_barrier=current-page-asset-ready-before-current-page-processing`。已有且可读的
页面图直接复用，不重复渲染；因此不需要另起脚本，也不会让截图生成和页面理解互相等待。
长 PDF 仍按页独立请求和落盘证据；`context-mode=auto` 只在页面缺少清晰标题时附带
有限邻页文字提示，`independent` 可强制页隔离，`neighbor` 可显式使用限定窗口。邻页提示
只用于语义承接，不成为本页事实，原生文字和页面资产仍按页保留。配置、恢复和输出契约见
[多文件队列配置](references/workflow-a-batch-config.zh.md) 与
`contracts/workflow-a-batch.yaml`。

### 页面修复阶段门

已有 Markdown 的读者层修复不使用“处理完全部页面再统一验收”的路径：

1. 第 1 页：只读取第 1 页单元和第 1 页图；页面结构守卫通过后才能解锁下一阶段。
2. 第 2–5 页：按显式页码建立小批量页面单元；完成页面结构守卫并人工查看后，才能解锁第 6-total_pages 页。
3. 第 6-total_pages 页：本轮不启动。第 1 页通过不能替代第 2–5 页证据。

任何失败或 `needs-review` 的阶段都不能解锁后续页面。合并只按页面编号升序拼接，
不重新推断、摘要、翻译或补写文字；reader image 处置和已有原始页面图链接逐字保持不变；`document-temp.md`
只能作为派生预览。

### 知识库低价值页清洗（可选下游步骤）

这一步不属于 Workflow A 的 A1–A7，也不与 A5 的页眉页脚噪声清洗合并。只有用户明确要把
电子书页面单元用于 AI 知识库或读者集合时，才在全部页面生成、分组复制或并发结果汇合完成后执行：

1. 优先复制出知识库 `page-units/` 工作副本；若直接清理 engineering package，该目录不再代表完整工程输出。
2. 逐页确认候选。目录、版权/出版说明、推广领取、书目推荐、重复封面和空白装饰页可以候选删除；前言、导读、术语表、附录、参考文献和主题索引默认复核。
3. 使用 [`prune-low-value-ebook-pages`](skills/prune-low-value-ebook-pages/SKILL.md) 先生成哈希锁定预览计划。计划必须覆盖完整页面计数，并为每个候选保存页码和理由，但不复制正文。
4. 只有完整目录与候选哈希均未漂移时，才能按同一计划删除。不能把某本书的“前 N 页”复用成其他书的规则。
5. 原 PDF、页面图、OCR/bbox、Vision JSON、manifest 和报告保持不变；保留 page-units 不得改写。
6. 删除后从保留页重新生成 `index.md`、`total.md` 或集合包。旧合并文件仍含已删除页时，不得交给知识库。

机器契约见 [`contracts/ebook-page-pruning.yaml`](contracts/ebook-page-pruning.yaml)。`applied` 只证明
指定 page-units 工作副本完成整页筛选，不改变原运行的 `passed`、`needs-review` 或 `failed` 状态。

### 三个适配器怎么协作

| 组件 | 人工应如何理解 | 机器边界 |
|---|---|---|
| `adapters/workflow_a_source_runner.py` | 来源无关的 active deterministic 入口 | 默认 `--execute`，显式 `--plan-only` 只做诊断，输出独立 package |
| `adapters/workflow_a_dispatcher.py` | bounded 编排器 | 仅在显式候选/envelope 场景读取 registry 和 `workflow.yaml`，不是普通单源前置步骤 |
| `adapters/workflow_a_candidate_compare.py` | 同一逻辑文档的 PPTX/PDF 候选比较器 | 只生成 `candidate-comparison.json`，分别记录 `semantic_source`、`visual_source`、`preferred_source` 和差异证据；不生成业务 package |
| 旧 document-profile runner | 已删除的历史实现 | 不参与当前 Workflow；历史证据只能按现有 package 直接审计 |

因此，candidate comparison 是可选来源选择证据，source runner 是唯一 active 执行边界，dispatcher
是可选的可观测编排边界；legacy runner 已退出 active 路由，四者不是四个 Workflow，也不能把两个候选来源合并为一个输入。

PPTX 与 PDF 即使名称相近也分别生成 package。只有用户明确声明它们是同一逻辑
文档候选时，才可以另行执行来源候选比较；普通单一来源报告必须写
`source_selection: single-source`。

候选比较采用混合保真规则：同名文字存在时，PPTX XML 优先作为语义和数字资产来源；
PPTX Office 渲染与 PDF 页面截图必须做代表页比较。若截图差异超过阈值，或 PPTX
渲染出现文字不可见、错位、遮挡等异常，则 PDF 页面图作为主视觉证据，PPTX 的 XML、
Office 渲染和异常诊断仍保存在比较证据中。差异未达到阈值时，才可继续使用 PPTX
页面图作为视觉候选；不能只按文件名、文字相似度或 runner 的 `complete` 选择。

1. **原生结构提取**
   - PPTX：XML 文字、形状、分组、连线、坐标、层级和媒体关系。
   - PDF：文本块、边界框、阅读顺序和页面布局。
   - DOCX：历史扩展 Skill 可描述 OOXML 提取，但当前 active runner 未纳入生产输入，必须先经 capability registry 和 preflight 放行。
   - 原生结果是事实层，不能被 OCR 或视觉模型静默覆盖。

2. **来源视觉资产落盘**
   - 先保存整页渲染图；只有整页图不足以支持关系复核时才生成 regions，按来源页实际引用保留 embedded 资产。
   - 每个资产必须进入 manifest，并可回到来源页、区域或对象。

3. **视觉模型候选路线：`vision-page-analysis`**
   - `mode=full`、`pass_strategy=single` 是单个视觉模型适配器的调用方式，不代表 mac96 是文档级默认路线。
   - 对关系密集、图表复杂、颜色/空间编码重要或明确要求高置信度的页面，显式使用
     `pass_strategy=dual`，分别得到专门的 `text` 与 `layout` 结果。
   - 运行记录必须包含：输入 SHA-256、模式、请求策略、实际请求次数、模型、请求时间、结构化输出和错误信息。
   - 任一视觉模型成功不是“看过图片”的口头说明，必须有 `evidence/vision-page-analysis.json`、请求/响应和
     `evidence/workflow-execution.json`。

4. **文字识别、Markdown 结构重建与广告策略**
   - Apple Vision OCR 加确定性结构重建、mac96 小视觉模型和 mac96 30B 使用同一真实页面集校准；选择和升级依据页面质量守卫，不设固定顺序。
   - 每个模型直接使用同一张当前页面图或区域图，不读取其他图片、OCR JSON、bbox 或相邻页面；OCR 结构重建器只读取当前页原始 OCR JSON。
   - OCR 只产生不可变的候选文字、坐标和置信度；结构重建独立生成标题、自然段、目录和列表，不能补充视觉关系或臆造跨页内容。
   - 微信号、二维码附近说明、推广语等可见文字在前两层全部保留；广告分类、标记、隐藏或删除属于独立后置策略，本轮默认 `preserve-only`。
   - 运行记录必须包含引擎、模型、参数、提示词版本、输入资产、耗时、资源、质量结论和升级原因。

6. **提取链终态**
   - 任一路径返回非空、可解析 Markdown 后仍须运行结构质量守卫；只有守卫通过才继续 A4 reconciliation。
   - 所有已校准且已授权候选都失败：保留视觉资产和失败证据，状态为
     `failed/unavailable`，停止该提取链。
   - `needs-review` 只用于真实冲突、关系未裁决或人工判断项，不是任何模型/OCR 失败后的自动下一步。
   - 不得把“没有报错”“图片文件存在”或“夹具断言通过”写成图片内容提取完成。

7. **Markdown 固定阅读顺序**
   - 每页按“标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 来源与不确定性 → 【需要保留的】原始页面图/按需复杂区域图/嵌入图片 → 待核对/未知形式”输出。
   - 图片不得置于正文文字之前；这只调整阅读顺序，不减少视觉资产保留要求。
   - A4 必须记录实际复核范围；真实失败、冲突或未裁决关系才进入 `needs-review`。模型没有返回可选 `unknown_forms` 字段、未执行开放集合扫描或整页图已足够，不能单独制造 `needs-review`。

## 结果判定和下一步

- `passed`：默认 reader-only 结构守卫通过；标题层级、正文分段、目录、列表、页眉页脚、标点、跨页连续性和异常内容保留的适用维度均有复核证据，且任何 `assets/` 图片引用均可解析。
- `needs-review`：关键点、截图或链接需要人工复核；默认包不写工程报告。显式 engineering-output 时再查看 `reports/validation-result.json`、`reports/acceptance-report.yaml` 和 `evidence/`。
- `failed`：输出缺失、来源指纹不一致、资产损坏或独立验收失败。不能交给后续流程。

runner 自报的生成状态不等于最终验收状态。真实运行应同时检查：

```text
stdout JSON.status == passed
默认包：index.md 非空；若有图片链接则全部存在且 assets/ 无未引用文件，纯文本结果可无 assets/
显式 engineering-output：reports/validation-result.json.status == passed
```

显式 engineering-output 若两者不一致，以独立验收器结果为准。

命令退出码：`0` 表示 `passed`，`1` 表示已生成但需要复核或验收失败，`2` 表示输入或参数错误。

## 业务结果与测试结果

正式业务结果只能是：

```text
<output_root>/<package-name>/
├── index.md
└── assets/                 # 可选；只保留 index.md 实际引用的语义图片
```

当前 active runner 默认把 `<package-name>` 生成为 `来源名称/`，例如
`50个相处话术案例/`；只有与现有目录冲突时才追加 `-02`、`-03`。来源哈希继续保留在工程元数据中，
不再默认出现在目录名里。

默认包需要图片时使用扁平 `assets/`；纯文本包不创建该目录，也不生成未被 Markdown 引用的嵌入媒体或复杂区域图。文件名只使用
`<short-semantic-slug>.<ext>`；只有同一目录中真实内容冲突时才追加 `-02`、`-03`。
页码、哈希、`page-render`、`original-page`、`o01` 等追溯字段不进入默认读者包。

`tests/runs/<run-id>/` 是测试记录。其 `outputs/` 只能保存 fixture-sample 或真实运行产生的临时副本，并必须在 `test-report.yaml` 中区分：

- `generated_business_outputs`：只有真实解析器实际生成时才允许填写；
- `fixture_sample_outputs`：手工预期样例，不是解析器结果；
- `real_business_result_package`：真实业务结果路径，没有就写 `null`。

对第 3、4 页的 bounded 运行，入口保持为上面的 dispatcher 命令；默认读者包不需要再写
工程报告；如使用 `--engineering-output` 生成完整 package，再单独执行：

```bash
python3 tests/validate_real_output_root.py \
  "/absolute/path/output-root/<package-name>"
```

`tests/validate_workflow_package.py`、fixture contract 和 pytest 不能替代人工检查。
默认路线不能只确认 `index.md` 非空；还要确认扫描页语义文字覆盖、Markdown 结构、异常文字保留和原始证据链。有图片时再确认链接存在且 `assets/` 没有未引用文件。纯文本页的 `reader_image_policy=omit` 必须有视觉 JSON 明确依据，二维码不解码、不裁剪、不作为图片；视觉/语义质量仍须人工复核。

## Fixture 与生产运行边界

第 24、25 页夹具仍只证明输入范围、关键文字、视觉基准图和样例链接；它不替代
生产 runner。生产运行由 `--pages` 决定，`all` 才能声称覆盖当前来源全部页面。

## 禁止的捷径

- 不用 `fixture-sample` 冒充真实解析结果。
- 不把任何一条候选路线写成固定默认，而没有同页真实质量、耗时和资源比较。
- 不因模型参数更大、输出非空或请求成功就跳过结构质量守卫。
- 不在提取或结构重建阶段删除、隐藏或改写疑似广告文字。
- 不把 mac96、当前对话视觉模型或 OCR 候选当作未经 reconciliation/独立验收的确定事实。
- 不把 GPT-image-2 纳入 Workflow A。
