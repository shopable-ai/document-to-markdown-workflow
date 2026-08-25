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
7. 已完成路线检测并要批量跑 `native-only` PDF：读 [局域网原生 PDF 批量转换说明](references/lan-pdf-markdown-batch.zh.md)。
8. 机器路由与验收：执行器只读取 [workflow-registry.yaml](workflow-registry.yaml)、对应 `workflow.yaml` 和 contracts；人工 README 不覆盖机器契约。

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
├── runs/                     # 正式转换运行证据与输出包（默认忽略）
├── runs/pdf-route-detection/ # PDF 路线检测证据（默认忽略）
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

不要把 `tests/runs/` 里的 fixture-sample 当作转换结果。一次真实 Workflow A 默认运行交付关键点 `index.md`；只有页面含 Markdown 无法替代的语义视觉时才附带实际引用的 `assets/` 图片。纯文本结果可只有 `index.md`，不会在输出目录生成 JSON、YAML、manifest、reports、evidence 或 page-units。

日常请求只需提供自然语言、来源和输出目录：

> 文档转 Markdown：来源 `<SOURCE>`；输出到 `<OUTPUT_ROOT>`；页码全部。

内部入口和输出格式由 `AGENTS.md`、注册表、Workflow 文件和 contracts 解析，不要求用户重复填写。

如果已经有 `PDF转换路线总览.md` 或 `原生文本路线.txt`，并且目标是批量执行原生文本路线，不要再手工循环单文件命令；直接使用 [局域网原生 PDF 批量转换说明](references/lan-pdf-markdown-batch.zh.md) 中的 `adapters/lan_pdf_markdown_batch.py`。

当前 active runner 是 `adapters/workflow_a_source_runner.py`：普通单源转换直接使用确定性 `--execute`，
`--plan-only` 仅用于显式诊断；它不调用 legacy 或 optimized runner。当前页面修复试运行不重新执行该 runner，而是只处理已有
`index.md` 的第 1 页独立单元；不调用模型/OCR。旧
旧 document-profile runner 已删除并从 Workflow、工具注册表和批处理路由排除；固定标题、页码、术语、
OCR 区域、DPI 和 mac96/SSH/Ollama 逻辑不得扩散到 generic path。
此前历史测试结果不能作为本轮页面修复或全量处理已通过的证据。

```text
单一来源文档（后续 execute 阶段）
  → source runner 读取当前 source 和固定 generic profile，直接生成来源无关 package
  → 只有显式候选比较或批处理场景才进入 dispatcher/batch envelope
  → A1/A2 原生 PPTX XML、PDF 布局或 DOCX OOXML 提取
  → A3 页面渲染供视觉模型读取（reader-only 只复制真正需要引用的图片）
  → A4 原生结构先行（内部临时处理，不写工程 JSON）
      ├─ 默认：vision-page-analysis 以 full + single 一次输出 Markdown 和视觉结构
      ├─ 高风险页面：显式传 dual，分别输出 text 和 layout
      ├─ 文字层失败：当前对话视觉模型或 Apple Vision OCR 只补文字
      └─ 视觉层失败：保留 unavailable/needs-review，不用 OCR 伪造关系
      → 真实冲突或关系未裁决时才使用 `needs-review`
      → 复杂结构/重建意图：visual-reconstruction-profile（结构版 + 提示词版）
  → A5 page-units 批量确定性页眉页脚清洗 → 逐页语言修复 → document-temp.md
      → heading repair 与合并后保守格式化（提示词约束只作为整理边界，不改图片路径/页序）
      → 可按 .env 选择局域网模型、Codex CLI profile/default 或确定性降级
      → 最终只落一份正式稿 index.md
  → A6 可选 assets/（只复制 index.md 引用且有语义增量的图片）
  → A7 结构守卫（stdout 摘要）
```

如确实需要完整工程登记和诊断证据，必须显式加 `--engineering-output`；这不是默认生产输出：

```bash
python3 adapters/workflow_a_source_runner.py \
  --source "/absolute/path/input.pdf" \
  --output-root "/absolute/path/output-root" \
  --pages all --execute --engineering-output
```

固定夹具命令只验证历史输入范围和目录契约；本轮的转换包与测试记录位于
`tests/runs/source-runner-test-20260816-0022/`，不作为生产结果。完整测试已通过；仍不调用
SSH/mac96、在线模型或 OCR。

默认读者包的结构是否完整，以命令 stdout 摘要和 `index.md` 的图片链接检查为准：

- `passed`：`index.md` 已写入，且其中任何图片引用均可解析；
- `needs-review`：先人工复核关键点和仍需保留的图片；
- `failed`：不能声称转换完成。

执行优先级和降级规则见 [WORKFLOW-A-EXECUTION.zh.md](WORKFLOW-A-EXECUTION.zh.md)；新对话续接提示词见 [tests/prompts/continue-workflow-a-mac96-first.zh.md](tests/prompts/continue-workflow-a-mac96-first.zh.md)。
合并后模型格式化的环境变量、provider profile、回退和审计规则见
[`references/model-provider-configuration.zh.md`](references/model-provider-configuration.zh.md)。

## 当前交付边界

- 已交付：页面独立处理契约、动态 `total_pages` 阶段门、同一资产包内的渲染/识别断点续跑、确定性拆分/合并能力和逐页运行账本。
- 当前真实包：第 1 页及第 2–5 页小批量的结构守卫和人工可读性检查已通过；已完成页在来源指纹与页面图哈希匹配时直接复用，不重复调用视觉模型。
- 当前未完成：第 6–total_pages 页仍须在精确来源可用且前置小批量 Gate 通过后续跑；部分覆盖的包必须保持 `needs-review`，不得把阶段完成表述成整本文档完成。
- 已执行：当前对话在线视觉候选路线的隔离式逐页回退、真实 package validator 和完整 pytest；实际 provider、失败尝试、耗时与 token/eval 可用性按页记录，无法取得的字段保持 `Unknown`。
- 未交付：DOCX 生产解析器、完整 Apple Vision fallback 的真实运行覆盖、逐阶段实时心跳/硬停止预算、GPT-image-2 供应商适配器。
- 已实现但不等于人工验收：`clean-page-markdown-noise` 确定性页眉页脚清洗和局域网语言质量修复适配器；工程路径会先批量清洗 page-units，再逐页执行语言修复并保留各自证据。
- 已实现可选的 `prune-low-value-ebook-pages` 知识库后置清洗：必须等待全部 page-units 分组复制/汇合完成，先生成无正文副本的哈希锁定计划，再按已批准页码删除目录、版权、推广等整页低价值内容；清洗后必须由保留页重建索引或合并全文。
- `tool-registry.yaml` 中标记 `planned` 的适配器仍不能作为已实现能力声明。
- Skill 文件已经建立；Tool 注册表中标记 `planned` 的适配器仍需后续实现。
- 视觉资产语义筛选是 A5/A6 的内容级清洗：只移除读者层图片链接和 section 绑定，不删除 `assets/` 中的来源资产、manifest 资产或来源出现记录；未获批准的页码可通过 mac96 分类脚本形成 `candidate-only` 报告。

## 设计原则

1. 原生结构优先；扫描文字页把 Apple Vision OCR 加确定性结构重建、mac96 小视觉模型和 mac96 30B 作为候选，用真实页面质量、耗时和资源证据选路由，不设固定主次。
2. 文字层与视觉层并行保留，复杂图不扁平化。
3. 默认输出保留 `index.md` 和可选的扁平 `assets/` 读者图片；纯文本页不附整页截图，二维码不作为图片提取。稳定 ID/来源指纹/哈希、asset kind 和 ordinal 不落盘到读者包。需要完整追溯时再显式使用 `--engineering-output`。
4. 事实、候选、Unknown 和 needs-review 分开保存。
5. B 只生成一张图片，不生成 PPTX、PDF 或其他文件集合。

## 测试输入选择

完整的 Workflow A fixture 测试仍可使用独立的 PPTX 与 PDF 夹具验证 XML/布局契约，
但生产运行必须一个来源一个 package。详见 [tests/TESTING.zh.md](tests/TESTING.zh.md)。
