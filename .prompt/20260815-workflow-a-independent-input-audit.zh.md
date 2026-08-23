原始需求：
请继续推进 `/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets` 的“文档转 Markdown”工作流。先从背景、目标、需求和用户问题写清楚，再把需求拆成高密度缩进任务树；不要只从现有流程图或已有解析结果倒推流程。

核心需求：
- 参考并校准 `Requirement → Chain → Spec → Task → Workflow → Skill/Tool → Run → Evidence → Verdict → Claim`。
- 在 `WORKFLOW-MAP.zh.md` 之前补上需求视角的任务功能树，解释每个阶段解决什么需求、成功信号是什么、出错怎样暴露。
- 先做单页、多页和候选来源的最小测试，再逐步扩展到全量：单页 PPTX、两页 PPTX、单页 PDF、两页 PDF、同名 PPTX+PDF、PPTX 部分排版错误+PDF。
- 同名 PPTX/PDF 明确声明为同一逻辑文档候选时：PPTX XML 优先作为语义/数字资产来源；比较 PPTX Office 渲染与 PDF 页面截图；差异大或 PPTX 渲染错乱时，PDF 页面图作为主视觉证据，同时保留 PPTX XML、对象/图表/媒体关系和错误渲染证据。
- 不把两个来源自动合并成一个业务 package；普通未声明候选仍按单一来源运行。
- 需要保持 `Unknown`、候选、`needs-review`、失败和证据等级边界，不能把 fixture/runner 自报状态当成真实业务通过。

本轮目标与验收：
目标：证明 Workflow A 对“不同输入文件”是来源独立的，并建立可执行的最小测试矩阵和候选视觉分流规则。
验收：
- `TASK-TREE.zh.md` 能从用户问题一路映射到测试、阶段、证据和 Claim，且包含单页→两页→候选→异常→全量的顺序。
- `workflow.yaml` 的 A1-A7 同时包含机器阶段 ID、读者语言、`user_problem`、`success_signal`、`failure_visible_as`。
- 运行器不注入任何固定页码、固定标题、固定正文、固定图表内容；解析结果只能来自当前 source、当前原生提取、当前 OCR/视觉响应或明确的 Unknown。
- `tests/fixtures/smoke-matrix/fixture.yaml` 覆盖六类最小输入；`tests/scripts/materialize_smoke_fixtures.py` 只生成测试输入，不创建生产输出。
- 候选比较结果至少能表达 `semantic_source`、`visual_source`、`visual_difference`、`selection_reason` 和 `uncertainties`；差异大/排版异常时 visual source 必须能落到 PDF。
- 执行并记录：
  - `python3 tests/validate_workflow_package.py`
  - `python3 -m pytest -q tests/test_repository_layout.py tests/test_real_output_root_validator.py tests/test_smoke_fixture_matrix.py tests/test_workflow_a_source_runner.py`
  - 若生成 smoke 输入，再对代表页运行候选比较器和对应独立验收器；未运行的真实 mac96/Apple Vision 证据必须标为待核实。

任务拆分：
- 1. 真实输入独立性审计
  - 1.1 `rg` 搜索 `adapters/`、`skills/`、`contracts/`、`tests/` 中的固定页码、固定标题、旧 fixture 文本和按页注入逻辑。
  - 1.2 对每一处分类：测试期望、通用启发式、来源特定泄漏；来源特定泄漏必须删除或改成当前输入证据驱动。
  - 1.3 增加“换输入不会继承旧语义”的回归测试；测试应使用与现有 fixture 无关的标题/正文。
  - 1.4 检查 Markdown 组装、验收器和 reclassify 是否仍有只针对某类旧文档的硬编码章节判断；能泛化就改为字段/结构判断。
- 2. 候选来源决策实现
  - 2.1 读取现有 `adapters/workflow_a_candidate_compare.py` 和 `contracts/source-inventory.yaml`，先确认当前输出字段与新规则一致。
  - 2.2 使用 PIL 或等价本地确定性方法比较代表页截图，记录尺寸归一化、差异指标、阈值和每页状态；不要用文字相似度代替截图比较。
  - 2.3 增加 PPTX 渲染异常信号（关键原生文字不可见、极端越界/遮挡、渲染失败）到选择理由。
  - 2.4 保留 `preferred_source` 兼容字段，同时新增 `semantic_source` 与 `visual_source`；明确 dispatcher/runner 如何消费混合来源证据。
  - 2.5 为“差异小”和“差异大/排版异常”各写一个确定性测试。
- 3. 最小夹具矩阵
  - 3.1 按 `tests/fixtures/smoke-matrix/fixture.yaml` 生成六类测试输入。
  - 3.2 单页通过后再两页；两页通过后才候选比较；候选通过后才接既有 24–25 页 fixture；不要直接跑全量掩盖局部错误。
  - 3.3 每类输入独立记录 source SHA-256、页数、输出根、状态和失败原因。
- 4. 证据与结论校验
  - 4.1 确认 `reports/failure-report.yaml` 即使无失败也存在。
  - 4.2 确认 XML/布局/OCR/视觉层分开保存，OCR candidate 不升级为 confirmed。
  - 4.3 确认 PDF 主视觉回退时，PPTX XML/对象/媒体关系仍在 evidence 或 manifest 中可追溯。
  - 4.4 只有独立 validator 通过才写 `passed`；否则给出 `needs-review` 或 `failed` 和下一步。

当前状态与证据：
- 已确认：项目已有 `workflow.yaml`、A1-A7、独立输出验收器、候选比较器、批处理器和 24–25 页 PPTX/PDF fixture。
- 已完成：在 `GOAL.zh.md` 写入背景/当前问题/需求链；新增 `TASK-TREE.zh.md`；在 `README.md` 与 `WORKFLOW-MAP.zh.md` 增加任务树入口；为 A1-A7 增加用户问题/成功信号/失败显现字段；新增 `tests/fixtures/smoke-matrix/` 元数据、说明和生成脚本。
- 已修复：旧 document-profile runner 已删除，固定页码/短语语义不再进入 active source runner。
- 已确认的边界：旧 OCR 页眉过滤、章节命名和验收正则只属于历史证据审计，不得重新接回当前 Workflow。
- 待核实：候选比较器还没有完成基于截图的 `visual_difference` 阈值实现；当前代码的默认选择逻辑与新的“PPTX XML 语义优先、PDF 视觉回退”规则可能不一致。
- 待核实：六类 smoke 输入尚未在本轮全部 materialize 并运行真实 runner；不要把生成脚本存在写成运行通过。

约束与未知项：
- 保留工作树中的用户已有修改，不 reset、stash、clean、force-push，不创建新 worktree/branch。
- 只修改当前项目范围；不把测试运行记录放进正式 `output_root`，不把 fixture 当生产结果。
- 不打印、保存或提交任何密码、token、cookie、Authorization、订阅 URL 或其他秘密。
- 不把 HTTP 200、`tools/list`、模型列表、队列接收或本地 fixture 结果当成 mac96 真实视觉证据。
- 不扩大到 GPT-image-2、DOCX 生产解析或复杂多来源合并；先完成输入独立性和 PPTX/PDF 候选视觉分流。

恢复起点：
从“真实输入独立性审计”继续。先执行固定字符串搜索并阅读 `_normalise_text_structure`、`classify_content`、`build_markdown`、`workflow_a_candidate_compare.py`、`workflow_a_output_validator.py`；然后实现候选截图差异证据，再 materialize 六类 smoke 输入。每个修复都先写/更新回归测试，再运行静态校验和 pytest。

交付要求：
- 更新后的任务树、Workflow 阶段字段、候选比较契约和测试矩阵文件。
- 运行器通用性修复及对应回归测试；明确列出删除了哪些来源特定注入。
- 候选比较 JSON 的实际样例或测试输出，包含语义来源、视觉来源、差异指标、选择理由和不确定项。
- 单页/两页/候选/排版异常的测试结果；每个未运行的真实外部复核门禁都要明确标记。
- 最终报告区分：已验证事实、待核实项、`passed`、`needs-review`、`failed`，并附绝对路径。

排除内容：
- 不继续把现有 24–25 页内容当作通用解析规则。
- 不用“已有输出看起来正确”替代对新输入的独立运行。
- 不在本轮实现自动合并 PPTX/PDF 为单一 package；只实现选择证据和视觉回退边界。
