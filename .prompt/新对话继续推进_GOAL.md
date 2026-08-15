# 新对话继续推进：完善源文档到 Markdown 的转换保真度

请在以下目录继续工作：

`/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets`

## 原始业务目标

建立一套稳定的文档转换流程，把 PPTX、PDF、DOCX 或图片中的原有内容转换成 Markdown，并尽量保持源文件中的：

- 文字内容和原意；
- 页码、章节、分区和层级；
- 表格、列表、分支和多层结构；
- 流程、箭头、循环、矩阵、树状关系和并列关系；
- 页面原图、嵌入图片和复杂区域截图；
- 内容、视觉资产和来源页码/区域之间的对应关系。

转换结果需要同时具备：

```text
Markdown 内容表达
+ 来源页面和复杂区域视觉资产
+ manifest、处理报告和验收报告
```

## 当前要完成的工作

当前目录已经有一套 Workflow A 规格，也有《18个工作思维模型》的已有转换结果和测试夹具。本轮请围绕“转换保真度”继续完善：

1. 检查已有 Markdown 是否完整表达源文档的文字、页面结构和图形关系。
2. 识别转换过程中可能出现的遗漏、压缩、错位、误读、来源混淆和视觉资产断链。
3. 对发现的问题记录来源证据、影响范围、修复动作和验收条件。
4. 对每页或每个受影响区域进行评分，优先修复内容缺失和结构关系错误。
5. 修改 Markdown、资产登记或相关转换规则后，重新核对修改结果与源文档的对应关系。
6. 对暂时无法确认的内容保留原始视觉证据，并记录 `Unknown` 或 `needs-review`。

## 审计问题和评分依据

请先阅读：

`references/markdown-quality-audit.zh.md`

审计至少覆盖以下七个方向：

1. 来源保护与证据可追溯；
2. 页面与章节结构映射；
3. 内容完整性与原意保留；
4. 图形结构保真；
5. 来源、版权与不确定性边界；
6. 视觉资产、命名、链接和来源出现登记；
7. 修复复核、测试和局部恢复。

每页目标为 100 分，验收要求为：

- 总分至少 95 分；
- 每个评分方向达到独立通过线；
- 内容完整性和图形结构保真达到各自满分的 85% 以上；
- 关键内容、复杂图关系、来源边界和视觉资产不能存在一票否决问题。

评分的目的，是决定下一项修复任务和确认修复是否有效；不能用总分掩盖关键内容缺失或关系错误。

## 问题记录格式

每个问题都要能回答：源文档哪里有证据、转换结果哪里不一致、会造成什么影响、准备怎样修复、怎样证明修复完成。

```yaml
task_id: page-05-content-001
page_id: page-05
dimension: content_completeness
problem: 原图中的细分结论没有进入 Markdown
evidence: 原始页面右侧有七个结论项，当前结果只有前三项
risk: 源文档的重要结论无法逐项对应
action: 补齐缺失内容，保留原图，并标记仍需核对的文字
acceptance: 七个结论项均能回到来源证据，未引入来源外内容
status: open
```

## 当前目录中的正式范围

当前保留两个独立 Workflow：

- `document-to-markdown-assets`：来源文档转换为 Markdown 资产包和来源视觉资产；
- `markdown-to-gpt-image`：已有 Markdown 局部内容生成单张图片。

本轮只完善 Workflow A 的转换保真度。旧目录中的 S0–S6、P0–P8 用作审计和恢复规则参考；新目录的正式主链路仍是 A1–A7：来源预检、原生提取、视觉资产保留、结构复核、Markdown 组装、来源登记、资产包验收。

## 可参考的旧资料

旧目录：

`/Users/a0000/Documents/workspace_agent/workflow/workflow-pptx-pdf-human-readable`

优先参考：

- `skills/pptx-pdf-human-readable/SKILL.md`：转换保真度审计方向；
- `03_执行结果/九维度评分与复审记录.md`：评分维度和通过线；
- `03_执行结果/优化任务与复审记录.md`：问题记录和修复示例；
- `00_已验证转换基线.md`：常见转换偏差；
- `01_工作流框架与执行链路.md`、`02_页面状态与任务契约.md`：恢复和证据规则。

旧目录中的具体页面分数、历史 `accepted=20`、旧 PASS 和历史运行结果只作为规则参考。

## 当前执行顺序

1. 阅读本提示词和 `references/markdown-quality-audit.zh.md`。
2. 阅读 Workflow A 的 `workflow.yaml`、README、ACCEPTANCE，以及 A5/A6/A7 相关 Skill 和契约。
3. 对照旧目录资料，补齐转换保真度、评分、修复和复核规则。
4. 使用固定夹具检查第 24、25 页的文字、结构和视觉资产对应关系。
5. 运行规格校验和 fixture contract 测试，并分别说明它们证明的范围。
6. 报告已完成的规则、尚未实现的 Tool、尚未执行的真实解析/视觉复核，以及下一步最小实现入口。

## 结果边界

- 正式业务结果是 `document.md`、`manifest.yaml`、`assets/` 和 `reports/` 组成的资产包。
- `tests/runs/` 和 `fixture-sample` 是测试记录或样例，不是正式转换结果。
- 规格 PASS、fixture PASS、历史评分和历史运行结果不能替代真实转换证据。
- 解析器、OCR、mac96 或其他 Tool 尚未实际运行时，按未执行或 `needs-review` 报告。
- 不创建第三个 Workflow，不调用 Workflow B 的 GPT-image-2，不修改源文件或无关目录。
