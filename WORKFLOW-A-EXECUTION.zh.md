# Workflow A 执行优先级与兜底

这份说明是给人和执行器看的运行入口。它补充 `workflows/document-to-markdown-assets/workflow.yaml`，不替代契约。

## 先说结论

Workflow A 不是“只跑夹具断言就算完成”。真实运行必须产生 `index.md`、`manifest.yaml`、来源视觉资产和处理/验收/失败报告；图片内容和复杂关系必须有实际复核证据。

当前轮次是已有 `index.md` 的页面级读者层修复试运行，不是 Workflow A source runner 的重新执行。
第 1 页已形成独立页面单元和 `document-temp.md` 派生预览；页面结构守卫与人工可读性检查通过，
但关系状态仍为 `needs-review`。第 2–5 页尚未处理，第 6–100 页尚未处理。

本轮不调用在线/局域网视觉模型、OCR、Apple Vision、mac96、Workflow A runner、smoke 或真实
package validator，也不生成新的真实运行 package。原始 OCR/Vision/bbox/置信度、页面图和
原始 manifest 保持只读。`adapters/page_markdown_units.py` 仅负责页面单元拆分、验证和确定性合并；
详细格式以页面契约和 Skill 为准。

日常触发只需要提供来源、输出目录和页码，不需要填写内部 Workflow 名称或重复输出格式：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

执行器会在 stdout 给出 JSON 摘要，并在资产包内写入
`reports/validation-result.json`。这个独立验收结果才是“是否正确”的判断依据。

## 稳定入口与优先级

在候选比较重跑和 A1–A7 转换前，入口会先执行 `execution_preflight` 横向门禁。它会
把可廉价确定的失败（来源/格式/页码/容器/渲染器/输出冲突/候选一致性）挡在高成本阶段
之前，并保留 `evidence/preflight.json`；它不能替代 smoke、阶段证据或独立验收。
详见 [执行前风险预检](references/execution-preflight.zh.md)。

后续 execute 阶段由 dispatcher 读取 `workflow-registry.yaml` 和 `workflows/document-to-markdown-assets/workflow.yaml`，记录 A1-A7；本轮不启动它。一次未来运行只允许一个 `source`：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

dispatcher 只接受显式页码；`--pages all` 在 bounded 验证中会被拒绝。旧的
`archive/legacy-adapters/workflow_a_runner.py` 仍是显式兼容入口，不代表存在另一个独立 Workflow Engine。

### 多文件生产入口

多个 PDF/PPTX/图片不要逐个手工启动，也不要把多个来源合并为一个 package。使用批处理编排器：

```bash
python3 adapters/workflow_a_batch.py --legacy-compat \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

编排器按 source 建立可恢复队列；generic execute 提升后，每个 source 才能调用 active
source runner 生成 `<batch>/packages/<source-hash>-<type>/` 独立包，并在 `<batch>/run-record.yaml`
汇总状态、attempt、命令、包路径和独立验收结果。本轮不启动真实转换。默认串行；`auto` 在没有可验证的
mac96/Ollama 容量信号时仍解析为一个 worker。只有明确配置
`WORKFLOW_A_MAC96_CONCURRENCY=N` 或 `execution.mac96_capacity_hint: N` 后，才会放大到
有限并发。HTTP 可达、模型列表存在或队列已接收都不等于并发容量证明。

长 PDF 仍按页独立请求和落盘证据；`context-mode=auto` 只在页面缺少清晰标题时附带
有限邻页文字提示，`independent` 可强制页隔离，`neighbor` 可显式使用限定窗口。邻页提示
只用于语义承接，不成为本页事实，原生文字和页面资产仍按页保留。配置、恢复和输出契约见
[多文件队列配置](references/workflow-a-batch-config.zh.md) 与
`contracts/workflow-a-batch.yaml`。

### 页面修复阶段门

已有 Markdown 的读者层修复不使用“处理完全部页面再统一验收”的路径：

1. 第 1 页：只读取第 1 页单元和第 1 页图；页面结构守卫通过后才能解锁下一阶段。
2. 第 2–5 页：按显式页码建立小批量页面单元；完成页面结构守卫并人工查看后，才能解锁第 6–100 页。
3. 第 6–100 页：本轮不启动。第 1 页通过不能替代第 2–5 页证据。

任何失败或 `needs-review` 的阶段都不能解锁后续页面。合并只按页面编号升序拼接，
不重新推断、摘要、翻译或补写文字；原始页面图链接逐字保持不变；`document-temp.md`
只能作为派生预览。

### 三个适配器怎么协作

| 组件 | 人工应如何理解 | 机器边界 |
|---|---|---|
| `adapters/workflow_a_source_runner.py` | 来源无关的 active deterministic 入口 | `--plan-only` 或不调用 legacy 的 `--execute`，输出独立 package |
| `adapters/workflow_a_dispatcher.py` | bounded 编排器 | 读取 registry 和 `workflow.yaml`，仍保持显式页码与 plan-only 边界；本轮未切换生产 dispatcher |
| `adapters/workflow_a_candidate_compare.py` | 同一逻辑文档的 PPTX/PDF 候选比较器 | 只生成 `candidate-comparison.json`，分别记录 `semantic_source`、`visual_source`、`preferred_source` 和差异证据；不生成业务 package |
| `archive/legacy-adapters/workflow_a_runner.py` | 单一来源的 legacy/document-profile 兼容执行器 | 仅在明确兼容/恢复场景使用；不属于 active dispatcher 路径 |

因此，candidate comparison 是来源选择证据，source runner 是 active plan 边界，dispatcher
是可观测编排边界，legacy runner 只是兼容遗留；四者不是四个 Workflow，也不能把两个候选来源合并为一个输入。

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

3. **统一页面视觉理解：`vision-page-analysis`**
   - 默认对当前单图使用 `mode=full`、`pass_strategy=single`，一次请求同时输出 Markdown 和结构化视觉分析。
   - 对关系密集、图表复杂、颜色/空间编码重要或明确要求高置信度的页面，显式使用
     `pass_strategy=dual`，分别得到专门的 `text` 与 `layout` 结果。
   - 运行记录必须包含：输入 SHA-256、模式、请求策略、实际请求次数、模型、请求时间、结构化输出和错误信息。
   - mac96 成功不是“看过图片”的口头说明，必须有 `evidence/vision-page-analysis.json`、请求/响应和
     `evidence/workflow-execution.json`。

4. **文字层降级**
   - 仅在统一 Skill 的文字层不可达、超时、模型不支持或输出不可解析时，才使用当前对话视觉模型或 Apple Vision OCR。
   - 直接使用同一张当前页面图或区域图，不读取其他图片、OCR JSON、bbox 或相邻页面。
   - OCR 只产生候选文字和置信度；保留原生层、mac96 层、当前对话层与 OCR 层的差异，不能补充视觉关系。
   - 运行记录必须包含 Apple Vision 引擎、语言、版本、fallback 原因和输入资产。

6. **提取链终态**
   - 任一路径返回非空、可解析 Markdown：继续 A4 reconciliation，并记录实际 provider。
   - mac96、当前对话在线视觉模型与 Apple Vision OCR 都失败：保留视觉资产和失败证据，状态为
     `failed/unavailable`，停止该提取链。
   - `needs-review` 只用于真实冲突、关系未裁决或人工判断项，不是任何模型/OCR 失败后的自动下一步。
   - 不得把“没有报错”“图片文件存在”或“夹具断言通过”写成图片内容提取完成。

7. **Markdown 固定阅读顺序**
   - 每页按“标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 来源与不确定性 → 【需要保留的】原始页面图/按需复杂区域图/嵌入图片 → 待核对/未知形式”输出。
   - 图片不得置于正文文字之前；这只调整阅读顺序，不减少视觉资产保留要求。
   - A4 必须记录实际复核范围；真实失败、冲突或未裁决关系才进入 `needs-review`。模型没有返回可选 `unknown_forms` 字段、未执行开放集合扫描或整页图已足够，不能单独制造 `needs-review`。

## 结果判定和下一步

- `passed`：独立验收器重新读取资产包后通过。可以阅读 `index.md`，并把 `assets/` 作为已验收来源资产使用。
- `needs-review`：资产和证据已保留，但存在视觉复核、关系、渲染或完整性问题。先查看 `reports/validation-result.json`、`reports/acceptance-report.yaml` 和 `evidence/`，不能当作已完成结果。
- `failed`：输出缺失、来源指纹不一致、资产损坏或独立验收失败。不能交给后续流程。

runner 自报的生成状态不等于最终验收状态。真实运行应同时检查：

```text
stdout JSON.status == passed
reports/validation-result.json.status == passed
```

若两者不一致，以独立验收器结果为准。

命令退出码：`0` 表示 `passed`，`1` 表示已生成但需要复核或验收失败，`2` 表示输入或参数错误。

## 业务结果与测试结果

正式业务结果只能是：

```text
<output_root>/<package-name>/
├── index.md
├── manifest.yaml
├── assets/                 # 页面图、嵌入媒体、按需区域图，统一扁平保存
└── reports/
    ├── processing-report.yaml
    ├── acceptance-report.yaml
    └── failure-report.yaml
```

正式包和读者导出都使用扁平 `assets/`；页面图、嵌入媒体和复杂区域图通过 manifest 的
`asset_kind`、来源位置和 occurrence 区分，不通过物理子目录区分。文件名只使用
`<short-semantic-slug>.<ext>`；只有同一目录中真实内容冲突时才追加 `-02`、`-03`。
页码、哈希、`page-render`、`original-page`、`o01` 等追溯字段留在 manifest/evidence，
不进入读者文件名。

`tests/runs/<run-id>/` 是测试记录。其 `outputs/` 只能保存 fixture-sample 或真实运行产生的临时副本，并必须在 `test-report.yaml` 中区分：

- `generated_business_outputs`：只有真实解析器实际生成时才允许填写；
- `fixture_sample_outputs`：手工预期样例，不是解析器结果；
- `real_business_result_package`：真实业务结果路径，没有就写 `null`。

对第 3、4 页的 bounded 运行，入口保持为上面的 dispatcher 命令；对已经生成的真实
package，必须单独执行：

```bash
python3 tests/validate_real_output_root.py \
  "/absolute/path/output-root/<package-name>"
```

`tests/validate_workflow_package.py`、fixture contract 和 pytest 不能替代这个真实包
验收器。validator 通过表示契约、完整性、来源和证据链满足门槛，不自动证明视觉/语义
质量达到 95 分；仍须读 `reports/quality-report.yaml`、页面资产和人工复核证据。

## Fixture 与生产运行边界

第 24、25 页夹具仍只证明输入范围、关键文字、视觉基准图和样例链接；它不替代
生产 runner。生产运行由 `--pages` 决定，`all` 才能声称覆盖当前来源全部页面。

## 禁止的捷径

- 不用 `fixture-sample` 冒充真实解析结果。
- 不把 `mac96` 写成可选但实际完全不尝试。
- 不在 mac96 失败后直接结束而不运行当前对话在线视觉模型。
- 不在当前对话在线视觉模型失败后直接结束而不运行 Apple Vision OCR。
- 不把 mac96、当前对话视觉模型或 OCR 候选当作未经 reconciliation/独立验收的确定事实。
- 不把 GPT-image-2 纳入 Workflow A。
