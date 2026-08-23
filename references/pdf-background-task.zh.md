# PDF 长任务后台静默运行

`adapters/pdf_background_task.py` 是转换脚本外的一层进程与状态管理器。它不改变 PDF
转换逻辑，只负责让长任务脱离当前 Codex 对话，并提供一次性 `start/status/cancel/resume`
命令。后台 worker 的 stdout、stderr 和生命周期进度全部落盘，不会依赖当前对话持续存在。

## 启动

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
`native-pdf`、`native-pdf-batch`、`hybrid-pdf`、`visual-pdf` 或 `workflow-a`，后台管理器不会自行改线。`visual-pdf`
会向 graph 明确传入 `--visual-only`；普通 PDF 默认应使用 `hybrid-pdf`。

启动命令只返回一个紧凑 JSON，其中包括 `run_id`、PID、任务类型、转换 `run_root`、后台状态
`task_root`、`run.log` 路径以及可直接复制的查询和取消命令。返回后 Agent 应结束当前交互，
不得为了保持关注继续调用 `write_stdin`、`ps` 或读取进度。

## 查询、取消与恢复

```bash
python3 adapters/pdf_background_task.py status <run_id>
python3 adapters/pdf_background_task.py cancel <run_id>
python3 adapters/pdf_background_task.py resume <run_id>
```

- `status` 只读当前 `progress-current.json`，不读取或复述 `progress.jsonl` 历史。
- 原生、混合和视觉 graph 路线可同步底层逐页 current 快照；`workflow-a` 当前执行器没有独立逐页
  current 文件，因此运行中只报告外层生命周期，终止时再从已经写入 `run.log` 的最终 runner
  JSON 提取实际处理页数，不伪造实时逐页进度。
- `cancel` 先校验 PID、进程组和本次 worker identity，再终止整个进程组；不会向旧 PID
  或身份不匹配的进程发送信号。
- `resume` 只允许从 `paused`、`cancelled`、`failed` 或 `budget-exhausted` 恢复，并复用原
  `run_root`。`workflow-a` 同步入口目前没有可验证的原地恢复参数，因此该任务类型不声称支持
  `resume`；原生、混合与视觉 graph 路线支持。

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
  状态、时间和成功/失败页数。
- `progress.jsonl`：只追加 queued/running/terminal 等生命周期事件，不记录逐页对话消息。
- `run.log`：转换命令的 stdout/stderr；不得原样转发到当前对话。

状态只允许 `queued`、`running`、`paused`、`cancelled`、`failed`、`completed` 和
`budget-exhausted`。状态文件或 PID 单独存在都不能证明任务仍在运行。

## Codex 对话规则

超过 30 秒的任务默认静默。当前对话只允许出现：一次启动摘要、真正需要处理的异常、用户
主动查询时的一次最新快照，以及一次完成或取消摘要。禁止逐页更新、周期性“仍在运行”、
重复粘贴相同路径和自动恢复轮询。完整机器契约见
`contracts/long-task-interaction.yaml`。
