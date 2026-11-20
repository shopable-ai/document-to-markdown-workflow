# 局域网原生 PDF 批量转换说明

## 这份说明为什么放在这里

这是一份**稳定使用说明**，所以放在 `references/`，而不是 `.prompt/`。

- `references/`：长期有效的“怎么用”说明，适合人和 agent 反复查阅。
- `.prompt/`：临时续接、handoff、一次性目标，不适合当稳定操作手册。

如果以后只是想快速知道“怎么跑这批 PDF”，优先读本文件，不需要先翻代码。

## 适用场景

适用于以下情况：

- 已经做过 PDF 路线检测，手里有 `report.json`、`PDF转换路线总览.md` 或 `原生文本路线.txt`。
- 想按既有路线报告分别执行 `native-only`、`hybrid` 或 `vision-only` 队列。
- 想沿用现有单文件入口 `adapters/lan_pdf_markdown_graph.py --no-model`，但不想手工一个个敲命令。
- 想保留批次台账、逐文件输出映射和续跑能力。

不适用于以下情况：

- 把多个来源拼成一个伪文档。
- 把 `adapters/workflow_a_batch.py` 当作当前生产入口。

## 推荐入口

当前批量入口是：

```bash
python3 adapters/lan_pdf_markdown_batch.py --help
```

它会逐个调用现有单文件入口，不改写单文件脚本的行为边界。

日常直接转换一个来源目录时，使用：

```bash
python3 adapters/lan_pdf_markdown_batch.py \
  --source-dir "/absolute/source-directory" \
  --batch-root "/absolute/unified-output-directory" \
  --pages all
```

`--source-dir` 会递归发现 PDF，默认使用逐页 `hybrid` 路由；这表示每页仍由证据决定
`native-only`、`native-plus-visual` 或 `visual-only`，并不强制每页调用视觉模型。所有批次台账和
逐书运行包都保存在同一个 `--batch-root` 下。

批量层固定为一本一本串行执行，`batch_size` 必须为 `1`；这不是书目并发参数。
同一批次父目录同时只允许一个合集队列持有锁。一本子进程返回后如果仍检测到该书的
转换 worker，队列会立刻停止，不会启动下一本。子进程 stdout/stderr 直接写入批次的
`logs/`，内存中只读取末尾 12 KB 用于状态解析。

## 全机器并发边界

批次锁只负责一个合集目录；所有正式 PDF 入口还会共同使用两个互相独立的机器级单槽：

- `local-pdf=1`：全机器同时最多转换一本 PDF。
- `visual-model=1`：全机器同时最多一个真实视觉模型请求。

两个槽可以同时占用，因此一个本地 PDF 转换与一个独立视觉请求可以重叠；混合路线处理视觉页
时会同时占用两者。超过容量的任务在锁上等待，不会提前载入另一整本 PDF。路线检测、状态读取
和后台任务管理器不占重任务槽。每个后台任务的紧凑状态和批次 manifest 都会记录本次
`machine_resource_policy`。锁文件默认位于 `~/.codex/resource-slots/pdf-markdown/`；进程异常退出
时由操作系统释放，同一转换的子进程继承父槽，避免内部阶段重复申请而自锁。

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

### 集合续接的固定目录约束

同一批来源集合进入恢复阶段后，不应反复创建新的时间戳批次目录。先收束入口，再续跑：

```text
<output-root>/conversions/
├── _current/          # 当前入口：CURRENT_PATHS.md、directory-registry.json/csv
├── _consolidated/     # 来源级 strict acceptance 总账，不放每本书工程包
├── native-only/       # 固定路线批次根，含 manifest/results 和每本工程包
├── hybrid/
└── vision-only/
```

- `_current/CURRENT_PATHS.md` 是给人和下次会话看的固定入口；后续续接先读它。
- `_consolidated/source-consolidation.json` 是唯一的来源级完成总账；它从所有历史 run root
  中选择每个来源最新的 strict accepted 工程包，并列出未完成来源。
- 新批次采用 `flat-v1`，工程包直接放在路线目录（如 `hybrid/<书名>-<来源标识>/`）。来源路径与指纹仍记录在
  `manifest.json`、`results.json` 和 `source-output-map.csv`，不复制成输出子目录。来源路径不同的同名文件即使内容相同，也不会共用工程包。
- 批次布局写入 `manifest.json` 的 `package_layout`，逐来源真实路径与 SHA-256 写入 `run_roots`；续跑重新核对来源指纹并复用已登记的包。登记路径不得越出批次根或彼此嵌套，且必须与结果快照中的登记一致。
- 旧清单仍按 `runs/<来源父目录>/<书名>-<指纹>/` 续跑；缺旧清单时必须找到与当前来源匹配的旧包及转换证据才能认领布局，无法辨认的目录不会被自动接管。
- 旧时间戳目录不删除、不覆盖；只登记到 `_current/directory-registry.json/csv`，作为历史证据
  或被 `_consolidated` 选择的候选 run root。
- 如果固定路线目录正在运行，只能更新 `_current` 指针或登记表，不移动其包。迁移已停批次时，必须核对包内文件哈希及验收结果，更新活动索引；若历史证据仍引用原绝对路径，可保留定向的兼容别名，索引始终使用真实包路径。
- 只有 `_consolidated` 证明 expected sources 全部 strict accepted 后，才允许创建最终读者层目录
  并运行集合导出脚本。

如果旧目录只完成 smoke 或 full extraction 中途失败，续跑会保留旧证据，并在同一个
source run root 下创建新的 full attempt。这里的“续跑”表示从安全阶段重新开始整本 full
attempt，不承诺从中断页面进行增量续传。

## 运行结果会保存什么

逐页 hybrid 路线的 `reports/validation-result.json` 由独立的 PDF 包验收器重读生成：核对
来源 SHA-256、原 PDF 页数、逐页证据的哈希、终稿页序及本地图片链接。逐页覆盖通过、
标题校验通过或生成 `index.md` 都不能单独代替整包验收。可用
`python3 adapters/pdf_hybrid_output_validator.py <run-root> --source <pdf>` 重新核验现有包。

每个批次会生成独立目录，大致结构如下：

```text
<batch-root>/
├── manifest.json
├── results.json
├── selected-sources.txt
├── source-output-map.csv
├── logs/
└── <书名>-<来源标识>/
    ├── index.md
    └── ...
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

- 文档级 `native-only` 会显式使用无模型路线；文档级 `hybrid` 和 `vision-only` 都进入
  逐页混合路由，由页面证据决定 `native-only`、`native-plus-visual` 或 `visual-only`，
  不会把整本书强制一次性送入视觉模型。
- 新的全页 page-hybrid 工程包验收后生成内部 `reader/index.md`、`reports/reader-visual-audit.json` 和
  `reports/reader-visual-validation.json`。只在副本跳过已通过整页字符、阅读顺序和非文字结构门的图片；
  低置信与待裁页仍保留。工程验收、读者副本字节验收与语义待复核分别记录；内部副本不是批量成品目录。
- 3.0.0 只对有扫描底图且无已证实图形的 Apple Vision 候选采集本地 Tesseract `chi_sim+eng`；未安装或冲突时保留页图。`evidence/reader-scan-ocr/` 保存逐页引擎/模型、耗时与绑定；独立验收重放不再启动 OCR。已锁定的旧包不升级或改写。
- 同一个 `batch-root` 续跑时只跳过已有独立验收为 `passed` 的书；失败、半成品和
  `needs-review` 继续使用原 source run root 的恢复证据，不另建替代成品目录。
- 真实路线清单里如果有某些 PDF 已经不存在，批量入口会跳过这些失效路径，并把它们记录在 `manifest.json -> origin -> missing_sources`，不会因为单个失效条目让整批中断。

## 什么时候需要 `.prompt/`

通常**不需要**专门为这个批量脚本再建 `.prompt/` 文件。

只有在以下情况才值得新建 `.prompt/`：

- 你想把“下一次新对话怎么继续跑这一个批次”固化成 handoff。
- 你要记录某次运行的临时注意事项、人工验收边界或失败重试策略。

稳定使用说明仍以本文件为准。
