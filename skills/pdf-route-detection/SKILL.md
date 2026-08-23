---
name: pdf-route-detection
description: 在大批量 PDF 转 Markdown 前做本地路由检测；适用于“先判断哪些能原生提取、哪些要混合、哪些要视觉转换”“先扫目录再分流”的请求。不适用于直接执行正式转换或替代独立验收。
---

# PDF 路由检测

在正式转换前，对一批 PDF 做低成本、可回放的本地检测；执行单本 PDF 时，再用同一规则集逐页选择原生、混合补充或视觉路线。结论必须进入工程证据，不能依赖用户在提示词里重复内部规则。

## 目标

- 把每个 PDF 保守分类为 `native-only`、`hybrid` 或 `vision-only`。
- 对 `hybrid` 文档逐页分类为 `native-only`、`native-plus-visual` 或 `visual-only`。
- 保存 `source_sha256`、页级抽样信号、路由理由和推荐入口，避免只凭文件名或记忆做判断。
- 输出按路由拆分的 source list，方便后续批量转换。

## 固定边界

- 这是**预检和分流**能力，不直接生成 Markdown 正文。
- 文档预检只用本地廉价信号：`pdfinfo`、`pdftotext` 抽样页、`pdfimages -list`。
- 页面执行路由组合原生文字、`pdfimages -list` 图片对象/面积、原生 bbox 空白、`pdfplumber.find_tables()` 和候选页的本地渲染结构探测。
- “如图”“图 1”“表 1”“矩阵”“流程图”“模型图”等文字只产生候选信号，不能单独触发视觉模型。
- 不调用在线模型、不跑 OCR、不替代 smoke、阶段门或独立验收。
- 本地页面渲染结构探测不使用模型，只能在候选页上检查表格线、流程线、坐标轴或矩阵式结构。
- 哈希使用 `sha256`，不要用 `md5` 作为结论主键。

## 推荐入口

扫描整个目录：

```bash
python3 adapters/pdf_route_detection.py \
  --source-dir "/absolute/path/to/pdf-root"
```

扫描明确清单：

```bash
python3 adapters/pdf_route_detection.py \
  --source-list "/absolute/path/to/sources.txt"
```

输出分成读者层和工程层：

- 单个 `--source-dir`：来源根目录只写一个中文总览和一个中文结果目录。
- 每次检测：完整 JSON、NDJSON 和逐文件记录只写入工程审计目录。
- `runs/pdf-route-detection/current.json` 保存当前报告指针和按来源目录索引，不复制完整记录。

批次目录类似：

```text
runs/pdf-route-detection/
├── current.json
└── 20260821-153000-abc123def0/
    ├── report.json
    ├── records.ndjson
    ├── records/
    │   └── 0001-<source>.json
    └── route-lists/
        ├── native-only.txt
        ├── hybrid.txt
        └── vision-only.txt
```

单个 `--source-dir` 的读者层输出类似：

```text
<source-dir>/
├── PDF转换路线总览.md
└── PDF转换路线/
    ├── 原生文本路线.txt
    ├── 图文混合路线.txt
    ├── 视觉识别路线.txt
    └── 待人工复核.csv
```

不在来源根目录写入完整 `report.json` 或 `records.ndjson`。`待人工复核.csv`
只收录失败和低置信度记录，便于人工集中处理。

已有旧报告可以直接回填，不重新读取 PDF：

```bash
python3 adapters/pdf_route_detection.py \
  --backfill-report "/absolute/path/to/route-report.json" \
  --source-dir "/absolute/path/to/pdf-root"
```

## 结果解释

- `native-only`：原生文字层强，图像/图示开销低，优先走 `adapters/lan_pdf_markdown_batch.py`。
- `hybrid`：原生文字存在，但页面结构稀疏、复杂图示或图片页值得进入 staged Workflow A。
- `vision-only`：原生文字显著不足，或整页图片/图示主导，优先走视觉页路线。

文档级 `hybrid` 执行时的页面路线含义：

- `native-only`：原生正文充分且没有视觉佐证；只写原生正文，不调用视觉模型。
- `native-plus-visual`：原生正文可用，同时图片对象/面积、bbox 空白、表格或渲染结构提供视觉佐证；原生正文先写，视觉结果只作为同一 page-unit 的图片说明、表格结构或图示关系补充。
- `visual-only`：原生文字为空或显著不足，同时存在图片、表格或渲染结构佐证；当前页进入视觉/OCR。
- “如图”但没有图片对象：保持 `native-only`，标记 `needs-review`，不得根据标题或文字臆造图示。

## 后续衔接

- `原生文本路线.txt`：优先喂给 `lan_pdf_markdown_batch.py`；更稳的是直接喂 `report.json + --route native-only`。
- `图文混合路线.txt`：逐个来源喂给 `lan_pdf_markdown_graph.py` 的默认页面混合入口；不得整本直接改走视觉模型。
- `视觉识别路线.txt`：只有文档预检明确为视觉主导且用户批准时，才使用 `lan_pdf_markdown_graph.py --visual-only`。

对已有同源运行目录续跑时，必须明确传入该目录；页面复用要同时校验来源 SHA-256、页码、已通过的路线记录和唯一 page-unit。页面失败只登记为待处理并继续后续页，最终由 `reports/page-completion.json` 报告缺页、重页和额外页。

如果同一目录反复检测，脚本会通过 `runs/pdf-route-detection/current.json` 定位该来源目录最近一次报告，复用未变化来源的结论，避免每次都重跑整套 `pdfinfo`/`pdftotext`/`pdfimages`。旧版 latest 报告只允许作为首次迁移的只读兼容输入，不再生成。

不得因为前面连续几页是整页图片就跳过代表页原生文字抽样；封面、插页和扫描附录不能替正文页决定整本路线。

如果检测结果边界模糊，优先保守地留在 `hybrid` 或 `vision-only`，不要为了追求吞吐把弱文字层误判为 `native-only`。
