# Legacy Adapters

此目录是 Workflow A 旧实现的只读归档，不参与当前生产路由。

- `workflow_a_runner.py` 仅用于已有证据包恢复、历史兼容和回归测试。
- 新来源必须使用 `adapters/workflow_a_source_runner.py`；当前默认是 `--plan-only`。
- `adapters/workflow_a_batch.py` 默认 fail-closed，只有明确传入 `--legacy-compat` 才会调用本目录文件。
- 新代码不得从本目录导入；需要维护兼容行为时，应先建立独立迁移任务和测试证据。
