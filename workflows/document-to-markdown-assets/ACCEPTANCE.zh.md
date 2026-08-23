# Workflow A 验收

## 默认 reader-only 必须通过

默认 `output_root/<package-name>/` 必须包含 `index.md`；只有存在 Markdown 无法替代的语义视觉时才包含被引用的 `assets/` 图片。纯文本结果可不创建 `assets/`，不得把 JSON、manifest、evidence、reports 或 page-units 当作默认交付。

- [ ] `index.md` 非空，包含可读的关键点 Markdown。
- [ ] `index.md` 的每个图片链接都存在且指向当前包内的截图。
- [ ] 若存在 `assets/`，其中没有未被 `index.md` 引用的文件，且不包含嵌套目录或非图片文件；若 `index.md` 没有图片链接，允许 `assets/` 不存在。
- [ ] `page_content_type=text-only`、Markdown 可完整表达且没有语义视觉/未决关系的页面不含整页截图链接；二维码不解码、不裁剪、不作为读者图片。
- [ ] 默认输出中不存在 `manifest.yaml`、`reports/`、`evidence/`、`page-units/`、`document-temp.md`、JSON 或 YAML。

需要完整资产登记、阶段证据和独立验收时，显式加 `--engineering-output`；以下工程验收清单只适用于该显式模式。

知识库导入前的 `prune-low-value-ebook-pages` 是 A1–A7 之后的可选读者层操作，不是 Workflow A
通过条件。若对 engineering package 原位删除 page-units，该目录不再满足全页工程输出边界；只有
保留完整工程包、并在独立知识库工作副本中清洗，才能继续使用原包做完整验收证据。

## 显式 engineering-output 验收

`stage_evidence` 的字段约束见 [`../../contracts/stage-evidence.yaml`](../../contracts/stage-evidence.yaml)，失败和降级记录见 [`../../contracts/failure-report.yaml`](../../contracts/failure-report.yaml)。

- [ ] 每个 A1–A7 阶段都有 `stage_evidence`：阶段状态、证据等级、证据路径、诊断信息和验证命令；不能只用总体验收勾选代替。
- [ ] A1–A4 中间证据包含上游 `source_fingerprint`，A5 每个 section 有 `source_occurrences`/资产 ID，A6/A7 对其做一致性校验。
- [ ] `status: available` 的每个资产都有可读取 MIME、宽高和 SHA-256，且哈希与文件内容一致。

- [ ] 输入文件类型和来源指纹已记录，原始文件未被修改。
- [ ] 同名同页数的 PPTX/PDF 候选先经过代表页比较；同名文字存在时 PPTX XML 作为语义/数字资产来源；若截图差异超阈值或 PPTX 渲染异常，则 PDF 页面图作为主视觉证据，PPTX Office 渲染只作为独立候选证据。
- [ ] Markdown 能按页面/章节检索，并保留来源定位。
- [ ] `index.md` 只包含读者语义和必要页面图链接；不得出现 `标题下正文文字块`、`视觉关系`、`来源与不确定性`、`A4 状态`、原生 bbox、对象 ID、模型调用或 OCR 诊断字段。
- [ ] 有可确认语义标题时，标题前不得拼接源文件名、页码、哈希或对象 ID；来源文件名、页码和 occurrence 必须仍可在 `manifest/evidence` 中追溯。无语义标题的页码 fallback 必须带 `needs-review`。
- [ ] 每个页面/章节遵循固定阅读顺序：标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 场景/限制/来源与不确定性 → 【需要保留的】原始页面图、按需复杂区域图、嵌入图片 → 待核对/未知形式清单；图片不得置于正文文字之前。
- [ ] A4/A5/A6 已执行视觉资产语义筛选：明确为 text-only 且 Markdown 足够的页面在首次投影时直接不写图片链接；对旧包的后置移除仍必须有 `evidence/visual-asset-pruning.json`。复杂图、流程、表格关系、颜色/空间编码不得因文字完整而移除；工程 `assets/`、manifest、source occurrences 和来源哈希仍保留。
- [ ] `type: divider` 的无语义章节分隔页若经用户明确要求物理删除来源图片，读者层、page-unit 和聚合预览均无图片链接；原始 manifest 保持不变，`evidence/visual-asset-pruning.json` 保留原 SHA-256/页码/资产 ID/删除授权，validator 只对该精确覆盖记录豁免缺图，普通页面不得借此放宽。
- [ ] 每个引用的视觉资产存在、可打开，且 manifest 中有来源页码/区域。
- [ ] PPTX/XML 或 PDF 布局提取结果与 OCR 候选分层保存；DOCX 只有在单独接入 active runner、契约和真实验收后才能加入输入范围。
- [ ] A4 把 `text-recognition`、`markdown-structure-reconstruction`、`advertising-classification-and-treatment` 作为三个独立阶段记录；任何阶段不得覆盖原始 OCR/模型响应。
- [ ] Apple Vision OCR 加确定性结构重建、mac96 小视觉模型与 mac96 30B 都只作为候选路线；主路线和升级顺序必须引用同一真实页面集的质量、耗时及资源证据，不得预设固定优先级。
- [ ] 每个视觉模型请求只包含当前一张页面图或区域图；提示词不得调用 OCR、读取其他文件或参考前后图片/页面。
- [ ] 视觉提示词约束标题层级、自然段、目录、列表、页眉页脚和跨页边界；无法确认的结构保留 `Unknown`/`needs-review`，不得补写页面不存在的标题、编号、页码对应或跨页正文。
- [ ] 提取及结构重建阶段逐字保留微信号、联系方式、二维码附近说明、推广语和其他疑似广告文字；独立广告策略默认 `preserve-only`，本轮不得删除、隐藏或改写。
- [ ] PDF 候选页页面图由同一执行器的渲染生产者写入扁平 `assets/`；只有路线需要视觉或本地渲染结构佐证的页面才渲染，且该页图片可读后才启动视觉处理；纯文字 `native-only` 页不进入渲染/视觉队列。
- [ ] PDF 每个选定页都有 `evidence/page-routes/pNNN.json`，路线只能是 `native-only`、`native-plus-visual` 或 `visual-only`；“如图”等文字只作候选，必须记录图片对象/面积、bbox 空白、`pdfplumber` 表格或本地渲染结构佐证。
- [ ] `native-only` 页不调用视觉模型，也不要求页面图或视觉 JSON；“如图”但没有图片对象的页保留原生正文并标为 `needs-review`，不得补造图示内容。
- [ ] `native-plus-visual` 页在同一个 `page-units/pNNN.md` 中先保留原生正文，再追加视觉说明/表格结构/图示关系；视觉结果不得覆盖、改写或替换原生正文。
- [ ] `visual-only` 页必须有原生文字为空/不足以及图片、表格或渲染结构佐证；视觉证据记录图片资产 ID、来源指纹、图片 SHA-256、页码、请求策略、实际调用次数、模型和状态，不得记录密钥、认证信息或订阅地址。
- [ ] PDF 渲染只覆盖 `native-plus-visual`、`visual-only` 和需要本地结构佐证的候选页；`evidence/rendering.json` 中视觉路线的 requested/rendered 差集为空，纯文字页不因验收规则被迫渲染或调用模型。
- [ ] 单页原生/视觉识别失败会写入路线记录和待处理列表并继续后续页面；`reports/page-completion.json` 必须报告 `missing_pages`、`duplicate_pages`、`unexpected_pages`，任一非空时最终状态不得为 `passed`。
- [ ] 已完成页的复用同时校验来源 SHA-256、页码、已通过路线记录、路线所需证据和唯一页面标记；不得仅凭 `page-units/pNNN.md` 存在跳过。
- [ ] `page-units` 生成后、语言模型修复前必须执行 `clean-page-markdown-noise`：先 scan、再用同一 profile apply，并写入 `evidence/page-markdown-noise-cleanup.json`。
- [ ] 清洗只允许删除完整页眉模板行、原始页面图链接前的页脚页码及清洗固定点上的首尾 `---`；正文、标题、表格、列表、内部横线、`Unknown/needs-review` 和页面图路径行逐字不变。
- [ ] 不同来源的页眉通过运行级 exact/regex/fuzzy profile 或跨页重复边界发现处理；profile 命中但不在安全区的独立行保留为 `suspects`，不得自动删除。
- [ ] apply 前全部输入哈希未漂移，只重写实际命中文件；写后所有选定文件非空，可由输入仅删除清单中的行精确重建，且没有高置信残留。
- [ ] 每个 `page-units/pNNN.md` 生成后必须先进入页面结构守卫，并写入 `evidence/markdown-content-repair-pNNN.json`；若守卫已证明标题、段落、列表、原文保真和 reader image 策略通过，允许 `status=skipped` 且模型调用次数为 0。
- [ ] 只有结构守卫未通过、局域网修复被判定为必要时，才允许调用 `markdown-content-repair` 的局域网语言模型；输入只包含当前页 Markdown，不包含图片、OCR JSON、bbox、manifest、其他页面或 evidence。局域网修复成功后在线模型调用次数必须为 0；局域网不可用、超时、不可解析或修复后仍未通过结构守卫时，只有明确授权才允许当前在线模型回退一次，并记录失败原因与授权状态。
- [ ] 修复写回前必须检查并保留页面编号、reader image 处置、已有原始页面图链接和 `Unknown/needs-review` 关系边界；纯文本页不得新增图片链接，失败或未决结果不得覆盖原始 page-unit。
- [ ] 每页结构守卫/修复都必须在 `reviews/markdown-content-repair.yaml` 中记录输入/输出哈希、路由、逐项差异、是否 `skipped` 和写回状态。
- [ ] 页面 Markdown 修复不单独运行 pytest 或页面独立 validator；修复适配器内的结构守卫和规定阶段的人工查看是该步骤的验证边界。完整资产包仍由 A7 独立验收。
- [ ] 对单个视觉模型，关系密集、图表复杂、颜色/空间编码重要或明确要求高置信度时，才显式使用 `pass_strategy=dual`；Apple Vision OCR 只能提供原始文字和坐标候选，必须经过独立结构重建，且不得用 OCR 伪造视觉结构。
- [ ] 复杂图、图文混排或关系密集区域有整页图或必要的局部图证据；整页图已足够时不生成 `complex-region`/`crop` 文件，并在报告中记录该判断。
- [ ] 新包使用 `output_layout: flat-assets-v1`，所有资产直接位于 `assets/`；不得创建 `assets/pages/`、`assets/regions/` 或 `assets/embedded/`。
- [ ] 对复杂结构或明确需要重新生成的视觉资产，存在 `evidence/visual-reconstruction-profile.yaml`，且结构版、提示词版、来源字段和未决项可独立重读；纯装饰或证据不足时明确记录 `not-applicable`/`insufficient-evidence`。
- [ ] A4 报告记录实际视觉复核范围。mac96 返回明确的未归类组合图、嵌套容器、颜色/线型编码、跨区/跨页关系、图例/注释时，必须登记为 `unknown_forms` 并保持 `needs-review`；模型没有返回可选字段或未执行开放集合扫描，不得单独制造 `needs-review`。
- [ ] 存在真实失败、冲突或未裁决关系时，页面/资产包最多为 `needs-review`，不能标记 `accepted`。
- [ ] 路线质量报告至少逐页检查标题层级、正文分段、目录、列表、页眉页脚、标点、跨页连续性和异常内容保留；非空输出、文件存在或 Markdown 可打开都不能单独通过。
- [ ] 原始可见文字有静默遗漏、结构有臆造、适用维度缺少复核证据，或广告候选未能追溯到原始文字时，页面最多为 `needs-review`。
- [ ] 读者层修复严格按第 1 页单页、第 2–5 页小批量、第 6-total_pages 页全量推进；前一阶段页面结构守卫未通过时不得启动下一阶段。
- [ ] 修复一页时输入只包含当前页面单元和当前页面图；不得加载其他页面正文，也不得修改 OCR JSON、bbox、置信度、逐页 Vision JSON、页面图或原始 `manifest.yaml`。
- [ ] 页面单元具备页面编号、语义标题、正文、列表/图示文字、表格与公式、解读、举例与思考、模型结构、已确认的关系说明和待核对项；原始页面图只在 reader image 处置不为 omit 时出现。
- [ ] `实用公式` 与公式各自单独成行；举例不与公式或解读粘成同一个列表项；图示标签不伪装成普通正文。
- [ ] 页面单元按固定页面编号升序合并；reader image 处置与已有原始页面图链接保持不变；合并不得重新推断、摘要、翻译或补写来源不存在的文字。
- [ ] `document-temp.md` 只能是页面单元的派生预览，不能替代最终 `index.md` 或成为新的事实来源。
- [ ] 未确认图示关系保持 `Unknown`/`needs-review`，不得生成未经视觉确认的箭头、层级、流程、矩阵或颜色关系说明。
- [ ] 第 1 页页面结构守卫通过后，才允许启用第 2–5 页入口；第 2–5 页结构守卫与人工复核通过后，才允许启用第 6–total_pages 页入口。
- [ ] `file_name` 为稳定英文 ASCII 名称，`display_name` 保留中文语义；原始文件名只作为 `original_filename` 证据，不参与命名。
- [ ] 同名资产通过 `source_occurrence_id`、来源指纹和 SHA-256 区分；来源出现记录与文件名解耦。
- [ ] 冲突、缺失和不可用复核均显式标记为 `Unknown`、`needs-review` 或失败态。
- [ ] 不调用 GPT-image-2，不生成 PPTX/PDF。

## 推荐规格夹具（非页面修复步骤）

以下夹具只用于维护 Workflow 规格和完整资产包契约，不是单页 Markdown 修复的必经环节。

- [ ] 使用 `tests/fixtures/18个工作思维模型-last-2-slides/`，而不是封面/目录页。
- [ ] PPTX 夹具恰好包含原文档第 24、25 页；PDF 夹具恰好包含对应两页。
- [ ] 第 24 页验证 `提升领导力的3357`、三零/三管/五带/七抓及关系边。
- [ ] 第 25 页验证 `搞定自己 vs 搞定别人`、世界观/价值观和多层对比结构。
- [ ] 夹具测试同时检查原生文本、PDF 对照文本和页面视觉基准图。

## 失败即不通过

- Markdown 生成成功但视觉链接断裂；
- OCR 覆盖了可信的原生文字且没有保留差异；
- 复杂结构仅剩无坐标的扁平文字；
- 资产无法追溯到来源页/节点/区域；
- 处理报告把未执行的 mac96 复核或解析器写成已完成。
- 每个修复任务均记录问题、来源证据、转换差异、风险、修复动作、验收条件和当前状态；`resolved` 必须有修改后复核证据。
- 单页/局部修复可从上一版有效结果恢复，不要求重做整本；修改后检查相邻页未被覆盖。
- [ ] 每页/受影响区域有当前分数、目标分数、各维度独立分数和扣分证据；总分至少 95，建议达到 97 安全余量。
- [ ] `content_completeness >= 20/23`、`graphic_fidelity >= 20/23`；`unknown_form_coverage = 5/5` 仅在实际登记未知形式候选时作为该候选的覆盖评分，不得把未执行开放集合扫描当作自动失败。
- [ ] 验收报告记录 P0/P1/P2 修复任务；存在 P0、未复核 `resolved` 任务或缺少恢复证据时不得 `passed`。
- 任一 `contract-invalid`、`provenance-missing` 或 `integrity-check-failed` 都使最终验收至少为 `needs-review`，不得写 `passed`。
