# 项目协作说明

本目录是“文档资产化与 Markdown 视觉生成工作流”的规格包和本地执行证据目录。修改时要同时维护机器可读契约、人工说明和可追溯证据；不要把一次运行的临时产物当成规格本身。

## 目录契约

- 根目录只放稳定入口：`README.zh.md`、目标/架构/工作流说明、注册表和其他长期维护的项目文档。
- `.prompt/` 只放仍需续接的临时提示词、handoff 和一次性执行目标。完成或被替代后，有追溯价值的放到 `archive/prompt-history/`；测试专用提示词放在 `tests/prompts/`。
- `workflows/` 放 Workflow 的 `README.zh.md`、`workflow.yaml` 和 `ACCEPTANCE.zh.md`；`skills/` 放可复用 Skill；`contracts/` 放 YAML 契约；`references/` 放长期参考资料。
- 常用手工提示词放在根目录的 `prompts/`，由 `prompts/README.md` 索引，不接入生产运行。`references/prompts/` 中的工作流提示词仍以对应的 Workflow 或契约所登记的文件为准。
- `.audit/` 放本机带日期的工程审计、质量评分和复盘记录；它们是临时工程快照，不是稳定入口，也不是临时提示词，默认不提交 Git。新增 `AUDIT-*`、`QUALITY-*` 或类似快照不得放在根目录。
- `runs/` 只放正式转换的原始证据和输出包。结果包直接使用 `runs/<source-name>/`，不再增加 `real/` 中间目录；该目录及其内容默认被 Git 忽略。
- `runs/pdf-route-detection/` 专门保存 PDF 转换前路线检测证据；运行目录使用 `<timestamp>-<digest>`，不得重复 `real-runs-route-detection` 前缀。来源目录只保留中文总览和分流清单，完整 JSON/NDJSON 留在本目录。
- `tests/runs/` 只放 fixture/测试运行记录，不能代替正式 `output_root`，也不能与 `runs/` 混用。
- `archive/` 是只读历史快照，不参与当前路由；`.omx/`、`.shopme/`、缓存和构建产物不属于项目交付内容。

## 修改规则

1. 移动目录时只改变容器路径，不改运行 ID、包内相对路径、manifest、报告或来源指纹。
2. 移动后必须同步更新 Markdown 链接、命令示例、审计记录和 `.gitignore`；不要留下指向旧根目录的说明。
3. 真实运行证据只有在对应 validator 通过后才能用于生产结论；旧运行目录不得因为“更靠近当前”而自动继承新结论。
4. 保留 `Unknown`、`needs-review`、候选和失败证据，不用重排目录掩盖未决问题。
5. 不要把临时提示词升级成正式 Workflow、Skill 或契约；如果内容稳定下来，再迁移到对应正式目录并补注册/校验。

## 日常触发入口

日常触发关键词是：`文档转 Markdown`。

日常使用不需要输入内部 Workflow 名称，也不需要重复描述输出格式。用户只需说明：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

Agent 读取本文件后，日常关键词直接进入 `contracts/workflow-a-production-runtime-context.yaml`；
`workflow-registry.yaml` 只在路由核对时按需读取对应条目。常规生产运行不加载完整 Workflow、需求史或实现源码。
多个来源按类型分流，一来源一包。两份及以上 PDF（含混合输入中的 PDF 子集）必须走正式 `pdf-collection` 队列；非 PDF 和单份 PDF 沿用各自原入口。除非用户明确声明同一逻辑文档的候选版本，否则不得自动配对。

### 生产上下文最小加载边界

- 默认只加载当前 `source` 或 PDF 来源清单、`output_root`、`pages`、精简生产契约、当前进度摘要和最终验收摘要。
- 参数不明确时先运行对应入口的 `--help`（通用单来源用 `python3 adapters/workflow_a_source_runner.py --help`，PDF 队列用 `python3 adapters/pdf_background_task.py start --help`）；正常生产执行不得读取 `adapters/*.py`。
- `prompts/`、`.prompt/`、目标/任务树、设计说明、完整 Workflow、`references/`、测试和历史运行不进入默认生产上下文。
- 只有用户明确要求改代码、命令失败且错误已定位、或实际行为与契约不一致时，才读取相关 Python 的最小故障范围。
- 需要改变路线、阶段或验收规则时才加载完整 `workflows/document-to-markdown-assets/workflow.yaml` 和相关 Skill/契约。

### 长任务静默与对话预算

- 预计超过 30 秒的转换默认通过 `python3 adapters/pdf_background_task.py start --task-type <已选任务类型> ...` 脱离当前对话；`task-type` 必须根据已批准的 Workflow 路线显式传入，不得由后台管理层静默改线。只发送一次启动摘要，然后结束当前交互。
- 未经用户明确要求监控，不得重复调用 `write_stdin`、`ps`、读取进度文件或发送“仍在运行”“已完成 N 页”“暂无错误”等周期性 commentary。
- 详细进度只保留在任务目录的 `progress-current.json`、`progress.jsonl` 和 `run.log`。用户主动查询时只返回一次当前快照，不复述历史；PDF 队列用 `status <run_id>` 查看 `batch_summary`（文件级计数）、`active_source`、`strict_accepted_count` 和 `halt_reason`。
- 用户说停止、取消、不用继续或不要再跑时，立即调用 `cancel <run_id>`；只在 PID、进程组和 worker identity 一致时终止整个进程组，确认残留进程并返回一次取消摘要后结束。
- 消息只允许出现在启动、真实异常、用户主动查询、完成或取消事件；完整规则见 `contracts/long-task-interaction.yaml`。

### 批量转换固定入口与续跑目录

- 多 PDF 以 `python3 adapters/pdf_background_task.py start --task-type pdf-collection --source-list <list> --output-root <root> --route hybrid --pages all` 静默启动；清单为 UTF-8、每行一个绝对 PDF 路径。默认显式选逐页 `hybrid`，预检批准后才可选 `native-only`；不支持整本视觉集合。不得临时写转换循环、后台脚本，或用单本分页的 `native-pdf-batch`、仍 blocked 的通用批处理替代。
- 新批次根为 `<output_root>/conversions/<route>`；可用 `--run-root` 采纳已有官方路线批次（传准确根路径）。队列按批次根生成稳定 ID，不支持手工 `--run-id`。同根已活动返回 `already-running`；不同路线持有同集合锁时返回 `collection-busy`；不得为旧的非托管队列伪造 `run_id` 或取消命令。
- 未活动的已登记任务返回 `already-registered`，附带 `existing_status` 和 `resume_command`。需要继续时用原 ID 续跑，保留路线、清单和包并核验原登记证据；`completed` 的队列也可原地重验，跳过仍严格验收通过的来源。`--min-free-bytes` 默认 `2147483648`，只在启动下一本前检查；低于阈值暂停，释放空间后续跑，不保证单本运行中不会耗尽磁盘。
- 同一批来源集合只能有一个当前入口目录，用 `<output_root>/conversions/_current/` 记录 `CURRENT_PATHS.md` 和历史目录登记表；后续续接必须先读该入口，再读 `<output_root>/conversions/_consolidated/source-consolidation.json`。
- `_consolidated/` 只保存来源级 strict acceptance 总账和已选 run root，不保存每本 PDF 的工程包；工程包保留在登记的路线批次根。
- 路线恢复批次使用稳定名称，例如 `<output_root>/conversions/native-only`、`hybrid`、`vision-only`。恢复时不默认创建带时间戳的批次目录。
- 新的 PDF 批次将每本书的工程包直接放在固定路线根目录，使用 `conversions/<route>/<package>/`；来源相对路径记录在元数据中，不复制成输出层级。已有批次按登记路径续跑，不自动迁移旧布局。已迁移包保留的旧路径别名只供历史证据解析，不是新的运行目录。
- 继续未完成集合前，先基于所有既有 run root 刷新来源级去重总账；只对未 strict accepted 的来源续跑。旧 `results.json`、stale `running`、smoke、needs-review、failed 或空 `index.md` 不能作为完成依据。
- 正在写入的固定批次根不得移动或重排。需要目录收束时，只新增 `_current` 指针和登记表；等运行停止后再刷新 `_consolidated`。
- 单路线批次完成不等于整集合完成。最终读者层目录（如 `Markdown成品`）只能在 `_consolidated` 显示 100% 来源 strict accepted 后创建和导出。

## Workflow A 稳定入口

Workflow A 的单来源入口使用“固定 Workflow 提示词 + 单一 `source` + `output_root`”；多 PDF 使用上节队列。
单来源调用只处理一个来源，不把 PPTX 与 PDF 自动配对：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pptx" \
  --output-root "/absolute/path/output-root" \
  --pages all \
  --plan-only

python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all \
  --plan-only
```

`--pages` 支持 `all`、单页、逗号列表和范围（如 `1-3,7`）；DOCX 原生路线只支持 `all`，不按 Word 元数据分页、不做 OCR。以上是当前
source-independent 的安全计划入口。旧 document-profile runner 已从本规格包删除并从 active
Workflow 路由排除；不得通过兼容别名、动态 loader 或批处理入口重新引入。旧的 `--pptx` 或
`--pdf` 不能同时传入。正式入口、阶段和输出契约
分别登记在 `workflow-registry.yaml`、`workflows/document-to-markdown-assets/workflow.yaml`
和 `contracts/workflow-a-output-layout.yaml`。

### 执行结果和使用方式

执行器必须在开始前报告来源路径、文件类型、SHA-256、总页数和实际页码；结束时必须报告：

- `status: passed`、`needs-review` 或 `failed`；
- 资产包绝对路径和实际处理页数；
- 独立验收器结果及 `reports/validation-result.json`；
- `index.md`、可选 `assets/` 和报告的下一步使用方式；纯文本结果不要求 `assets/`。

`passed` 才能作为已验收结果交给后续流程；`needs-review` 只能作为保留证据，必须先查看
报告和页面资产；`failed` 不能声称转换完成。runner 自己生成的状态不替代独立验收器。
命令退出码为：`0` 表示 `passed`，`1` 表示已生成但需复核或验收失败，`2` 表示输入/参数错误。

## 页面 Markdown 修复阶段门

读者层 Markdown 修复必须遵循固定推进顺序：**先单页，第 1 页的页面结构守卫通过后再小批量第 2–5 页，人工复核通过后才允许全量第 6–total_pages 页**。前一阶段没有页面结构守卫和规定阶段人工查看记录时，不得启动下一阶段，也不得以一次全量运行替代阶段门。

- 修复一页时只加载该页工作单元；不得为修复一页把其他页面正文、OCR 候选、bbox 或逐页 Vision JSON 读入修复输入。
- 原始 OCR JSON、bbox、置信度、逐页 Vision JSON、页面图和原始 `manifest.yaml` 只读保留；读者层修复不得覆盖、重排或伪造这些来源证据。
- 页面单元格式、拆分/合并规则和 `document-temp.md` 的派生边界以 [`contracts/page-markdown-unit.yaml`](contracts/page-markdown-unit.yaml)、[`skills/page-markdown-unit/SKILL.md`](skills/page-markdown-unit/SKILL.md) 和 [`workflows/document-to-markdown-assets/workflow.yaml`](workflows/document-to-markdown-assets/workflow.yaml) 为准；本文件只规定阶段门，不复制详细格式。
- `document-temp.md` 只能作为当前已通过页面单元的可审阅预览，不能覆盖或替代最终 `index.md`，也不能成为新的事实来源。
- 无法由视觉证据确认的图示关系必须保留为 `Unknown`/`needs-review`，不得生成未经确认的箭头、层级、流程、矩阵或颜色关系说明。

## 验证

从项目根目录执行：

```bash
python3 tests/validate_workflow_package.py
python3 -m pytest -q tests/test_repository_layout.py tests/test_real_output_root_validator.py
```

以上命令用于规格包或完整资产包维护，不是单页 Markdown 修复的必经步骤。单页修复只执行
适配器内的页面结构守卫、当前页写回检查和规定阶段的人工查看；完整生产包仍按 Workflow A
的 A7 规则执行独立验收。

对具体真实包，另外执行：

```bash
python3 tests/validate_real_output_root.py runs/<package>
```
