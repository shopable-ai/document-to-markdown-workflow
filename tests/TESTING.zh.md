# 测试说明

## 推荐输入

完整能力测试使用：

```text
PPTX + PDF 对照
原始页码：24、25
```

选择 PPTX 作为结构主来源，PDF 作为页面布局与文本顺序对照。原因是 PPTX 可以验证 XML 中的文字、形状、分组、连线和坐标；PDF 可以验证文本块、渲染和 OCR 对照。只使用其中一种格式，只能覆盖部分能力。

## 最小输入矩阵

在运行既有 24–25 页 fixture 之前，先阅读并 materialize
[`tests/fixtures/smoke-matrix/fixture.yaml`](fixtures/smoke-matrix/fixture.yaml)：

- 单页 PPTX、两页 PPTX；
- 单页 PDF、两页 PDF；
- 同名 PPTX + PDF 候选；
- PPTX 排版异常 + PDF 候选。

生成命令见该 fixture 的 README。它只生成测试输入，不代表业务输出已通过；候选比较还必须记录 PPTX XML 语义来源、PPTX/PDF 截图差异、视觉来源和异常证据。

测试输入选择逻辑见 [select-test-input.zh.md](prompts/select-test-input.zh.md)。

## 运行测试

这是对 Workflow A 输入/输出契约的简单测试，不是额外的 Workflow，也不需要调用测试 Skill；已有转换结果审计属于原工作流的阶段执行。它不替代真实 A1-A7 运行。

使用工作区提供的 Python 运行时：

```bash
RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

PDF 夹具检查优先使用 `pypdf`；如果运行时未安装 `pypdf`，测试会降级使用系统的 `pdfinfo` 与 `pdftotext`。两条路径都必须保留页数和文本断言，不能因为缺少 Python 包而静默跳过 PDF 检查。

若环境已安装 pytest，也可运行：

```bash
pytest -q tests/test_fixture_contract.py
```

## 测试内容

- 夹具层：确认 PPTX 恰好保留第 24、25 页，PDF 恰好包含第 24、25 页。
- 原生文本层：确认关键主题和节点存在。
- 结构层：确认流程/层级/对比结构不能只用标题通过。
- 视觉层：确认第 24、25 页的页面图存在且可打开。
- 资产层：确认后续 Markdown 必须能链接到对应页面或复杂区域资产。
- A6 层：确认 manifest 同时登记稳定英文 `file_name`、中文 `display_name` 和可定位的 `source_occurrences[]`；文件名不得依赖原始中文文件名。
- 证据层：确认 fixture 报告逐项列出 A1–A7 的 `stage_evidence`，并明确 `L2-fixture-contract`、验证命令和 `Unknown`；确认始终存在 `failure-report.yaml`。

## 不包含在本夹具中的内容

- 不把 GPT-image-2 调用作为本地单元测试；它应由独立的在线适配器测试覆盖。
- 本夹具契约测试不自动连接 SSH/mac96，也不运行当前对话在线视觉模型或 Apple Vision OCR；它只能验证输入范围和预期目录契约。真实运行必须按“原生结构 → 候选文字识别 → Markdown 结构重建 → 广告策略”记录执行路径，并依据真实页面质量门选择 OCR、小模型或 30B；传输失败不自动写成 `needs-review`。
- 不用封面或目录作为“解析成功”的唯一证据。
- 当前测试是 fixture/契约层证据，不等同于完整解析器、OCR 或在线模型 E2E 证据。
