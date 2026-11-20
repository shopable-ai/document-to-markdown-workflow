# 新对话任务：Workflow A 质量修复与同源候选单包转换

原始需求：

请在当前项目中优化并完善 Workflow A `document-to-markdown-assets`，然后用真实来源重新执行一次。当前确认下面两个文件是同一逻辑文档的两个格式候选，不应分别生成两个业务 package：

```text
/Users/a0000/Downloads/21个成长思维模型.pptx
/Users/a0000/Downloads/21个成长思维模型.pdf
```

先修复流程和相关说明，再进行真实转换。最终只保留一个经过候选比较选出的主来源 package；另一个来源只能作为候选比较和差异证据，不能把它的页面图、文本层或排版结果套到主来源上。

本轮只执行 Workflow A，不实现或调用 Workflow B，不调用 GPT-image-2，不生成新图片批次。

## 本轮目标与验收

目标：

1. 修复 PPTX 页面资产的真实渲染链路，使 `assets/pages/` 中的每一页都保留原 PPTX 页面可见的文字、图片、图形、连线、背景和层级。页面图不能退化为只有背景的图片，也不能用单独的嵌入媒体替代整页渲染。
2. 修复 PPTX/PDF 的内容组装，使原生结构和布局事实先于 OCR，Markdown 具有标题层级、主题、列表、表格/分区和可读顺序，而不是把 OCR 字符逐行堆在一起。
3. 将读者-facing 内容与工程证据分层：`document.md` 只保留整理后的文档语义和必要的原始页面图链接；来源指纹、bbox、对象 ID、字体、渲染命令、视觉关系候选、OCR 候选、不确定性、fallback、模型调用和失败诊断全部放入 `evidence/`、`manifest.yaml` 或 `reports/`。
4. 对同源 PPTX/PDF 先进行结构化来源候选比较，选择一个 `preferred_source`，再只对选中的来源进行完整转换和逐页视觉复核，避免把同一文档完整处理两遍。

验收：

- 新的真实运行只生成一个业务 package，覆盖所选来源全部页面；旧 `runs/real/`、fixture 和 expected 不被覆盖。
- PPTX 页面图来自 PPTX 自身的 Office/LibreOffice 渲染，PDF 页面图来自原始 PDF 渲染；两者不能交叉冒充证据。
- 任选至少 5 个代表页（含封面、目录、复杂图、含嵌入图片页和第 33 页）人工查看页面 PNG，确认文字真实可见且没有“只剩背景”的缺陷；再对全量页面做尺寸、存在性和链接校验。
- `document.md` 中不出现工程模板标题或字段：`标题下正文文字块`、`视觉关系`、`来源与不确定性`、`字符`、`A4 状态`、原生 bbox/对象 ID/模型调用等。需要表达图示语义时，直接整理为读者可读的标题、段落、列表或表格，不暴露内部证据字段。
- 页面标题与标题下主题/对比内容分开。例如第 33 页必须呈现为页面标题 `人活着其实就两件事`，其下为 `搞定自己 vs 搞定别人`，不能拼成一个标题，也不能把整页 OCR 字符平铺。
- 真实包独立业务验收器通过，规格静态校验通过，fixture 契约测试通过；报告中的状态、失败、fallback 和模型 token 统计与实际调用一致。

## 任务拆分

- 1. 先做审计和失败复现
  - 阅读根目录 `AGENTS.md`、`.prompt/README.zh.md`、当前 Workflow A 的 `workflow.yaml`、README、ACCEPTANCE、注册表和 contracts。
  - 检查 `adapters/workflow_a_source_runner.py`、`adapters/workflow_a_output_validator.py`、`tests/run_fixture_contract_tests.py`、`tests/test_fixture_contract.py`。
  - 对 PPTX 先只运行代表页，打开/检查生成的 page PNG，定位文字缺失发生在 Office 渲染、PNG 复制、嵌入媒体提取还是 Markdown 链接层；不要用“文件存在”代替视觉证据。
  - 记录现有真实运行只是诊断样本：`runs/real/real-runs-20260814-independent-sources-final-v3/`，不得复制其 `document.md` 或报告作为新结果。

- 2. 修复 PPTX 原生解析与渲染
  - A1/A2 真实读取 OOXML：页、段落、文字、形状、图片、分组、连接线、对象 ID、坐标、父子层级、z-order、媒体关系、主题和母版。
  - A3 使用已注册的本机 Office 渲染器，记录命令、版本、退出码、stderr 和失败页码。确认 LibreOffice 输出页面包含页面上的文字和矢量对象；如果缺字，修复渲染链路或将运行标为 `needs-review`，不能声称 complete。
  - `ppt/media` 中被目标页实际引用的媒体复制到 `assets/embedded/`，但嵌入媒体只是独立资产，不能代替包含文字的整页 PNG。
  - 记录主题字体、实际使用字体、`fc-match` 结果、字体替代/溢出/错位/连线异常和受影响页码；字体诊断不能改写 XML 事实。

- 3. 修复 PDF 原生布局链路
  - A1/A2 读取 PDF 文本层和布局：文本块、阅读顺序、bbox、字号估计、页尺寸、字符/图片/线/矩形统计。
  - A3 页面图只能来自原始 PDF 页面渲染。OCR 只能作为无文本层或冲突时的候选，不能覆盖可信的 PDF 文本层。

- 4. 修复来源候选比较和成本控制
  - 因两份输入是同一逻辑文档，运行结构化候选比较 JSON。至少比较文字覆盖、字体/换行、裁切、页面排版、图形/连线关系、图片清晰度和渲染异常。
  - 比较结果记录候选路径、SHA-256、页数、代表页资产、模型、时间、各维度结论、`preferred_source`、选择理由和不确定项。
  - 先比较代表页；只有结论不确定时才扩大比较范围。选择主来源后，只对主来源执行完整 A1-A7 和全量逐页视觉复核。
  - mac96 请求必须返回可解析 JSON，并记录真实模型、请求次数、输入资产和 prompt/eval token 计数。mac96 不可达或不可解析时按规定 fallback 到 Apple Vision，再失败则 `needs-review`。

- 5. 修复 Markdown 语义组装和契约
  - 重新定义 `document.md` 的读者层模板：文档标题 -> 章节/页面标题 -> 主题/副标题 -> 整理后的正文、列表、表格或图示说明 -> 必要的原始页面图。
  - 删除把内部阶段字段直接写入 Markdown 的逻辑。工程证据写入 JSON/YAML；读者层不要出现 `视觉关系`、`来源与不确定性` 等诊断标题。
  - 保留原生文本与 OCR/视觉候选的差异证据，但只把已裁决的语义写入正文；无法裁决的内容进入 `Unknown`/`needs-review` 证据，不要用猜测补齐。
  - 更新 README、ACCEPTANCE、contracts、注册表和测试，使新分层成为正式契约，而不是临时提示词行为。

- 6. 回归测试与真实运行
  - 增加/修复测试：PPTX page PNG 有文字像素和正确尺寸；嵌入媒体与整页图不混淆；原生结构不被 OCR 覆盖；`document.md` 不含工程字段；同源候选只生成一个 package；全页覆盖和来源指纹一致。
  - 使用新的运行 ID，例如 `runs/real/real-runs-<new-id>/`，不得覆盖旧运行。
  - 对最终 package 运行：

```bash
python3 adapters/workflow_a_output_validator.py \
  runs/real/<new-id>/<selected-package>

python3 tests/validate_workflow_package.py

RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/run_fixture_contract_tests.py
```

  - 再运行项目要求的 pytest，并保存完整 stdout、退出码和报告路径。

## 当前状态与证据

- 已确认：现有运行对 PDF 和 PPTX 各生成了一个 33 页 package；这次结果可作为问题复现和证据参考，不作为新业务结果。
- 已确认：现有 `document.md` 直接输出了 `标题下正文文字块`、`视觉关系`、`来源与不确定性` 等内部字段，需修复。
- 已确认：现有 package 报告的 `status: complete/passed` 不能单独证明页面视觉质量；必须重新查看页面 PNG 并补充回归门禁。
- 待核实：PPTX 文字缺失的具体层级（Office 渲染、资产复制、查看器或 Markdown 引用）；必须用代表页复现后再修复。
- 待核实：PPTX 与 PDF 的逐页视觉差异和最终 `preferred_source`；不能根据扩展名或关键词数量直接选择。

## 约束与未知项

- 只修改当前 Workflow A 目录和本项目文件；不修改 `/Users/a0000/Downloads/` 的源文件。
- 不删除或覆盖旧 fixture、expected、`tests/runs/` 或旧真实运行记录。
- 不调用 Workflow B、GPT-image-2 或在线图像生成服务。
- 不把 HTTP 200、模型列表、图片存在、关键词数量或非空响应当作视觉复核成功。
- 如果完整页面无法真实解析、渲染或复核，保留原图并标记 `needs-review`，不能写 `complete`。

## 交付要求

1. 列出实际修改的文件和入口，并说明新的推荐调用方式。
2. 给出新的单一主来源 package 路径、`document.md`、manifest、报告和证据路径。
3. 说明 PPTX 页面文字可见性检查结果、PDF/PPTX 原生解析状态、来源候选比较结果、mac96/Apple Vision 状态和实际 token 统计。
4. 展示 `document.md` 的代表页片段，证明读者层没有混入工程字段，并证明第 33 页标题与标题下对比主题已分离。
5. 给出业务包验收器、规格静态校验、fixture 契约测试和 pytest 的完整退出码/关键输出。
6. 单独列出仍需人工复核、字体风险、未决关系和任何未实现范围；不要把旧运行的 `passed` 继承为新结论。

## 排除内容

- 不将旧的双 package 结果合并、重命名或包装成新的业务结果。
- 不为了满足目录结构而生成无意义的区域截图；整页图足够时 `assets/regions/` 保持为空。
- 不用 OCR 重写 PPTX XML 或 PDF 文本层，不把工程诊断字段继续塞回 `document.md`。
