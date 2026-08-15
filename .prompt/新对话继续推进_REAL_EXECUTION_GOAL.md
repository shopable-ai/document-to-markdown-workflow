# 新对话继续推进：Workflow A 真实解析与视觉复核

请在新的 Codex 对话中继续处理目录：

`/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets`

## 原始需求

把来源 PPTX/PDF/DOCX/图片真实转换为可检索、可追溯的 Markdown 资产包，并保留来源视觉资产。Workflow A 必须实际执行解析、视觉复核和必要 OCR，不能只通过 fixture/contract 断言，更不能用手工编写的 Markdown 样例冒充转换结果。

本轮只推进 Workflow A，不实现或调用 Workflow B 的 GPT-image-2 生成。

Workflow A 的正式结果必须是：

```text
<output_root>/<package-name>/
├── document.md
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

## 本轮目标与验收

必须完成或明确阻断以下真实链路：

```text
PPTX/PDF 真实输入
→ A1/A2 原生结构提取
→ A3 页面/区域/嵌入资产生成
→ SSH/mac96 默认视觉复核
→ mac96 不可用时 Apple Vision OCR 兜底
→ 仍无法确认时 needs-review
→ A5 document.md
→ A6 manifest.yaml
→ A7 两份报告
```

验收必须能证明：

- 真实解析代码实际读取了固定夹具，而不是只读取 `expected/` 或已有 `tests/runs/` 样例。
- PPTX 第 24、25 页的 XML 文字、形状、分组、连线、坐标和层级至少形成可检查的 native-content-model。
- PDF 第 24、25 页的文本块、阅读顺序和页面布局至少形成可检查的布局证据。
- 页面图片中的文字/复杂关系确实经过 SSH/mac96 视觉复核；如果 mac96 不可用，必须实际运行 Apple Vision OCR，或明确记录失败并进入 `needs-review`。
- `document.md`、`manifest.yaml` 和资产文件由真实运行生成，并且报告记录真实执行路径、模型/引擎、输入、输出和失败原因。
- 手工样例和测试预期不能进入 `generated_business_outputs`；真实结果路径必须与 `tests/runs/<run-id>/` 测试记录分离。
- 不能声称完整 25 页文档已转换；当前固定范围只有原文档第 24、25 页。

## 任务拆分

### 1. 先定位“看起来正确”的来源

- 检查 `tests/run_fixture_contract_tests.py`、`tests/test_fixture_contract.py`，确认它们是否只做断言而不生成 Markdown。
- 对比以下文件并记录来源：
  - `tests/fixtures/18个工作思维模型-last-2-slides/expected/document.md`
  - `tests/runs/fixture-contract-20260814-0118/outputs/document-asset-package/document.md`
  - `tests/fixtures/18个工作思维模型-last-2-slides/expected/pptx-slide-24-native-text.txt`
  - `tests/fixtures/18个工作思维模型-last-2-slides/expected/pptx-slide-25-native-text.txt`
  - `tests/fixtures/18个工作思维模型-last-2-slides/expected/pdf-page-24-text.txt`
  - `tests/fixtures/18个工作思维模型-last-2-slides/expected/pdf-page-25-text.txt`
- 明确报告：当前 `document.md` 是手工 `fixture-sample`，不是测试命令生成的真实结果。

### 2. 盘点并实现最小真实 Tool 链

- 检查 `tool-registry.yaml` 中 `pptx-xml-parser`、`pdf-layout-parser`、`office-page-renderer`、`image-region-cropper`、`system-ocr`、`mac96-vision-endpoint`、`markdown-writer`、`manifest-writer` 的真实实现状态。
- 不要因为注册表里有 Tool 名称就假定实现存在；每个结论必须有实现路径或运行输出。
- 如果全量解析器过大，先实现固定第 24、25 页的最小真实适配器，但必须保留通用输入/输出契约，不能把一次性样例硬编码成生产解析器。

### 3. 按默认优先级执行视觉复核

- 原生结构提取完成后，先只读检查 `ssh mac96` 的当前连通性、服务和视觉模型标识。
- mac96 可用时，对第 24、25 页或必要复杂区域实际发送输入，返回结构化 JSON，记录：目标、模型、输入资产、时间、状态、关系结论和不确定项。
- mac96 不可用、超时、模型不支持或输出不可解析时，才运行本机 Apple Vision OCR；记录引擎、语言、版本、fallback 原因和候选置信度。
- 两者均失败或关系无法裁决时，保留来源视觉资产并标记 `needs-review`，不能用关键词断言或人工样例补齐。

### 4. 生成并验收正式结果

- 使用一个新的真实运行 ID 和独立的 `output_root`，不要覆盖或重命名旧的 `fixture-sample`。
- 生成 `document.md`、`manifest.yaml`、`assets/pages/`、`assets/regions/`、`assets/embedded/` 和两份报告。
- `document.md` 只放读者可读语义；坐标、模型调用细节、OCR 原文和失败原因放入 evidence/reports。
- 检查 Markdown 图片链接、manifest 路径、来源页码/区域、哈希和状态一致。

### 5. 验证与报告

运行并分别报告：

```bash
cd /Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets
python3 tests/validate_workflow_package.py

RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

除此之外，必须给出真实解析命令及其输出；如果真实 Tool 尚未实现，命令必须失败在明确的实现边界，并将 `real_business_result_package` 写为 `null`，不能把两条契约测试命令当成真实转换证明。

## 当前状态与证据

- 已确认：规格静态校验通过，fixture contract 测试通过。
- 已确认：`tests/runs/fixture-contract-20260814-0118/outputs/document-asset-package/` 的 Markdown、manifest、报告和页面图片带有 `fixture-sample` / `fixture-contract-only` 标记。
- 已确认：当前测试脚本没有调用 Markdown 生成器、manifest writer、SSH/mac96 或 Apple Vision OCR。
- 已确认：样例 Markdown 的主要语义与 `expected/` 文件、PPTX/PDF 预先提取文本高度一致，因此“看起来正确”不能证明本次执行过转换流程。
- 未完成：真实 PPTX/PDF 解析、页面渲染、视觉复核、Apple Vision OCR fallback、正式业务结果包。
- 待核实：当前 mac96 的 SSH、服务、模型和可接受的结构化输入/输出协议。

## 约束与未知项

- 只查看和修改上述目标目录，不读取或修改其他项目目录、原始下载目录或源文件。
- 不删除或覆盖旧的 fixture、expected 文件和测试记录；它们是对照证据，但不是业务结果。
- 不新增测试 Workflow 或测试 Skill，不创建 Workflow C。
- 不调用 GPT-image-2，不生成新的图片批次。
- 不泄露密码、Token、Cookie、Authorization 或订阅 URL。
- 不把 HTTP 200、模型列表、空响应、关键词存在或图片文件存在当成视觉复核成功。
- 原生文本、OCR 候选、mac96 结构结论和人工解释必须分层保存。

## 恢复起点

从“来源溯源”开始：先读取 `tests/run_fixture_contract_tests.py` 和 `tests/test_fixture_contract.py`，确认没有生成器调用；然后对比 `expected/document.md` 与 `tests/runs/.../document.md`。完成这一步后再检查 Tool 注册表和 `ssh mac96`，不要先修改 Markdown 样例。

## 交付要求

- 一份真实执行路径说明，解释此前结果为何看起来正确、但为何不是真实转换结果。
- 最小可运行的真实解析/复核实现，或明确、可复核的阻断报告。
- 新的真实运行记录路径；如果没有正式结果，明确 `real_business_result_package: null`。
- mac96 成功证据，或 mac96 失败后 Apple Vision OCR 的实际证据，或最终 `needs-review` 证据。
- 两条固定契约测试命令和真实解析命令的完整真实输出。
- 明确区分：规格静态校验、夹具契约、真实本地解析、OCR、mac96、GPT-image-2 和正式业务结果。

## 排除内容

- 不把 `tests/fixtures/.../expected/` 当成自动生成结果。
- 不把 `tests/runs/fixture-contract-20260814-0118/outputs/` 当成正式 `output_root`。
- 不因为现有 Markdown 语义“看起来正确”而跳过真实解析、视觉复核和来源追溯。
