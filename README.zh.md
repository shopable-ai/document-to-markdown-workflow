# 文档资产化与 Markdown 视觉生成工作流

这是一个规格包，定义两个可独立运行的 Workflow：

- `document-to-markdown-assets`：来源文档 → Markdown + 来源视觉资产。
- `markdown-to-gpt-image`：Markdown 局部内容 + 可选参考资产 → GPT-image-2 单张图片。
- `knowledge-map-image-to-md`：早期名称的兼容入口 → Workflow A + 知识地图差异策略。

它们不是一条必须连续执行的流水线。Workflow A 建立内容和页面结构可追溯的 Markdown 资产；Workflow B 可以消费任意已有 Markdown，也可以选择性使用 A 产生的视觉资产。已有转换结果的转换保真度审计属于原工作流的后续阶段，不另立 Workflow。

## 从哪里开始读

按你的目的选择入口，不要把所有文件当成同一层说明：

1. 第一次进入项目：先读本文件，了解稳定目录、Workflow 边界和证据边界。
2. 需求拆解：先读 [需求到证据任务树](TASK-TREE.zh.md)，把用户问题、测试范围、失败可见性和可声称结论对齐。
3. 选择流程：读 [WORKFLOWS.zh.md](WORKFLOWS.zh.md)，只比较 A/B 的适用场景和主要产物。
4. 理解链路：再读 [WORKFLOW-MAP.zh.md](WORKFLOW-MAP.zh.md)，查看任务树如何落到阶段、Skill、Tool、契约和证据。
5. 理解执行拆分：读 [EXECUTION-TASK-TREE.zh.md](EXECUTION-TASK-TREE.zh.md)，先看单页纵向切片，再看多页、多文件、测试与旧 A1–A7/Skill 拆分复盘。
6. 执行 Workflow A：读 [Workflow A 人工入口](workflows/document-to-markdown-assets/README.zh.md)，需要逐步执行或排障时再读 [Workflow A 执行指南](WORKFLOW-A-EXECUTION.zh.md)。
7. 机器路由与验收：执行器只读取 [workflow-registry.yaml](workflow-registry.yaml)、对应 `workflow.yaml` 和 contracts；人工 README 不覆盖机器契约。

人工说明解释“为什么、何时、如何阅读”；YAML 和 validator 决定“字段、顺序、状态是否合规”。

## 目录

```text
workflow-document-markdown-visual-assets/
├── AGENTS.md
├── GOAL.zh.md
├── TASK-TREE.zh.md           # 需求→任务→证据的前置功能树
├── EXECUTION-TASK-TREE.zh.md  # 工程 Task 的纵向切片与规模扩展树
├── README.zh.md
├── WORKFLOWS.zh.md
├── WORKFLOW-MAP.zh.md
├── workflow-registry.yaml
├── .prompt/                  # 临时提示词、handoff 和续接目标
├── .audit/                   # 本地临时审计快照（默认忽略）
├── runs/real/                # 本地真实运行证据（默认忽略）
├── workflows/
│   ├── document-to-markdown-assets/
│   │   ├── README.zh.md
│   │   ├── workflow.yaml
│   │   └── ACCEPTANCE.zh.md
│   ├── markdown-to-gpt-image/
│   │   ├── README.zh.md
│   │   ├── workflow.yaml
│   │   └── ACCEPTANCE.zh.md
├── skills/
│   ├── document-asset-extraction/
│   │   ├── SKILL.md
│   │   └── agents/openai.yaml
│   ├── document-visual-generation/
│   │   ├── SKILL.md
│   │   └── agents/openai.yaml
│   ├── knowledge-map-image-to-md/
│   │   ├── SKILL.md
│   │   └── agents/openai.yaml
│   ├── semantic-visual-asset-pruning/
│   │   ├── SKILL.md
│   │   ├── scripts/prune_visual_assets.py
│   │   ├── scripts/classify_visual_assets_mac96.py
│   │   └── agents/openai.yaml
│   ├── source-inventory/ …
│   └── generated-image-review/ …
├── skill-registry.yaml
├── tool-registry.yaml
├── ARCHITECTURE.zh.md
├── references/
│   ├── legacy-code-status.zh.md # 遗留、兼容和 fixture 脚本边界
│   ├── visual-reconstruction-profile.zh.md # 复杂图片结构/提示词保存规则
│   └── prompts/                 # 版本化的通用模型提示模板
├── contracts/
│   ├── knowledge-map-profile.yaml
│   ├── workflow-handoff.yaml
│   ├── workflow-run-record.yaml
│   ├── workflow-b-output-layout.yaml
│   ├── markdown-asset-package.yaml
│   ├── source-visual-asset.yaml
│   ├── visual-asset-pruning.yaml
│   ├── visual-asset-classification.yaml
│   ├── visual-reconstruction-profile.yaml
│   └── gpt-image-generation-request.yaml
└── references/
    ├── knowledge-map-profile.zh.md
    ├── failure-states.zh.md
    ├── known-failures.zh.md
    └── naming-policy.zh.md
```

历史技能快照不参与运行路由，保存在：

```text
archive/legacy-skills/knowledge-map-image-to-md/
├── SKILL.md
├── README.zh.md
└── snapshot.yaml
```

阶段、Skill 和 Tool 不是同一层：A1–A7、B1–B7 是阶段；阶段引用的每个 `skills: [...]` 都对应一个独立 `SKILL.md`；确定性代码、系统 OCR、渲染器和 GPT-image-2 接口则登记在 `tool-registry.yaml`。兼容入口不属于阶段，也不能成为第二套流程。已有转换结果审计由原工作流目录内的后续阶段和提示词承载。详见 [ARCHITECTURE.zh.md](ARCHITECTURE.zh.md)。

当前已为两个 Workflow 引用的细粒度 Skill 建立独立目录和 `SKILL.md`，并保留入口级复合 Skill。早期的 `knowledge-map-image-to-md` 已登记为兼容入口；它只路由到 Workflow A，不复制 A1-A7。知识地图的语义资格、重复预检、版本仲裁和来源保真增量见 [知识地图差异策略](references/knowledge-map-profile.zh.md)。A1–A7、B1–B7 是阶段门禁。

早期 131 行原文件已原样保存到 [archive/legacy-skills/knowledge-map-image-to-md](archive/legacy-skills/knowledge-map-image-to-md)，仅用于历史审计，不进入 Skill 注册表；本次没有删除 `/Users/a0000/.codex/skills/knowledge-map-image-to-md/SKILL.md`。

根目录的 [WORKFLOWS.zh.md](WORKFLOWS.zh.md) 是两个 Workflow 的选择和关系总览；[workflow-registry.yaml](workflow-registry.yaml) 是机器可读入口索引。详细阶段仍只保存在对应 Workflow 子目录，避免根目录重复维护。

如果需要快速理解文件之间的调研和执行关系，先看 [WORKFLOW-MAP.zh.md](WORKFLOW-MAP.zh.md)。遗漏环节与横向门禁见 [references/omitted-gates.zh.md](references/omitted-gates.zh.md)。

下一轮在本目录继续优化时，使用 [.prompt/新对话继续推进_GOAL.md](.prompt/新对话继续推进_GOAL.md)。临时提示词、审计快照和真实运行目录的边界见 [AGENTS.md](AGENTS.md)。



每个 Workflow 目录包含三类文件：`README.zh.md` 面向人类使用者，`workflow.yaml` 面向路由与校验器，`ACCEPTANCE.zh.md` 面向质量验收。三者互补，不互相替代。

## 如何实际运行

不要把 `tests/runs/` 里的 fixture-sample 当作转换结果。一次真实 Workflow A 运行必须按以下顺序执行，并在 `reports/processing-report.yaml` 写明实际路径：

日常请求只需提供自然语言、来源和输出目录：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

内部入口和输出格式由 `AGENTS.md`、注册表、Workflow 文件和 contracts 解析，不要求用户重复填写。

当前 active runner 是 `adapters/workflow_a_source_runner.py`：默认 `--plan-only`，并支持
不调用 legacy 的确定性 `--execute`。当前页面修复试运行不重新执行该 runner，而是只处理已有
`index.md` 的第 1 页独立单元；不调用模型/OCR。旧
`archive/legacy-adapters/workflow_a_runner.py` 仅保留为 `legacy/document-profile` 兼容入口，
固定标题、页码、术语、OCR 区域、DPI 和 mac96/SSH/Ollama 逻辑不得扩散到 generic path。
此前历史测试结果不能作为本轮页面修复或全量处理已通过的证据。

```text
单一来源文档（后续 execute 阶段）
  → source runner 读取 source/profile/config，生成来源无关执行计划
  → dispatcher/batch 在 generic execute 被单独实现并测试后，记录 workflow 与 A1-A7 执行链
  → A1/A2 原生 PPTX XML、PDF 布局或 DOCX OOXML 提取
  → A3 页面/按需区域/嵌入视觉资产落盘
  → A4 原生结构先行
      ├─ 默认：vision-page-analysis 以 full + single 一次输出 Markdown 和视觉结构
      ├─ 高风险页面：显式传 dual，分别输出 text 和 layout
      ├─ 文字层失败：当前对话视觉模型或 Apple Vision OCR 只补文字
      └─ 视觉层失败：保留 unavailable/needs-review，不用 OCR 伪造关系
      → 真实冲突或关系未裁决时才使用 `needs-review`
      → 复杂结构/重建意图：visual-reconstruction-profile（结构版 + 提示词版）
  → A5 index.md
  → A6 manifest.yaml
  → A7 processing-report.yaml + acceptance-report.yaml + failure-report.yaml
  → 独立验收器 → reports/validation-result.json → passed / needs-review / failed
```

固定夹具命令只验证历史输入范围和目录契约；本轮的转换包与测试记录位于
`tests/runs/source-runner-test-20260816-0022/`，不作为生产结果。完整测试已通过；仍不调用
SSH/mac96、在线模型或 OCR。

最终是否正确，以资产包内 `reports/validation-result.json` 的状态为准：

- `passed`：可继续使用 `index.md` 和 `assets/`；
- `needs-review`：先根据报告和页面资产人工复核；
- `failed`：不能声称转换完成。

执行优先级和降级规则见 [WORKFLOW-A-EXECUTION.zh.md](WORKFLOW-A-EXECUTION.zh.md)；新对话续接提示词见 [tests/prompts/continue-workflow-a-mac96-first.zh.md](tests/prompts/continue-workflow-a-mac96-first.zh.md)。

## 当前交付边界

- 已交付：页面独立处理契约、阶段门、确定性拆分/合并能力，以及第 1 页试运行闭环。
- 第 1 页：独立结构验收和人工可读性检查通过；关系状态仍为 `needs-review`。
- 尚未进入：第 2–5 页小批量正文修复、第 2–5 页人工复核、第 6–100 页批量修复。
- 暂不执行：真实 source 转换、在线基准、本地视觉校准、质量比较、OCR、Workflow A runner 和真实 package validator。
- 未交付：DOCX 生产解析器、完整 Apple Vision fallback 的真实运行覆盖、独立语言质量修复适配器、逐阶段实时心跳/硬停止预算、GPT-image-2 供应商适配器。
- `tool-registry.yaml` 中标记 `planned` 的适配器仍不能作为已实现能力声明。
- Skill 文件已经建立；Tool 注册表中标记 `planned` 的适配器仍需后续实现。
- 视觉资产语义筛选是 A5/A6 的内容级清洗：只移除读者层图片链接和 section 绑定，不删除 `assets/` 中的来源资产、manifest 资产或来源出现记录；未获批准的页码可通过 mac96 分类脚本形成 `candidate-only` 报告。

## 设计原则

1. 原生结构优先，局域网 mac96 视觉转写主路径，当前对话在线视觉模型是 mac96 失败后的第二路径，Apple Vision OCR 只做最后兜底。
2. 文字层与视觉层并行保留，复杂图不扁平化。
3. 落盘文件名使用短英文语义 `file_name`，阅读名称使用中文 `display_name`；稳定 ID/来源指纹/哈希、asset kind 和 ordinal 只放 manifest，原始文件名只作为来源证据。正式包使用扁平 `assets/`，类型由 manifest 区分，读者层复制无需再穿过 `pages/`。
4. 事实、候选、Unknown 和 needs-review 分开保存。
5. B 只生成一张图片，不生成 PPTX、PDF 或其他文件集合。

## 测试输入选择

完整的 Workflow A fixture 测试仍可使用独立的 PPTX 与 PDF 夹具验证 XML/布局契约，
但生产运行必须一个来源一个 package。详见 [tests/TESTING.zh.md](tests/TESTING.zh.md)。
