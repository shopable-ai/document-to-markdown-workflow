# 验证层说明

## 当前验证器

测试只是一个普通验证环节，不单独建立测试 Workflow 或测试 Skill。测试说明和测试提示词保存在 `tests/`，运行产物保存在 `tests/runs/<run-id>/`。

注意：`tests/runs/<run-id>/` 保存的是测试记录；Workflow A 的业务结果必须写入它的 `output_root`，并符合 `contracts/workflow-a-output-layout.yaml`，不能用测试摘要或阶段日志代替 `index.md`。

`validate_workflow_package.py` 只验证规格与路由层，不执行真实解析或在线调用。它检查：

- 两个 Workflow 是否各有 7 个阶段，并且阶段门禁结构完整；
- Workflow 引用的 Skill 和 Tool 是否已注册；
- 每个注册 Skill 是否同时具备 `SKILL.md`、正确 frontmatter 和 `agents/openai.yaml`；
- 契约路径、验收文档和 Markdown 是否完整；
- GPT-image-2 请求契约是否声明秘密字段禁止写入；
- 是否存在孤立 Skill 目录或未登记工具。

## 证据等级

```text
L0 只有文字设计
L1 规格静态校验通过
L2 本地确定性工具在 fixture 上通过
L3 mac96 视觉复核真实可用并有输出证据
L4 GPT-image-2 真实调用、图片下载和人工/自动验收通过
```

规格包本身达到 L1；当前夹具契约测试达到 `L2-fixture-contract`。这不等于本地解析器、OCR、mac96 或 GPT-image-2 已经完成。`tool-registry.yaml` 中的 `planned` 项必须实现并在对应 fixture 上验证后，才能声称达到更高证据等级。
