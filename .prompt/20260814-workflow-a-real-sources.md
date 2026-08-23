# 新对话执行提示词：Workflow A 独立来源转换

在项目根目录继续执行 Workflow A `document-to-markdown-assets`。先阅读根目录
`AGENTS.md`、本提示词、`workflows/document-to-markdown-assets/workflow.yaml`、
README、ACCEPTANCE、`workflow-registry.yaml`、`tool-registry.yaml` 与相关契约。

本轮只执行 Workflow A，不调用 Workflow B、GPT-image-2，也不生成新图片批次。
每个 `source` 必须单独生成一个 package，不能把文件名相近的 PPTX/PDF 当作候选
配对，也不能把一个来源的视觉结果套到另一个来源。

稳定入口：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pptx" \
  --output-root "/absolute/path/output-root" \
  --pages all

python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all
```

`--pages` 支持 `all`、单页、逗号列表和范围；旧 `--pptx`/`--pdf` 仅是单一来源
兼容别名。PPTX 必须使用 OOXML 原生事实和 PPTX 自身 Office 渲染；PDF 必须使用
PDF 文本层/布局事实和原始 PDF 渲染。报告写入真实绝对路径、SHA-256、页数与范围、
解析器/渲染器命令版本/退出码、字体与 `fc-match`、mac96 预检与每页 JSON 请求、
Apple Vision fallback、资产哈希、分类依据、失败原因和 `source_selection`。

只有用户明确声明同一逻辑文档的多个候选时才运行本地模型候选比较；否则写
`source_selection: single-source`，不得伪造 PPT 优于 PDF。视觉复核失败时保留
原生层和页面图，进入 `needs-review`，不要用 HTTP 200、关键词或模型列表冒充复核。

完成后分别运行：

```bash
python3 adapters/workflow_a_output_validator.py runs/real/<run-id>/<package>
python3 tests/validate_workflow_package.py
RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

最终报告分开写规格静态校验、fixture 契约、每个来源的原生解析/渲染、mac96、
Apple Vision、候选比较（无候选为 single-source）、正式业务结果和仍需人工复核范围。
