# Workflow A：文档资产化与 Markdown 转换

## 这个 Workflow 解决什么问题

把 PPTX、PDF、DOCX 或图片转换为一个可检索、可追溯的 Markdown 资产包，同时保留页面图、嵌入图片，并在整页图不足以支撑复核时生成复杂结构区域图。

它的目标不是“把文档 OCR 成一段文字”，而是同时保存：

- 语义层：Markdown、标题、列表、表格、节点和关系；
- 视觉层：原始页面、嵌入媒体、复杂区域和局部裁剪图；
- 追溯层：来源页码、对象坐标、出现记录、哈希、状态和不确定性。

## 什么时候使用

适合：

- 需要把 PPT/PDF/DOCX 纳入知识库或 Markdown 资产库；
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

## 输入

必需：

- `source_document`：PPTX、PDF、DOCX、PNG、JPG 或 JPEG；
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

推荐的生产入口是先由 dispatcher 读取 registry/workflow.yaml，再执行 A1-A7；runner 仍保留为兼容入口。单一来源的 bounded 运行示例：

```bash
python3 adapters/workflow_a_dispatcher.py \
  --workflow document-to-markdown-assets \
  --source "/absolute/path/preferred.pdf" \
  --candidate-comparison "/absolute/path/candidate-comparison.json" \
  --output-root "/absolute/path/output-root" \
  --pages 3,4
```

这里的机器关系固定为：`workflow_a_dispatcher.py` 是唯一推荐入口，负责读取
`workflow-registry.yaml`/`workflow.yaml`、限制 bounded 页码并记录 A1-A7 执行 envelope；
`workflow_a_candidate_compare.py` 只在显式声明同一逻辑文档候选时生成比较证据；
`workflow_a_runner.py` 是被 dispatcher 调用的单一来源兼容执行器，负责实际解析、渲染、
复核/兜底、Markdown/manifest/report 生成和独立 validator 调用。runner 的 `complete`
只表示生成过程完成，不能替代 `reports/validation-result.json`。

dispatcher 要求显式页码，阻止本轮 bounded 验证意外扩展为 `--pages all`。PDF、PPTX
和图片必须分别调用，不能因为文件名相近就合并。`workflow_a_runner.py` 仍接受
`--source`、`--pages` 以及旧的 `--pptx`/`--pdf` 单一来源兼容参数，但不绕过
dispatcher 的执行链证据。

多个来源请调用 `python3 adapters/workflow_a_batch.py`。它是上层队列编排器，不改变
单文件 runner 的来源隔离：每个 source 都有独立 package、manifest、document.md 和
`reports/validation-result.json`，批次根目录另写 `run-record.yaml`。默认串行，只有有
明确 mac96/Ollama 容量证据时才配置有限并发；长文档按页请求，使用 `--context-mode`
控制严格隔离或受限邻页提示。完整配置、恢复和判定见根目录的
[多文件队列配置](../../references/workflow-a-batch-config.zh.md)。

### 长文档 smoke、分批与恢复

长 PDF 不允许直接把 `--pages all` 当成一次不可观测运行。runner 会默认先处理所选页的前
1–2 页（可用 `--smoke-pages 1-2` 显式指定），写入 `evidence/smoke-report.json`；标题仍是页码、正文和 OCR 候选为空、页面图/Markdown 链接缺失、relation-only 或空语义占位任一关键门禁失败，stdout 会返回失败页、阶段、证据路径和修复建议，并停止后续页。

smoke 通过后，用 page-batch 编排器按小批次运行。每批都使用新 package 目录并写 checkpoint，不能覆盖既有真实运行证据：

```bash
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/long.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all --smoke-pages 1-2 --batch-size 8
```

checkpoint 位于 `page-batch-*/checkpoint.json`。恢复时使用
`--resume-pages <checkpoint.json>`，只从最近一个通过批次继续；失败批次会用新的 attempt 目录重跑，旧证据保留。每个页面状态分别记录 native、OCR、visual、标题来源和候选数量，不能把 mac96 关系复核当成文字成功。

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
├── document.md
├── manifest.yaml
├── assets/
│   ├── pages/
│   │   └── <来源>-第<页码>-<语义>-原始页面.png
│   ├── regions/       # 按需；整页图足够时为空目录
│   │   └── <来源>-第<页码>-<语义>-复杂结构图.png
│   └── embedded/
├── evidence/
│   ├── source-inventory.json
│   ├── native-content-model.json
│   └── reconciliation.json
├── reports/
│   ├── processing-report.yaml
│   ├── acceptance-report.yaml
│   ├── failure-report.yaml
│   └── validation-result.json  # runner 自动写入的独立验收结果
```

`document.md`、`manifest.yaml`、`assets/` 和 `reports/` 才是正式业务结果；`evidence/` 是辅助证据，不能替代主结果。

### 读者层与工程层

| 层 | 读什么 | 不放什么 |
|---|---|---|
| 读者层 | `document.md`：标题、主题、正文、列表/表格、已裁决的关系说明和必要页面图链接 | bbox、对象 ID、模型调用、token、fallback、OCR 诊断或其他运行细节 |
| 工程层 | `evidence/`：原生模型、mac96/Apple Vision 请求响应、渲染和复核范围；`manifest.yaml`：资产 ID、哈希、来源出现记录；`reports/`：处理、验收、失败、独立 validator 状态 | 不把工程证据当成读者正文，不用 runner 自报状态替代独立验收 |

`manifest.yaml` 连接文件名、资产和来源指纹；`reports/` 解释本次 package 是否可用；
`evidence/` 允许维护者重读输入、复核降级路径和定位问题。三者都服务工程追溯，
不应复制进 `document.md`。

### Markdown 默认阅读顺序与形式选择

常见图文页面按“标题 → 主题/对比 → 内容要点、列表、表格或分区 → 图示说明（有已裁决关系时） → 原始页面图”输出。这个顺序是默认阅读 profile，不是所有 PDF、图片或其他文档的统一模板；文字型、表格/图表型、流程/矩阵型、低质量扫描、多栏或跨页内容应按实际结构选择 Markdown 表达。来源指纹、bbox、对象 ID、字体、渲染命令、OCR 候选、模型调用和未决诊断只进入 `evidence/`、`manifest.yaml` 或 `reports/`，不得混入读者层。

资产文件采用双名制：`file_name` 使用来源、位置、内容语义和用途组成的稳定英文 ASCII 名称，`display_name` 保留中文阅读语义；稳定 ID、来源指纹与 SHA-256 只进入 manifest，不作为随机前缀暴露在文件名中。原始文件名只作为 `original_filename` 证据保存。

## 执行阶段

1. **A1 来源预检**：识别文件类型、页数、文字层和可解析性。
2. **A2 原生内容提取**：优先解析 PPTX XML、PDF 布局或 DOCX OOXML。
3. **A3 视觉来源资产保留**：保留整页图和嵌入媒体；只有整页图无法支撑复杂关系复核时才生成区域图，不能为了满足目录而重复截图。
4. **A4 原生文字优先的结构复核与 OCR 兜底**：先将每页紧凑 PDF 原生文字/layout JSON 通过 SSH 发给 `mac96`；只有关系无法裁决时，才对同页 PDF 渲染 PNG 发一次关系复核。文字结构不可用、超时或不可解析时，调用本机 Apple Vision OCR；OCR 候选会写入可检索 Markdown，但始终标记“待复核”，不能冒充 confirmed；OCR 也失败时必须写出失败原因、候选数和下一步。
5. **A5 Markdown 组装**：建立页面/章节结构、关系说明和视觉链接。
6. **A6 命名与来源登记**：处理语义命名、来源出现记录和内容去重。
7. **A7 资产包验收**：执行链接、完整性、追溯性和不确定性检查。

每个阶段的 Skill、路由、输出和证据文件以同目录的 [workflow.yaml](workflow.yaml) 为准。
PPTX 页面图必须来自 PPTX 自身的 Office 渲染；PDF 页面图必须来自原始 PDF 渲染。

如果沿用早期的 `$knowledge-map-image-to-md` 名称，仍然执行本 Workflow 的 A1-A7；
仅额外加载 [知识地图差异策略](../../references/knowledge-map-profile.zh.md)。该入口
不复制本说明中的阶段，也不改变 Workflow A 的正式输出布局。

## 成本与外部调用边界

- 默认使用本地解析、渲染、裁剪和 OCR；不把整份文档发送给在线模型。
- mac96 是视觉复核的默认首选，不是静默可选项；每次尝试都必须写入运行证据。不可用时按顺序降级到 Apple Vision OCR，再到 `needs-review`。mac96 成功返回可解析关系且无未裁决项时，Workflow A 应自动完成，不得因模型没有返回可选 `unknown_forms` 字段而强制降级。
- 在线视觉模型不是默认兜底，只有用户明确授权时才可调用，并必须单独记录成本、输入范围和结果。
- 本 Workflow 不调用 GPT-image-2，不生成 PPTX、PDF 或 SVG。

## 失败处理

- 输入损坏或不支持：`source-invalid` / `unsupported-format`；
- 本地解析或渲染暂时失败：`extract-timeout` / `render-failed`，允许重试；
- mac96 或 Apple Vision OCR 不可用：保留原生结果和视觉资产，标记 `visual-review-unavailable` / `ocr-unavailable`，不得声称图片内容已完成提取；
- 真实失败、冲突和未裁决关系必须进入 `Unknown`、`needs-review` 或失败报告，不能静默删除；未执行开放集合扫描本身不是失败，必须在 evidence 中写明实际复核范围。

完整失败态见 [failure-states.zh.md](../../references/failure-states.zh.md)，验收门禁见 [ACCEPTANCE.zh.md](ACCEPTANCE.zh.md)。

## 验证方式

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
