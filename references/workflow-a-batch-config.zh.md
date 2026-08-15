# Workflow A 多文件队列配置

## 推荐入口

多个来源不要把文件拼成一个伪文档，也不要让一个 shell 循环丢失中间状态。使用批处理编排器；它按 source 建立队列，每个 source 继续调用单文件 runner，并生成独立资产包：

```bash
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/a.pdf" \
  --source "/absolute/path/b.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

来源较多时，可以使用 UTF-8 文本清单，每行一个绝对或相对路径；空行和 `#` 注释会被忽略，不展开 glob：

```bash
python3 adapters/workflow_a_batch.py \
  --source-list "/absolute/path/sources.txt" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

默认行为是串行队列。批处理根目录类似：

```text
<output-root>/batch-<timestamp>-<digest>/
├── run-record.yaml
└── packages/
    ├── <source-a-hash>-pdf/
    │   ├── document.md
    │   ├── manifest.yaml
    │   └── reports/validation-result.json
    └── <source-b-hash>-pdf/
```

## 并发策略

`--concurrency serial` 强制一个 worker；`--concurrency 2` 使用明确的固定上限；`--concurrency auto` 只接受明确容量提示：

```bash
WORKFLOW_A_MAC96_CONCURRENCY=2 \
python3 adapters/workflow_a_batch.py --source-list sources.txt --output-root out --concurrency auto
```

没有可验证的 mac96/Ollama 并发容量时，`auto` 必须解析为 `workers=1`。`ollama list` 或 HTTP 200 只能证明服务可达，不能证明模型可以安全并行；因此不会因为“看起来没有限制”而放大请求。容量提示也可以写入配置：

```yaml
execution:
  concurrency: auto
  mac96_capacity_hint: 2
  per_source_timeout_seconds: 1800
retry:
  max_attempts: 2
  backoff_seconds: 5
context:
  mode: auto
  window_pages: 1
```

配置只保存行为参数，禁止放 API key、Authorization、Cookie、Token、订阅 URL 等秘密。

## 长文档与上下文

单个 PDF 仍按页发起原生文字/layout 请求，每页独立落盘请求和响应证据；不会把全部页面图片或全文一次性塞进一个上下文。`context.mode` 有三档：

- `independent`：严格页隔离，适合页面互不延续的图表或目录。
- `neighbor`：只附带相邻页有限长度的文字提示，用于解决“本页/下一步/上图”等语义承接；邻页提示不是本页事实。
- `auto`：有清晰页标题时保持独立，否则附带一页邻接提示；窗口由 `window_pages` 限制。

无论哪一档，原生抽取、页面图、模型请求、Markdown section 和 manifest 都保留页号与来源指纹。上下文提示只帮助语义定位，不允许跨页复制事实或关系。

## 恢复与验收

每次批处理写 `run-record.yaml`。可以恢复最新批次，或指定准确记录：

```bash
python3 adapters/workflow_a_batch.py --source-list sources.txt --output-root out --resume
python3 adapters/workflow_a_batch.py --source-list sources.txt --output-root out \
  --resume-run out/batch-.../run-record.yaml --retry-needs-review
```

恢复只复用 source SHA-256 完全相同且独立验收为 `passed` 的 package；失败项会使用新的 attempt 目录，原失败证据保留。批次 `succeeded` 的唯一条件是所有 source 的 `reports/validation-result.json` 都为 `passed`。批次 `needs-review` 或 `failed` 不能被概括成“全部转换完成”。

长 PDF 的页级批处理使用同一个编排器的 `--batch-size` 模式。它先固定运行 smoke 页，
smoke 报告通过后才启动后续页；每批使用新 package 目录，checkpoint 记录页面状态和
`document.md` 哈希：

```bash
python3 adapters/workflow_a_batch.py \
  --source "/absolute/path/long.pdf" --output-root out \
  --pages all --smoke-pages 1-2 --batch-size 8
```

恢复使用 `--resume-pages out/page-batch-.../checkpoint.json`。恢复只跳过已经通过且哈希
仍存在的批次；失败批次以新的 attempt 目录重跑，绝不覆盖已有真实运行包。
