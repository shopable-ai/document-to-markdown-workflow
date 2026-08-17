# Workflow A 测试阶段执行提示词

## 一句话任务

在现有规格包内，验证并修正“来源文档 → Markdown + 来源视觉资产”的 Workflow A；使用第 24、25 页高信息密度测试夹具，确保测试记录、测试样例和正式业务结果不再混淆。

## 总目标

在以下目录内，持续完善一个“文档内容资产化与视觉生成”规格包：

```text
/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets
```

最终要维护两个彼此独立的 Workflow，不要把它们合并成一条强耦合流水线。

### Workflow A：document-to-markdown-assets

输入 PPTX、PDF、DOCX 或图片，输出可检索、可追溯的 Markdown 资产包：

```text
来源文档
→ Markdown 语义内容
→ 来源视觉资产
→ manifest 与处理/验收报告
```

A 的核心不是“把文件 OCR 成一段文字”，而是并行保留：

- PPTX XML 的文字、形状、分组、连线、坐标、层级和媒体关系；
- PDF 的文本块、边界框、阅读顺序和页面视觉布局；
- DOCX 的标题、段落、表格、图片和 OOXML 关系；
- 复杂流程图、矩阵、图文混排和其他难以用线性文字表达的结构。

原生结构提取始终先行；视觉内容复核默认通过 SSH 调用局域网 mac96。mac96 不可用、超时或输出不可解析时，才调用本机 Apple Vision OCR；两者均不可用或冲突未决时必须 `needs-review`。

A 的正式业务结果必须符合：

```text
<output_root>/<package-name>/
├── index.md
├── manifest.yaml
├── assets/
│   ├── pages/
│   ├── regions/
│   └── embedded/
└── reports/
    ├── processing-report.yaml
    ├── acceptance-report.yaml
    └── failure-report.yaml
```

`index.md`、`manifest.yaml`、`assets/` 和 `reports/` 才是业务结果；阶段日志、解析中间模型、测试摘要和断言报告不能替代它们。

### Workflow B：markdown-to-gpt-image

输入已有 Markdown 的一个局部知识单元，加上可选参考资产，调用 GPT-image-2 生成恰好一张图片。B 不重新解析 PPT/PDF，不生成 PPTX、PDF、SVG 或图片批量文件。

本轮只推进 Workflow A 的测试与边界修正，不扩展 B 的实现。

## 当前阶段与执行原则

当前处于 Workflow A 的测试阶段。测试只是 `tests/` 下的普通验证环节：

- 不创建测试 Workflow；
- 不创建测试 Skill；
- 不把夹具契约通过写成完整解析器通过；
- 如果测试暴露结果契约、Skill 边界或目录结构问题，先重新梳理再修改；
- 不为了让测试变绿而降低断言或掩盖真实失败；
- 未执行的 OCR、mac96 或 GPT-image-2 必须明确写成未执行。

只查看和修改上述目标目录，不读取或修改其他项目目录及原始下载目录。

## 固定测试输入

使用已复制到目标目录内的夹具：

```text
tests/fixtures/18个工作思维模型-last-2-slides/
```

只测试原文档第 24、25 页，禁止退回封面和目录：

- 第 24 页：提升领导力的 3357；三零、三管、五带、七抓；多层节点和连接关系；
- 第 25 页：搞定自己 vs 搞定别人；世界观、价值观、正确人生观、实力、能力、背景和复杂对比关系。

输入文件：

```text
tests/fixtures/18个工作思维模型-last-2-slides/selected/18个工作思维模型-第24-25页.pptx
tests/fixtures/18个工作思维模型-last-2-slides/selected/18个工作思维模型-第24-25页.pdf
```

PPTX 是结构主输入，用于验证 XML；PDF 是布局、阅读顺序和视觉对照输入。两者互相补充，不能把其中一个当成另一个的替代品。

## 本轮任务

### 1. 审计现有结构

只检查目标目录内的文件，明确区分：

- Workflow 规格；
- 阶段与 Skill；
- Tool/Adapter 注册；
- 测试夹具；
- 测试运行记录；
- 真实业务结果。

重点检查 Workflow A 的 YAML、以下契约和 A5/A6/A7 Skill 是否完全一致：

```text
workflows/document-to-markdown-assets/workflow.yaml
contracts/markdown-asset-package.yaml
contracts/workflow-a-output-layout.yaml
skills/markdown-structure-builder/SKILL.md
skills/source-asset-linker/SKILL.md
skills/asset-package-validation/SKILL.md
```

### 2. 验证正式结果契约

确认所有正式结果统一使用：

- `index.md`
- `manifest.yaml`
- `assets/`（扁平目录）
- 不得存在 `assets/pages/`、`assets/regions/`、`assets/embedded/`
- `reports/processing-report.yaml`
- `reports/acceptance-report.yaml`
- `reports/failure-report.yaml`

确认 `tests/runs/<run-id>/` 只保存测试记录和测试产生的临时结果，不被当成正式交付目录。

### 3. 运行固定夹具测试

执行：

```bash
python3 tests/validate_workflow_package.py

RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

至少验证：

- PPTX 恰好包含第 24、25 页；
- PDF 恰好包含对应两页；
- 关键文字包含：领导力、3357、三零、三管、五带、七抓、搞定自己、搞定别人、世界观、价值观；
- 页面视觉基准图存在；
- 若测试确实生成了业务结果包，Markdown 图片链接和 manifest 资产路径都真实存在。

### 4. 区分“测试样例”与“真实解析结果”

检查：

```text
tests/runs/fixture-contract-20260814-0118/
tests/runs/fixture-contract-20260814-0118/outputs/document-asset-package/
```

如果其中的 Markdown、manifest 或报告是手工编写的预期样例，必须明确标记为 `expected`、`fixture-sample` 或 `fixture-contract-only`，不能称为解析器自动生成结果。

只有在真实解析代码实际执行并生成文件后，才能报告“夹具范围内真实解析结果”。无论哪种情况，都不能声称完整 25 页文档已经转换完成。

如果当前没有真正解析器：

- 不要擅自扩大任务去实现全部解析器；
- 先报告缺少哪些 Tool/Adapter；
- 保留测试夹具、契约和预期结构；
- 将完整解析器实现列为后续明确任务。

## 验收标准

- 两个正式 Workflow 仍然独立；
- 测试没有新增 Workflow 或 Skill；
- Workflow A 的正式结果契约没有把过程文件当成最终结果；
- 测试记录与业务结果目录清晰分离；
- 第 24、25 页夹具测试通过；
- 失败时能指出失败发生在夹具、契约、链接、manifest、解析器、OCR 还是环境依赖；
- 报告中明确区分：
  - 规格静态校验；
  - 夹具契约测试；
  - 真实本地解析；
  - OCR；
  - mac96 视觉复核；
  - GPT-image-2；
- 不修改目标目录以外的文件。

## 最终交付格式

完成后只需汇报：

1. 检查或修改的目标目录内文件；
2. 测试记录路径；
3. 如果存在，真实业务结果包路径；
4. 两条测试命令的真实输出；
5. 已完成、未执行、未实现和待后续处理项；
6. 当前是否仍存在“测试记录被误认为转换结果”的风险。
