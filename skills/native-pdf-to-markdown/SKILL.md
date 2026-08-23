---
name: native-pdf-to-markdown
description: 用现有 adapters 脚本把 PDF 按原生文本路线转成 Markdown；适用于“PDF 转 Markdown”“全书原生提取”“不要视觉模型”“逐页生成 page-units 并独立验收”。不适用于扫描件 OCR、图片优先或视觉关系重建任务。
---

# 原生 PDF 转 Markdown

使用仓库当前 `adapters` 目录中的现有脚本完成整本或指定页范围的 PDF 原生文本转换，默认不调用视觉模型、不渲染 PNG、不复用旧输出。该 Skill 是纯原生 worker；页面级混合编排由 `pdf-route-detection` 和 `lan_pdf_markdown_graph.py` 负责，不能在这里隐式升级为整本视觉转换。

## 入口脚本

- 逐页原生提取：`adapters/lan_pdf_to_markdown.py`
- 全书批处理与最终验收：`adapters/run_pdf_batches.py`
- 多文件原生批量编排：`adapters/lan_pdf_markdown_batch.py`

## 固定边界

- 只适用于 PDF 原生文字层可读的来源；先验证 `pdftotext` 和页数。
- 必须走 `--no-model` 等价路线；不得调用 `lan_pdf_vision_to_markdown.py`、`qwen3-vl`、在线模型或视觉验证。
- 独立原生运行默认输出到新的 run root。建议把 `--output-root` 指到父目录，例如 `runs/`；脚本会在其下创建
  `来源名称/` 的可读包目录；只有发生同名冲突时才追加 `-02`、`-03`。`assets/` 与 `page-units/` 仍保留；
  `assets/` 可为空。
- 只有调用方明确给出 `--resume-package <exact-run-root>` 时才允许原地续跑；复用页必须通过来源 SHA-256、页码、页面摘要/evidence 和唯一页面标记校验，不能只看文件存在。
- 后处理只允许做 Markdown 结构修复：页眉、页脚、孤立页码、标题层级。不得改写、总结、补写或删除 PDF 正文。

## 执行顺序

1. 预检查：确认源文件可读、脚本存在、脚本可编译、PDF 总页数、原生文字层、原生文字总行数、`pdfplumber.find_tables()` 表格数。
2. 少量试运行：固定含第 1 页，再补一个表格或列表页、一个长正文页。页面必须来自当前 PDF 实际内容。
3. 全书执行：使用 `run_pdf_batches.py --batch-size 1` 跑完整页范围。
4. 后处理与验收：生成 `page-units/pNNN.md`、`document-temp.raw.md`、`document-temp.md`、最终 Markdown、`plan.json`、`progress.json`、`summary.json`、`evidence.json`、`acceptance.json`。

当上层混合控制器调用本 worker 时，每页仍先生成或复用原生证据，然后才由页面路由决定是否追加视觉结果。视觉补充不得修改本 worker 的原生正文。

## 常用命令

单页或少量页直接验证 `lan_pdf_to_markdown.py`：

```bash
python3 adapters/lan_pdf_to_markdown.py \
  --source "/absolute/source.pdf" \
  --output-root "/absolute/output-root" \
  --pages "1,20,100" \
  --execute \
  --no-model \
  --engineering-output
```

整本执行：

```bash
python3 adapters/run_pdf_batches.py \
  --source "/absolute/source.pdf" \
  --output-root "/absolute/output-root" \
  --pages all \
  --batch-size 1 \
  --smoke-pages "1,20,100"
```

如果已经先做过路线检测，并且只想批量执行 `native-only`，优先使用：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --route-report "/absolute/path/to/report.json" \
  --route native-only \
  --output-root "/absolute/path/to/batch-output-root"
```

## 完成标准

只有在以下条件都满足时才报告完成：

- `progress.status == "passed"`
- `summary.status == "passed"`
- `selected_pages` 覆盖完整 PDF
- `missing_line_ids` 与 `duplicate_line_ids` 为空
- `table_count_pdfplumber_find_tables == rendered_table_count`
- 源 PDF SHA-256 一致
- `old_summary_reused == false`
- `model_calls` 为空且 `visual_model == "not-run"`
- 无残留后台进程
- 最终独立验收文件为 `passed`
