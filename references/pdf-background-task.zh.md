# PDF 长任务后台静默运行

`adapters/pdf_background_task.py` 是转换脚本外的一层进程与状态管理器。它不改变 PDF
转换逻辑，只负责让长任务脱离当前 Codex 对话，并提供一次性 `start/status/cancel/resume`
命令。后台 worker 的 stdout、stderr 和生命周期进度全部落盘，不会依赖当前对话持续存在。

## 启动多 PDF 队列

两份及以上 PDF（含混合输入中的 PDF 子集）使用正式 `pdf-collection` 队列。`--source-list` 指向
UTF-8 清单，每行一个绝对 PDF 路径。其余输入仍走原单来源入口，不自动配对；队列逐本串行，一来源一包。

```bash
python3 adapters/pdf_background_task.py start \
  --task-type pdf-collection \
  --source-list "/absolute/path/to/pdfs.txt" \
  --output-root "/absolute/path/to/output-root" \
  --route hybrid \
  --pages all
```

显式 `hybrid` 为推荐的逐页混合路线；只有路线预检批准才用 `--route native-only`。队列不支持
整本视觉集合。不要临时写转换循环或后台脚本；`native-pdf-batch` 仅用于单 PDF 分页，旧通用
`workflow_a_batch.py` 仍 blocked，都不是正式多 PDF 入口。

- 新批次使用 `<output-root>/conversions/<route>`。采纳已有官方批次时，另传 `--run-root <existing-exact-route-batch-root>`；不要传工程包路径或批次父目录。
- 队列按准确批次根生成稳定 run ID，不支持手工 `--run-id`。同根已有活动队列时返回 `already-running`；若同集合锁由另一条路线的队列持有，则返回 `collection-busy`。两种情况均不启动新队列；旧队列若非管理器托管，返回 `managed: false` 和锁归属，不为它伪造 `run_id` 或取消命令。
- 已登记任务未活动时，`start` 返回 `already-registered`，附 `existing_status` 和 `resume_command`。用原 ID 续跑，保持原路线、来源清单、批次根和逐书包，并在恢复前核验原登记证据。`completed` 的集合任务也能 `resume` 原地重验，跳过仍严格验收通过的来源。
- `--min-free-bytes` 默认 `2147483648`（2 GiB）。仅在启动下一本之前检查，空间不足返回 `paused`；释放空间后用原 run ID 继续，沿用原阈值。这不保证单本转换期间磁盘不会耗尽。
- 单路线批次完成不等于整集合通过。跨路线继续和最终导出仍以来源级 strict acceptance 总账为准，见 [批量转换说明](lan-pdf-markdown-batch.zh.md#集合续接的固定目录约束)。

管理器在返回启动成功前即持有集合锁；同一集合的原生和混合路线不会同时启动。已登记批次的
`manifest.json` 缺失时必须先恢复，不能在同一任务 ID 下重建不同批次。恢复中的当前文件与验收计数
只取自本次启动后写入的快照。默认全页；显式页码只表明本次处理范围，不能推导整本已通过。

## 启动单来源长任务

纯原生 PDF 路线显式写为：

```bash
python3 adapters/pdf_background_task.py start \
  --task-type native-pdf \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

页面级混合路线显式写为：

```bash
python3 adapters/pdf_background_task.py start \
  --task-type hybrid-pdf \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/existing-or-new-run-root" \
  --pages all
```

`hybrid-pdf` 会逐页选择 `native-only`、`native-plus-visual` 或 `visual-only`，不会把整本
PDF 直接交给视觉模型；恢复时复用同一 run root，并重新校验来源和页面路线记录。

只有已批准整本视觉路线时才显式指定：

```bash
python3 adapters/pdf_background_task.py start \
  --task-type visual-pdf \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

`--task-type` 没有隐式默认值；调用方必须传入已经由 Workflow 路由确定的
`native-pdf`、`native-pdf-batch`、`local-ocr-pdf`、`hybrid-pdf`、`visual-pdf` 或 `workflow-a`，后台管理器不会自行改线。`visual-pdf`
会向 graph 明确传入 `--visual-only`；普通 PDF 默认应使用 `hybrid-pdf`。

启动命令只返回一个紧凑 JSON；成功启动时包括 `run_id`、PID、任务类型、转换 `run_root`、后台状态
`task_root`、`run.log` 路径以及可直接复制的查询和取消命令。返回后 Agent 应结束当前交互，
不得为了保持关注继续调用 `write_stdin`、`ps` 或读取进度。

## 查询、取消与恢复

```bash
python3 adapters/pdf_background_task.py status <run_id>
python3 adapters/pdf_background_task.py cancel <run_id>
python3 adapters/pdf_background_task.py resume <run_id>
```

- `status` 读取当前 `progress-current.json` 和对应的当前来源证据，不读取或复述 `progress.jsonl` 历史。
- `pdf-collection` 返回 `batch_summary`（文件级计数）、`active_source`（当前来源）、`strict_accepted_count`（当前批次严格验收来源数）和 `halt_reason`（停队原因）。`current_phase` 及 `phase_source` 说明当前阶段及其依据。`current_page` 仅在匹配的活动子进程携带单页参数时返回；`progress_checkpoint.page` 则是最近落盘的页或二次 OCR 缓存记录。五分钟没有新检查点记为 `no-recent-checkpoint`，不等于进程卡死、失败或通过；状态查询不转发逐页历史或子转换日志。
- 原生、混合和视觉 graph 路线可同步底层逐页 current 快照；`workflow-a` 当前执行器没有独立逐页
  current 文件，因此运行中只报告外层生命周期，终止时再从已经写入 `run.log` 的最终 runner
  JSON 提取实际处理页数，不伪造实时逐页进度。
- `cancel` 先校验 PID、进程组和本次 worker identity，再终止整个进程组；不会向旧 PID
  或身份不匹配的进程发送信号。
- 多 PDF 的外层进程异常退出时，如无法确认组身份，`cancel` 只报告观察到的组成员或残留未知，
  不会把不确定性标成“无残留”。原集合锁仍被持有时不能续跑。
- 支持 `resume` 的单来源任务只允许从 `paused`、`cancelled`、`failed` 或 `budget-exhausted` 恢复，并复用原
  `run_root`。`workflow-a` 同步入口目前没有可验证的原地恢复参数，因此该任务类型不声称支持
  `resume`；原生、混合与视觉 graph 路线支持。`pdf-collection` 还允许 `completed` 后原地重验。

已在同机样本验证后，可以在已有队列自然退出、来源级复验完成且磁盘余量足够时，用原 ID
续跑并选用本地 Apple Vision 快速 PNG：

```bash
WORKFLOW_APPLE_VISION_FAST_PNG=1 python3 adapters/pdf_background_task.py resume <run_id>
```

这个环境开关只作用于本次新启动的 worker 及其子进程；之后再续跑需显式提供。它只改变新
Apple Vision 页面图的无损 PNG 编码，不改 Poppler RGB 像素、DPI、OCR 或验收。已存在的
图和其他锁定证据不重编码；超出快速路径的缓冲与时间预算时使用旧渲染路径。

## 状态与文件

默认后台状态目录为 `~/.codex/background-tasks/<run_id>/`：

```text
<task-root>/
├── progress-current.json
├── progress.jsonl
├── run.log
└── .state.lock
```

- `progress-current.json`：唯一的最新任务快照，包含 `run_id`、来源、输出目录、PID、进程组、
  状态和时间，以及单来源成功/失败页数或队列的文件级摘要。
- `progress.jsonl`：只追加 queued/running/terminal 等生命周期事件，不记录逐页对话消息。
- `run.log`：转换命令的 stdout/stderr；不得原样转发到当前对话。

状态只允许 `queued`、`running`、`paused`、`cancelled`、`failed`、`completed` 和
`budget-exhausted`。`already-running`、`collection-busy` 和 `already-registered` 是启动响应，不是新生命周期状态。状态文件或 PID 单独存在都不能证明任务仍在运行。

## Codex 对话规则

超过 30 秒的任务默认静默。当前对话只允许出现：一次启动摘要、真正需要处理的异常、用户
主动查询时的一次最新快照，以及一次完成或取消摘要。禁止逐页更新、周期性“仍在运行”、
重复粘贴相同路径和自动恢复轮询。完整机器契约见
`contracts/long-task-interaction.yaml`。
