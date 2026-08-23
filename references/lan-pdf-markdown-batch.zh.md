# 局域网原生 PDF 批量转换说明

## 这份说明为什么放在这里

这是一份**稳定使用说明**，所以放在 `references/`，而不是 `.prompt/`。

- `references/`：长期有效的“怎么用”说明，适合人和 agent 反复查阅。
- `.prompt/`：临时续接、handoff、一次性目标，不适合当稳定操作手册。

如果以后只是想快速知道“怎么跑这批 PDF”，优先读本文件，不需要先翻代码。

## 适用场景

适用于以下情况：

- 已经做过 PDF 路线检测，手里有 `report.json`、`PDF转换路线总览.md` 或 `原生文本路线.txt`。
- 只想批量执行 `native-only` 的 PDF。
- 想沿用现有单文件入口 `adapters/lan_pdf_markdown_graph.py --no-model`，但不想手工一个个敲命令。
- 想保留批次台账、逐文件输出映射和续跑能力。

不适用于以下情况：

- `hybrid` 或 `vision-only` 路线。
- 把多个来源拼成一个伪文档。
- 把 `adapters/workflow_a_batch.py` 当作当前生产入口。

## 推荐入口

当前批量入口是：

```bash
python3 adapters/lan_pdf_markdown_batch.py --help
```

它会逐个调用现有单文件入口，不改写单文件脚本的行为边界。

## 最快用法

### 方式一：直接读取路线检测报告

这是首选方式。它能直接读取 `report.json`，按 `route` 选出待转换文件，并保留来源相对路径信息。

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --route-report "/absolute/path/to/report.json" \
  --route native-only \
  --output-root "/absolute/path/to/batch-output-root"
```

如果你要直接使用 2026 年 8 月 21 日这次真实检测结果，命令可以写成：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --route-report "/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets/runs/pdf-route-detection/20260821-170459-d19ae62475/report.json" \
  --route native-only \
  --output-root "/Users/a0000/pdf_runs/lan-pdf-batches"
```

### 方式二：直接读取读者层路线清单

如果你只想拿 `PDF转换路线/原生文本路线.txt` 直接开跑，也可以：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --route-list "/absolute/path/to/PDF转换路线/原生文本路线.txt" \
  --output-root "/absolute/path/to/batch-output-root"
```

### 方式三：先小批量试跑

第一次建议先限制数量：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --route-report "/absolute/path/to/report.json" \
  --route native-only \
  --max-files 10 \
  --output-root "/absolute/path/to/batch-output-root"
```

## 续跑方式

第一次运行后，会生成一个批次目录。后续要续跑同一批时，优先复用同一个 `--batch-root`：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --batch-root "/absolute/path/to/existing-batch-root"
```

如果你更想显式重复原始选择条件，也可以：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --batch-root "/absolute/path/to/existing-batch-root" \
  --route-report "/absolute/path/to/report.json" \
  --route native-only
```

续跑时，只有顶层最终 `index.md`、`summary.json`、
`reports/validation-result.json` 和 `evidence/progress-current.json` 同时存在，来源指纹一致，
并且 `--pages all` 确认覆盖 `1..total_pages` 的项，才会自动标记为
`skipped-existing`。smoke 目录、半成品目录和只带局部 `passed` 报告的目录都不会跳过。

如果旧目录只完成 smoke 或 full extraction 中途失败，续跑会保留旧证据，并在同一个
source run root 下创建新的 full attempt。这里的“续跑”表示从安全阶段重新开始整本 full
attempt，不承诺从中断页面进行增量续传。

## 运行结果会保存什么

每个批次会生成独立目录，大致结构如下：

```text
<batch-root>/
├── manifest.json
├── results.json
├── selected-sources.txt
├── source-output-map.csv
└── runs/
    └── <source-relative-dir>/...
```

其中最重要的是：

- `manifest.json`：本次批量的输入、来源、配置、摘要。
- `results.json`：逐文件执行结果。
- `selected-sources.txt`：本次实际选中的源文件列表。
- `source-output-map.csv`：`源文件 -> run_root -> index.md -> validation-report` 的快速对照表，
  还会记录 `final_stage`、`completion_kind`、实际页数、全量覆盖、返回码和错误。

## 它实际调用的单文件入口

批量入口不会自己实现转换内核，而是对每个 PDF 调用：

```bash
python3 adapters/lan_pdf_markdown_graph.py \
  --source "<pdf>" \
  --pages all \
  --run-root "<per-source-run-root>" \
  --evidence-level compact \
  --no-model \
  --batch-size 1
```

所以如果单文件入口本身表现正常，批量入口只是负责：

1. 选文件。
2. 排队逐个执行。
3. 保存台账。
4. 支持续跑。

`--timeout-seconds` 会继续传给单文件入口，作为其内部各子阶段的超时；批量层不会再把它
重复用作整本转换的总墙钟超时，以免在 smoke 之后仍正常运行的长书被提前终止。

## 当前边界

- 目前只支持 `native-only` 的批量队列。
- `hybrid` 和 `vision-only` 仍然应该走各自单源或专用路线。
- 真实路线清单里如果有某些 PDF 已经不存在，批量入口会跳过这些失效路径，并把它们记录在 `manifest.json -> origin -> missing_sources`，不会因为单个失效条目让整批中断。

## 什么时候需要 `.prompt/`

通常**不需要**专门为这个批量脚本再建 `.prompt/` 文件。

只有在以下情况才值得新建 `.prompt/`：

- 你想把“下一次新对话怎么继续跑这一个批次”固化成 handoff。
- 你要记录某次运行的临时注意事项、人工验收边界或失败重试策略。

稳定使用说明仍以本文件为准。
