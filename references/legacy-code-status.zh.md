# 遗留代码与兼容逻辑状态

这份登记防止“以前跑通过”被误读成“适合所有新来源”。遗留项可以用于恢复旧证据或夹具测试，但不能作为新来源的默认生产入口。

## 本轮审查结论：`workflow_a_runner.py`

`archive/legacy-adapters/workflow_a_runner.py` 同时承担了通用的文件读写/哈希/页码处理和一个旧“思维模型”文档的内容解释器。后者不能继续沿着通用路径扩散。以下逻辑已明确标记为 `legacy/document-profile`：

| 旧 runner 位置/逻辑 | 结论 | 新 runner 处理 |
|---|---|---|
| `classify_content()` 的 `个人成长`、`21个思维模型`、数字页眉和 `CHARLIEMUNGER` 过滤 | 历史文件页眉/标题噪声 | 迁移到显式 legacy profile；通用路径只接受 profile 提供的噪声规则，默认不提供这些术语 |
| `classify_content()` 的 `vs\|对比\|复盘\|模型\|方法\|法则\|层级\|人生观\|价值观` 标题候选正则 | 固定文档标题模式 | 不进入 `workflow_a_source_runner.py`；标题必须来自当前 source 的原生结构或候选证据 |
| `_render_pdf()` 的固定 `pdftoppm -r 144` | 历史渲染质量/运行环境选择 | 由 source profile 的 renderer config 提供；本轮不把 144 写入通用接口 |
| `mac96_preflight()`、`run_mac96*()` 的 `ssh mac96`、Ollama 探测和模型选择 | 具体机器、transport 和模型绑定 | 独立视觉能力适配器；新 runner 只读取已验证 profile，不直接写 SSH 主机、地址或模型名 |
| `title_quality_flags()` 默认标题区域 `(0.74, 0.87)` | 固定 OCR 标题版式假设 | legacy/document-profile；通用路径不设置固定 bbox/比例 |
| `build_markdown()` 中“主题/对比”、页面标题、固定阅读顺序和页面组织 | 旧文档读者层模板 | 新 runner 只传递当前 source 的动态结构给 Markdown builder；模板由 profile/结构结果决定 |
| `extract_embedded_for_pages()` 主要处理 PPTX zip media | 资产覆盖不完整 | 新 runner 明确区分 PPT/PPTX、PDF、独立图片的页面/嵌入/原图/区域资产路线 |
| `--pptx` / `--pdf` 别名 | 旧命令兼容 | 保留，但标记 `legacy-alias`；新入口使用 `--source` |

以上内容是代码审查结论，不是一次真实转换结果。旧 runner 的一般性能力（哈希、确定性文件命名、证据写入、独立 validator 调用）可以被拆出复用，但不得通过复制函数把旧语义带入通用路径。

## 迁移表：旧逻辑 → legacy/profile；通用边界 → source runner

| 能力 | 旧 runner 现状 | 新 runner / 后续实现边界 | 当前状态 |
|---|---|---|---|
| source 路径、媒体类型、单源隔离 | 已有 `--source`，并保留 `--pptx/--pdf` 别名 | `--source` 是唯一 generic 输入；profile 解析 PPT/PPTX、PDF、图片 | 规格已完成 |
| 页码 selector 与总页数 | `parse_pages()` 可复用，但旧执行链会直接进入固定 A1-A7 | source runner 从当前 PPTX slide XML/PDF/image 动态锁定 selected pages | 已实现并由 fixture execute 验证 |
| SHA-256、run/package 指纹 | 旧 runner 有哈希和 package 命名 | source fingerprint 先于 source-owned evidence；字段在新 contract | 已实现并由三类 fixture 验证 |
| 来源预检 | 由 `workflow_a_preflight.py` 作为横向门禁补入旧 dispatcher | source runner execute 使用确定性 preflight；legacy 不参与 generic 路径 | 已实现并复测 |
| 原生结构提取 | PPTX XML/PDF layout 与旧文档分类耦合 | 按 source type 注册 native extractor；不在 runner 中写标题/术语 | PPTX/PDF 方案已登记；实现待测 |
| 页面、嵌入、原图、区域资产 | `extract_embedded_for_pages()` 以 PPTX media 为主，PDF/独立图片不完整 | profile 分别声明 PPT/PPTX、PDF、图片的 asset sources，并先登记 asset/occurrence/hash | 已实现；PPTX/PDF/image fixture 已逐包验证 |
| 内容提取阶梯 | mac96、Apple Vision 和 OCR 逻辑混在旧 runner | 原生结构 → mac96 视觉模型 → 当前对话在线视觉单图转写 → Apple Vision OCR（仅文字层），候选与 raw 分层；外部在线 provider 仍需明确授权 | 规格已完成 |
| 语言质量修复 | 旧 runner 没有独立 raw/draft/diff 边界 | 页面单元后的 `markdown-content-repair` 局域网优先适配器；不得覆盖原始视觉证据 | available; real LAN proof pending |
| Markdown 组装 | `build_markdown()` 带固定页面标题和阅读模板 | 从当前 source 动态结构生成；标题/页码/术语不由 runner 注入 | 已实现；三包 Markdown 链接和 provenance 通过 validator |
| validator / 质量审计 | 旧 runner 可写报告，但自报状态不能代替独立 validator | Markdown 后调用独立 validator；缺任一视觉/OCR/质量门只能 `needs-review` | validator 已复测；质量审计仍未执行 |
| dispatcher / batch | 目前仍调用旧 runner | generic execute 推广后再切换；迁移前保留 legacy 运行路径 | migration-pending |

## 新旧入口状态

| 入口 | 状态 | 允许用途 |
|---|---|---|
| `adapters/workflow_a_source_runner.py` | `active-deterministic-execute` | `--plan-only` 与确定性 `--execute`；不调用 legacy、视觉模型或 OCR |
| `archive/legacy-adapters/workflow_a_runner.py` | `legacy/document-profile` | 历史包兼容、已有证据恢复、后续迁移对照；不得作为新来源默认入口 |
| `adapters/workflow_a_dispatcher.py` | `migration-pending` | 规格上改用新 runner；待 generic execute 阶段完成后再启用真实调用 |
| `adapters/workflow_a_batch.py` | `migration-pending` | 仅在新 runner 的单 source 契约稳定并单独测试后迁移 |

## 本轮证据边界

- 当前无活动的 `workflow_a_runner.py`、dispatcher 或 batch 进程；`runs/real/` 下目录都是历史运行证据，不属于本轮执行。
- 先前的 `57 passed` 只能标记为旧基线，不能作为本轮新增规格/runner 修改已验证的结果。
- 本轮不调用高质量在线模型、本地视觉模型、Apple Vision OCR 或其他 OCR；只处理 `tests/fixtures/` 中的 fixture source。
- 图片资产校准路线只登记为 `planned`：高质量在线基准 → 本地视觉模型复现 → 质量比较 → 达标后降本。
- 测试状态为 `completed-for-fixtures`；证据索引见 `tests/runs/source-runner-test-20260816-0022/output-index.json`。fixture 结果仍不等同于生产 source 结果。

| 文件/逻辑 | 当前状态 | 允许用途 | 禁止用途 | 后续处理 |
|---|---|---|---|---|
| `adapters/workflow_a_reclassify.py` | `legacy-recovery-only` | 从已有完整证据包重建读者层 Markdown | 对没有通过前置门禁的新来源直接调用 | 改造成有明确输入/输出契约的 repair workflow |
| `tests/scripts/materialize_workflow_a_smoke_inputs.py` | `fixture-only` | 生成与当前输入元数据对应的测试夹具 | 伪装成真实来源转换器 | 保留在 `tests/`，不进入生产路由 |
| `tests/fixtures/18个工作思维模型-last-2-slides/` | `fixture-only` | 回归页面、链接、manifest 和失败边界 | 为其他来源填充标题、正文、页码或关系 | 继续保留，但每次运行都标注 `fixture` 证据等级 |
| `archive/legacy-adapters/workflow_a_runner.py` 的 `--pptx/--pdf` | `legacy-alias` | 兼容旧命令 | 新文档说明中作为首选入口 | 新入口只使用 `--source` |
| `references/reader-noise-profile.yaml` | `compatibility-only` | 旧思维模型类页面的页眉/布局噪声候选 | 将 profile 规则当成新来源事实 | 新来源应建立独立 profile 或改为页面证据判定 |
| `references/prompts/workflow-a-*.txt` | `versioned-prompt-template` | 通用结构化视觉复核提示 | 在脚本里追加某一固定文档的标题、正文或关系 | 每次模型/模式变化都做校准并记录版本 |

## 执行门禁

- 遗留恢复脚本默认只接受来源包已有 `smoke-report.json` 和独立 `validation-result.json`；前置证据不完整时应停止。
- 任何包含固定文档标题、页码、正文或关系的脚本都只能放在 `tests/` 或 `archive/`，不得被 Workflow 注册表引用。
- 兼容规则只能生成候选或过滤噪声，不能把候选升级为 `confirmed`；原始 OCR 和页面图必须保留。
