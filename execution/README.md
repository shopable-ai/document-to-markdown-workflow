# 工程执行层入口

execution 承接 V3 架构基线产生的工程任务和人工执行指南。当前 V3 blocked，因此新任务均为 candidate/blocked；既有 Workflow A 指南只描述 Brownfield 入口，不授权真实运行。

| 文件 | 唯一职责 | 当前状态 |
|---|---|---|
| [ENGINEERING-TASKS.md](ENGINEERING-TASKS.md) | 工程任务列表、来源、输出和验收 | candidate-blocked-by-V3 |
| [ENGINEERING-TASK-TREE.md](ENGINEERING-TASK-TREE.md) | 依赖与纵向切片的派生树 | derived |
| [WORKFLOW-A-EXECUTION.md](WORKFLOW-A-EXECUTION.md) | 现有 Workflow A 人工执行边界 | Brownfield guide |

单次 Run 的当前页、进度和结果不写入这些长期文件；它们属于 runs/ 和 Evidence。
