# Workflow A 执行优先级与兜底

这份说明是给人和执行器看的运行入口。它补充 `workflows/document-to-markdown-assets/workflow.yaml`，不替代契约。

## 先说结论

Workflow A 不是“只跑夹具断言就算完成”。真实运行必须产生 `document.md`、`manifest.yaml`、来源视觉资产和处理/验收/失败报告；图片内容和复杂关系必须有实际复核证据。

日常触发只需要提供来源、输出目录和页码，不需要填写内部 Workflow 名称或重复输出格式：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

执行器会在 stdout 给出 JSON 摘要，并在资产包内写入
`reports/validation-result.json`。这个独立验收结果才是“是否正确”的判断依据。

## 稳定入口与优先级

生产运行由最小 dispatcher 读取 `workflow-registry.yaml` 和 `workflows/document-to-markdown-assets/workflow.yaml`，记录 A1-A7，再调用候选比较器和兼容 runner。一次运行只允许一个 `source`：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

dispatcher 只接受显式页码；`--pages all` 在 bounded 验证中会被拒绝。旧的
`workflow_a_runner.py` 仍是兼容入口，不代表存在另一个独立 Workflow Engine。

### 多文件生产入口

多个 PDF/PPTX/图片不要逐个手工启动，也不要把多个来源合并为一个 package。使用批处理编排器：

```bash
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

编排器按 source 建立可恢复队列，每个 source 调用现有单文件 runner，生成
`<batch>/packages/<source-hash>-<type>/` 独立包，并在 `<batch>/run-record.yaml`
汇总状态、attempt、命令、包路径和独立验收结果。默认串行；`auto` 在没有可验证的
mac96/Ollama 容量信号时仍解析为一个 worker。只有明确配置
`WORKFLOW_A_MAC96_CONCURRENCY=N` 或 `execution.mac96_capacity_hint: N` 后，才会放大到
有限并发。HTTP 可达、模型列表存在或队列已接收都不等于并发容量证明。

长 PDF 仍按页独立请求和落盘证据；`context-mode=auto` 只在页面缺少清晰标题时附带
有限邻页文字提示，`independent` 可强制页隔离，`neighbor` 可显式使用限定窗口。邻页提示
只用于语义承接，不成为本页事实，原生文字和页面资产仍按页保留。配置、恢复和输出契约见
[多文件队列配置](references/workflow-a-batch-config.zh.md) 与
`contracts/workflow-a-batch.yaml`。

### 三个适配器怎么协作

| 组件 | 人工应如何理解 | 机器边界 |
|---|---|---|
| `adapters/workflow_a_dispatcher.py` | Workflow A 的唯一推荐机器入口 | 读取 registry 和 `workflow.yaml`，限制显式页码，记录 A1-A7 envelope，串起后续命令 |
| `adapters/workflow_a_candidate_compare.py` | 同一逻辑文档的 PPTX/PDF 候选比较器 | 只生成 `candidate-comparison.json`，分别记录 `semantic_source`、`visual_source`、`preferred_source` 和差异证据；不生成业务 package |
| `adapters/workflow_a_runner.py` | 单一来源的兼容执行器 | 实际解析、渲染、mac96/Apple Vision 路径、Markdown/manifest/report 和独立 validator；不替代 dispatcher |

因此，candidate comparison 是来源选择证据，runner 是转换实现，dispatcher 是可观测编排
边界；三者不是三个 Workflow，也不能把两个候选来源合并为一个输入。

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
   - DOCX：标题、段落、表格、图片和 OOXML 关系。
   - 原生结果是事实层，不能被 OCR 或视觉模型静默覆盖。

2. **来源视觉资产落盘**
   - 先保存整页渲染图；只有整页图不足以支持关系复核时才生成 regions，按来源页实际引用保留 embedded 资产。
   - 每个资产必须进入 manifest，并可回到来源页、区域或对象。

3. **原生文字优先：SSH → mac96**
   - 每页第一次请求只接收紧凑的 PDF 原生文字、bbox、字号估计、阅读顺序和图形统计 JSON，不接收页面图片，也不做 OCR 式转录。
   - 只有 native 结构留下关系冲突或不确定性时，才向同一测试 package 的 PDF `pdftoppm` PNG 发一次关系复核请求；每页最多一次。
   - 运行记录必须包含：`input_kind`、输入路径、输入 SHA-256、模型、请求次数、prompt/eval token、请求时间、结构化输出和错误信息。
   - mac96 成功不是“看过图片”的口头说明，必须有 `evidence/mac96-visual-review.json`、请求/响应和 `evidence/workflow-execution.json`。

4. **第一兜底：本机 Apple Vision OCR**
   - 仅在 mac96 不可达、超时、模型不支持、输出不可解析或复核失败时执行。
   - OCR 只产生候选文字和置信度；保留原生层与 OCR 层的差异。
   - 运行记录必须包含 Apple Vision 引擎、语言、版本、fallback 原因和输入资产。

5. **第二兜底：`needs-review`**
   - mac96 和 Apple Vision OCR 都不可用，或候选冲突无法裁决时，必须保留视觉资产并标记 `needs-review`。
   - 不得把“没有报错”“图片文件存在”或“夹具断言通过”写成图片内容提取完成。

6. **Markdown 固定阅读顺序**
   - 每页按“标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 来源与不确定性 → 【需要保留的】原始页面图/按需复杂区域图/嵌入图片 → 待核对/未知形式”输出。
   - 图片不得置于正文文字之前；这只调整阅读顺序，不减少视觉资产保留要求。
   - A4 必须记录实际复核范围；真实失败、冲突或未裁决关系才进入 `needs-review`。模型没有返回可选 `unknown_forms` 字段、未执行开放集合扫描或整页图已足够，不能单独制造 `needs-review`。

## 结果判定和下一步

- `passed`：独立验收器重新读取资产包后通过。可以阅读 `document.md`，并把 `assets/` 作为已验收来源资产使用。
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
├── document.md
├── manifest.yaml
├── assets/pages/
├── assets/regions/
├── assets/embedded/
└── reports/
    ├── processing-report.yaml
    ├── acceptance-report.yaml
    └── failure-report.yaml
```

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
- 不在 mac96 失败后直接结束而不运行 Apple Vision OCR 或记录 `needs-review`。
- 不把 OCR 候选当作确定事实。
- 不把 GPT-image-2 纳入 Workflow A。
