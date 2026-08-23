# 局域网视觉 PDF 转 Markdown（简单独立工作流）

这是一个未注册、不会替换 Workflow A 的显式 `visual-only` worker。它只适合已经确认需要
整页视觉转写的扫描型或视觉结构重要页面，并把读者 Markdown 与工程证据严格分开。普通
PDF 的默认入口是页面级混合路线；不得因为引用了本工作流就把整本 PDF 全部交给视觉模型。

本工作流只负责五项内容任务：

1. 逐页视觉转换：当前页只发送一张完整页面图，生成忠实的逐页 Markdown。
2. 页面整理：删除重复页眉、重复页脚、孤立印刷页码、包裹整页的代码围栏和明确指定的噪声。
3. 合并原始版：按页码确定性生成 `document-temp.md`，保留标题修复前的版本。
4. 合并覆盖检查：逐一核对所选页、页面单元和 `document-temp.md` 的页面标记数量、顺序与唯一性，缺页、重页或乱序均失败关闭。
5. 标题审计与输出：只在规则发现明显标题异常时查看对应页，必要时调整 ATX `#`，生成 `index.md` 后再次核对完整页面序列。

## 职责分离

- `adapters/lan_pdf_vision_to_markdown.py` 负责逐页转换、紧凑留证、恢复校验、页面整理和确定性合并。
- `adapters/lan_markdown_heading_repair.py` 负责受限标题审计；它不改写页面单元，只生成新的 `index.md`。
- `adapters/lan_pdf_markdown_graph.py --visual-only` 是本工作流的显式编排入口，只推进上述五个阶段；不带该参数时是页面级混合路线。

LangGraph 不保存页面 Markdown、PNG、Base64、完整请求、原始响应或日志，也不使用 checkpoint
作为恢复依据。重新运行时，页面恢复只认当前运行目录中的页面图、页面单元和逐页结果，并重新
核对来源、图片、模型、提示词和请求参数哈希。

## 固定模型边界

- Ollama：`http://shopme.local:11434/api/generate`
- 模型：`qwen3-vl:30b-a3b-instruct`，其他模型会被拒绝
- 每次只发送当前一页图片
- `stream: true`
- `think: false`
- `temperature: 0`
- `num_ctx: 16384`
- `num_predict: 4096`
- 请求不设置 `format`
- PDF 原生文本不作为模型输入

## 运行目录

```text
<run-root>/
  assets/
    page-001.png
  page-units/
    p001.md
  evidence/
    page-result-p001.json
    metrics-p001.json
    request-evidence-p001.json
    progress-current.json
    debug/p001/
      ollama-request.json.gz
      ollama-response.raw.jsonl.gz
      ollama-response.aggregate.json
  reports/
    merge-coverage.json
    final-coverage.json
    heading-repair.json
    validation-result.json
  document-temp.md
  index.md
  summary.json
  run.log
  test-history.jsonl
```

这里复用仓库稳定的 `assets/page-NNN.png`、`page-units/pNNN.md` 和
`evidence/*-pNNN.json` 命名，以及页面隔离、确定性合并和读者/工程分层原则。
它不会机械引入正式 `page-markdown-unit` 契约中的固定章节，也不会凭空增加“解读、举例与思考、
模型结构、待核对项”等来源中不存在的内容。因此，这个独立输出不能仅凭命名声称符合完整
page-unit contract。

## 留证级别

- `compact`（默认）：成功页只保留页面图、页面 Markdown、页面结果、性能和请求摘要；失败页自动保留完整压缩诊断证据。
- `diagnostic`：所有页面额外保留压缩原始 NDJSON；成功页仍不保存包含 Base64 的完整请求。
- `full`：明确指定时，所有页面保存压缩后的完整请求和响应。

成功页的请求摘要只记录端点、模型、流式/思考开关、options、图片数量、图片字节数、图片哈希、
提示词哈希、实际请求体哈希、是否含 `format` 和是否使用 PDF 原生文本。全局 `summary.json`
不嵌入逐页结果，`test-history.jsonl` 也只追加一次调用的时间、页段和紧凑结果。

## 使用方式

正式运行前先查看帮助：

```bash
python3 adapters/lan_pdf_markdown_graph.py --help
python3 adapters/lan_pdf_vision_to_markdown.py --help
python3 adapters/lan_markdown_heading_repair.py --help
```

已知《100个思维工具详解.pdf》的 1–3 页 smoke 不需要再粘贴整段验收提示词：

```bash
python3 adapters/lan_pdf_markdown_graph.py --thinking-tools-smoke
```

这个短入口会只读核对固定源文件 SHA-256、页数、大小、PDF 版本、A4 与未加密状态，检查是否
存在同源活动转换进程，创建新的时间戳运行目录，执行 LangGraph 五阶段，并在首次通过后自动
用同一运行目录做一次恢复跳过验证。通用小范围 smoke 可使用：

```bash
python3 adapters/lan_pdf_markdown_graph.py \
  --visual-only \
  --smoke \
  --source "/absolute/path/input.pdf" \
  --pages 1-3
```

正常生成不是 smoke。给出源 PDF 和明确页段，脚本会自动创建新的时间戳运行目录：

```bash
python3 adapters/lan_pdf_markdown_graph.py \
  --visual-only \
  --source "/absolute/path/input.pdf" \
  --pages "1-30" \
  --auto-run-root \
  --evidence-level compact
```

全书视觉转换使用同一入口的 `--visual-only --pages all`，但只应在文档级路线已经确认视觉主导且用户明确要求全量视觉转换时执行。

未来实际执行时使用新的空运行目录：

```bash
python3 adapters/lan_pdf_markdown_graph.py \
  --visual-only \
  --source "/absolute/path/input.pdf" \
  --run-root "/absolute/path/run-root" \
  --pages all \
  --evidence-level compact
```

中断后在同一目录继续：

```bash
python3 adapters/lan_pdf_markdown_graph.py \
  --visual-only \
  --source "/absolute/path/input.pdf" \
  --run-root "/absolute/path/run-root" \
  --pages all \
  --evidence-level compact \
  --resume
```

如果当前 Python 环境没有 LangGraph，编排脚本会给出清楚提示并退出，不会安装依赖或修改全局环境。
三个内容脚本仍可分别使用；`--help` 不读取 PDF、不连接模型。

## 安全边界

- 页面图片一旦存在就只校验和复用；无效或冲突文件会失败关闭，不静默覆盖。
- 页面整理跨页只统计页首和页尾短行，删除仍逐页执行；未命中页面保持原字节。
- 明确噪声必须用精确的 `--confirmed-noise` 参数提供，“其他”不代表开放式正文修改权限。
- 标题模型只看到异常页自己的 PNG 和 `pNNN.md`；没有异常标题时模型调用次数为零。
- 标题阶段只允许增加、删除或调整 ATX `#` 前缀，页面标记和正文必须逐字保持。
- `changed`、`reason`、`confidence`、候选项和模型诊断只进入报告，不进入 `index.md`。
- `document-temp.md` 保留标题修复前的确定性合并版；`index.md` 是校验后的读者入口。
- `reports/merge-coverage.json` 和 `reports/final-coverage.json` 必须同时证明实际页面序列与所选页完全一致。
- 本目录没有登记到 `workflow-registry.yaml`、`skill-registry.yaml` 或 `tool-registry.yaml`。
