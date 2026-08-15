# 新对话续接提示词：Workflow 文档结构与人工可读性

请在以下项目目录继续工作：

`/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets`

## 当前已确认事实

- Workflow A `document-to-markdown-assets` 第 3、4 页真实验证已经通过。
- 已验收 package：
  `runs/real/real-runs-20260815-text-first-p34-v5/21-1130abf8-pdf`
- 主来源是原始 PDF；PPTX 只保留在 candidate-comparison evidence。
- A1-A7、业务 package validator、real output-root validator、workflow package validator、fixture contract 和 pytest 均已通过。
- 质量报告为 97/100。
- 不能执行 33 页全量转换，也不能覆盖旧运行目录；后续对话只能在第 3、4 页通过事实基础上继续讨论全量方案，除非用户明确提出新的授权。

## 本轮目标

只优化规格包的文档结构和人工阅读体验，不运行真实转换，不修改 `runs/real/` 内的任何产物。

### 1. 先核对职责边界

阅读并说明以下文件各自服务于谁、负责什么、明确不负责什么：

- `README.zh.md`
- `WORKFLOWS.zh.md`
- `WORKFLOW-MAP.zh.md`
- `workflow-registry.yaml`
- `workflows/document-to-markdown-assets/README.zh.md`
- `workflows/document-to-markdown-assets/workflow.yaml`
- `workflows/document-to-markdown-assets/ACCEPTANCE.zh.md`
- `WORKFLOW-A-EXECUTION.zh.md`
- `adapters/workflow_a_dispatcher.py`
- `adapters/workflow_a_runner.py`

先输出一份文件职责矩阵和最小文档重构计划，再开始编辑。

### 2. 保持稳定路径，避免无意义批量改名

- 根 `README.zh.md` 保留为项目总入口。
- 每个 Workflow 目录下的 `README.zh.md` 保留为该 Workflow 的人工主入口。
- `WORKFLOWS.zh.md` 定位为 Workflow 总览和选择指南。
- `WORKFLOW-MAP.zh.md` 定位为链路/文件关系图。
- `WORKFLOW-A-EXECUTION.zh.md` 定位为 Workflow A 人工执行指南，不重复复制 `workflow.yaml` 全部字段。
- registry、`workflow.yaml`、contracts、`ACCEPTANCE.zh.md` 保持机器路由或验收职责，不把人工说明塞进 YAML。
- 如认为必须改名，先列出旧路径、新路径、全部受影响引用和测试，再执行最小迁移；默认不要改名。

### 3. 人工说明应覆盖的内容

确保根 README 有“从哪里开始读”的导航，Workflow A README 至少明确：

- 解决什么问题、适用/不适用边界；
- 输入、输出和单一来源原则；
- A1-A7 的人类可读摘要；
- dispatcher、candidate comparator、runner 的关系；
- PDF 原生文字优先、mac96 结构化请求优先、必要时一次页面关系复核、Apple Vision fallback 和 `needs-review`；
- `document.md` 与 `evidence/`、`manifest.yaml`、`reports/` 的读者层/工程层边界；
- bounded 第 3、4 页测试入口和独立验收命令；
- validator 通过不等于视觉/语义质量自动达到 95 分。

Workflow B README 只在发现职责缺口时做同样风格的轻量调整，不扩展 B 的实现。

## 硬约束

- 不执行 `--pages all`，不生成新的 33 页业务 package。
- 不覆盖、删除或重命名旧运行证据、fixture 或 expected。
- 不修改源文件 `/Users/a0000/Downloads/21个成长思维模型.*`。
- 不把 bbox、对象 ID、模型调用、token、fallback 或诊断写入 `document.md`。
- 中文人工说明使用简体中文；命令、路径、Workflow ID、字段名保持原样。

## 验证

修改后运行：

```bash
python3 tests/validate_workflow_package.py
python3 -m pytest -q
git diff --check
```

不要因为文档修改重新执行真实转换。最终报告说明：修改的文件、保持原名的文件及原因、人工推荐入口、机器唯一推荐入口、验证输出，以及“本轮没有执行 33 页全量转换”。
