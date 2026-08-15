# 项目协作说明

本目录是“文档资产化与 Markdown 视觉生成工作流”的规格包和本地执行证据目录。修改时要同时维护机器可读契约、人工说明和可追溯证据；不要把一次运行的临时产物当成规格本身。

## 目录契约

- 根目录只放稳定入口：`README.zh.md`、目标/架构/工作流说明、注册表和其他长期维护的项目文档。
- `.prompt/` 放临时性提示词、跨对话续接、handoff 和一次性执行目标。新增此类文件不得放到根目录；测试专用提示词继续放在 `tests/prompts/`。
- `workflows/` 放 Workflow 的 `README.zh.md`、`workflow.yaml` 和 `ACCEPTANCE.zh.md`；`skills/` 放可复用 Skill；`contracts/` 放 YAML 契约；`references/` 放长期参考资料。
- `.audit/` 放本机带日期的工程审计、质量评分和复盘记录；它们是临时工程快照，不是稳定入口，也不是临时提示词，默认不提交 Git。新增 `AUDIT-*`、`QUALITY-*` 或类似快照不得放在根目录。
- `runs/real/` 只放本地真实运行的原始证据和输出包。目录名保留已有 `real-runs-*` 运行 ID，但不得再直接出现在根目录；该目录及其内容默认被 Git 忽略。
- `tests/runs/` 只放 fixture/测试运行记录，不能代替正式 `output_root`，也不能与 `runs/real/` 混用。
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

Agent 读取本文件后，再从 `workflow-registry.yaml` 和对应 Workflow 文件解析实际路由、阶段、契约和验收器。多个来源必须分别执行；除非用户明确声明它们是同一逻辑文档的候选版本，否则不得自动配对。

## Workflow A 稳定入口

Workflow A 的唯一推荐生产入口是“固定 Workflow 提示词 + 单一 `source` + `output_root`”。
每次调用只处理一个来源，不把 PPTX 与 PDF 自动配对：

```bash
python3 adapters/workflow_a_runner.py \
  --source "/absolute/path/input.pptx" \
  --output-root "/absolute/path/output-root" \
  --pages all

python3 adapters/workflow_a_runner.py \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

`--pages` 支持 `all`、单页、逗号列表和范围（如 `1-3,7`）。旧的 `--pptx` 或
`--pdf` 只保留为单一来源兼容别名，不能同时传入。正式入口、阶段和输出契约
分别登记在 `workflow-registry.yaml`、`workflows/document-to-markdown-assets/workflow.yaml`
和 `contracts/workflow-a-output-layout.yaml`。

### 执行结果和使用方式

执行器必须在开始前报告来源路径、文件类型、SHA-256、总页数和实际页码；结束时必须报告：

- `status: passed`、`needs-review` 或 `failed`；
- 资产包绝对路径和实际处理页数；
- 独立验收器结果及 `reports/validation-result.json`；
- `document.md`、`assets/` 和报告的下一步使用方式。

`passed` 才能作为已验收结果交给后续流程；`needs-review` 只能作为保留证据，必须先查看
报告和页面资产；`failed` 不能声称转换完成。runner 自己生成的状态不替代独立验收器。
命令退出码为：`0` 表示 `passed`，`1` 表示已生成但需复核或验收失败，`2` 表示输入/参数错误。

## 验证

从项目根目录执行：

```bash
python3 tests/validate_workflow_package.py
python3 -m pytest -q tests/test_repository_layout.py tests/test_real_output_root_validator.py
```

对具体真实包，另外执行：

```bash
python3 tests/validate_real_output_root.py runs/real/<real-run>/<package>
```
