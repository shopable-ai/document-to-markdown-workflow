# 遗留代码与兼容逻辑状态

## 本轮结论

旧 document-profile runner 已从本规格包删除。它曾把固定文档标题、页眉过滤、OCR 标题区域、固定 PDF
DPI、mac96/SSH/Ollama 探测和旧 Markdown 模板混在一条执行链中；这些规则不属于来源无关的 Workflow。

当前不再存在以下可执行关系：

| 关系 | 当前状态 | 处理原则 |
|---|---|---|
| active Workflow → 旧 document-profile runner | 已排除 | 注册表、Workflow YAML、工具注册表和 dispatcher 不得登记或导入 |
| 批处理 → 旧 document-profile runner | 已排除 | generic batch 尚未推广；批处理脚本 fail-closed，不提供兼容开关 |
| 动态 loader → 旧 document-profile runner | 已删除 | 新代码只能从 active source runner 或独立适配器导入 |
| 历史 package → 旧 runner | 不回放 | 直接审计已有 `manifest.yaml`、`index.md` 和 reports，不重新执行历史代码 |

## 当前边界

| 组件 | 状态 | 允许用途 |
|---|---|---|
| `adapters/workflow_a_source_runner.py` | active source-independent runner | `--plan-only` 和确定性 `--execute`；不读取旧实现 |
| `adapters/workflow_a_dispatcher.py` | active plan dispatcher | 读取当前 registry/workflow，执行 preflight 后调用 active source runner |
| `adapters/workflow_a_batch.py` | blocked-until-generic-execute-promotion | 只保留参数/队列契约检查，当前执行会 fail-closed |
| `adapters/workflow_a_reclassify.py` | removed with retired recovery path | 不再作为 Workflow 路由或恢复入口 |
| `references/reader-noise-profile.yaml` | historical reference only | 不作为 generic source runner 的输入 |
| `tests/fixtures/` | fixture-only | 回归结构和边界；不升级为真实来源结论 |

## 维护规则

- 任何新来源只能经 `adapters/workflow_a_source_runner.py` 路由；不能添加旧 runner、动态 loader 或旧参数别名。
- 若未来需要恢复历史 package，必须先建立独立、来源无关的恢复契约，并把旧规则作为显式候选证据，不能重新接回 Workflow。
- 旧实现名称可以出现在审计或迁移记录中，但不能出现在可执行入口、工具注册表、批处理命令或导入语句中。
