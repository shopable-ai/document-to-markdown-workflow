# Workflow A 验收

## 默认 reader-only 必须通过

除下述 DOCX 原生全文例外，默认 `output_root/<package-name>/` 必须包含 `index.md`；只有存在 Markdown 无法替代的语义视觉时才包含被引用的 `assets/` 图片。纯文本结果可不创建 `assets/`，不得把 JSON、manifest、evidence、reports 或 page-units 当作默认交付。

- [ ] `index.md` 非空，包含可读的关键点 Markdown。
- [ ] `index.md` 的每个图片链接都存在且指向当前包内的截图。
- [ ] 默认最终正式稿只有 `index.md`；不得额外生成同内容的 source-named Markdown、副本或“最终版/修正版”别名。
- [ ] 若存在 `assets/`，其中没有未被 `index.md` 引用的文件，且不包含嵌套目录或非图片文件；若 `index.md` 没有图片链接，允许 `assets/` 不存在。
- [ ] `page_content_type=text-only`、Markdown 可完整表达且没有语义视觉/未决关系的页面不含整页截图链接；二维码不解码、不裁剪、不作为读者图片。
- [ ] 默认输出中不存在 `manifest.yaml`、`reports/`、`evidence/`、`page-units/`、`document-temp.md`、JSON 或 YAML。

需要完整资产登记、阶段证据和独立验收时，显式加 `--engineering-output`；以下工程验收清单只适用于该显式模式。

知识库导入前的 `prune-low-value-ebook-pages` 是 A1–A7 之后的可选读者层操作，不是 Workflow A
通过条件。若对 engineering package 原位删除 page-units，该目录不再满足全页工程输出边界；只有
保留完整工程包、并在独立知识库工作副本中清洗，才能继续使用原包做完整验收证据。

## DOCX 原生全文验收

- [ ] 仅接受 `--pages all`；不渲染 Word 分页，不接受 `--engineering-output`。
- [ ] `reports/validation-result.json` 由独立验收器重读来源和成品生成；核对原生正文、表格数量及正文图片出现顺序、字节和本地链接。
- [ ] 图片型正文保留已核对的原图，不冒充 OCR；未核验对象或内容差异明确标为 `needs-review`。后台状态展示实际包路径及验收结论。

## 多 PDF 队列验收

- [ ] 两份及以上 PDF（包括混合输入中的 PDF 子集）通过 `pdf-collection` 正式队列逐本处理，一来源一工程包。其他文件仍走各自单来源入口，不自动配对；旧通用批处理和单本分页任务不是替代入口。
- [ ] 队列使用 `--pages all` 和显式路线：`hybrid` 沿用逐页决策，`native-only` 有预检批准；不支持整本视觉集合，不改变每个来源的页面和验收门。
- [ ] 采用固定路线根或通过 `--run-root` 采纳准确的官方批次根，不能手工指定 `--run-id`。同根活动或其他路线持有同集合锁时不启动第二个队列，分别返回 `already-running` 或 `collection-busy`；非托管旧队列没有伪造的 ID 或取消命令。
- [ ] 未活动已登记的任务返回 `already-registered`、`existing_status` 和 `resume_command`。续跑（含 `completed` 的原地重验）保留原路线、来源清单和逐书包，核验原登记证据，只跳过仍严格验收通过的来源；旧结果、空 `index.md` 或队列状态不能证明某本通过。
- [ ] 低空间 `paused` 以及 `status` 的 `batch_summary`（文件级计数）、`active_source`、`strict_accepted_count` 和 `halt_reason` 不替代来源级总账。单路线批次完成不等于整个集合通过；跨路线续接只处理总账中尚未 strict accepted 的来源，`_consolidated/source-consolidation.json` 显示所有预期来源均 strict accepted 后才输出最终读者层目录。

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
- [ ] PPTX/XML 或 PDF 布局提取结果与 OCR 候选分层保存；DOCX 适用上面的独立原生全文验收，不适用本分页工程清单。
- [ ] A4 把 `text-recognition`、`markdown-structure-reconstruction`、`advertising-classification-and-treatment` 作为三个独立阶段记录；任何阶段不得覆盖原始 OCR/模型响应。
- [ ] Apple Vision OCR 加确定性结构重建、mac96 小视觉模型与 mac96 30B 都只作为候选路线；主路线和升级顺序必须引用同一真实页面集的质量、耗时及资源证据，不得预设固定优先级。
- [ ] 每个视觉模型请求只包含当前一张页面图或区域图；提示词不得调用 OCR、读取其他文件或参考前后图片/页面。
- [ ] 视觉提示词约束标题层级、自然段、目录、列表、页眉页脚和跨页边界；无法确认的结构保留 `Unknown`/`needs-review`，不得补写页面不存在的标题、编号、页码对应或跨页正文。
- [ ] 提取及结构重建阶段逐字保留微信号、联系方式、二维码附近说明、推广语和其他疑似广告文字；独立广告策略默认 `preserve-only`，本轮不得删除、隐藏或改写。
- [ ] PDF 候选页页面图由同一执行器的渲染生产者写入扁平 `assets/`；只有路线需要视觉或本地渲染结构佐证的页面才渲染，且该页图片可读后才启动视觉处理；纯文字 `native-only` 页不进入渲染/视觉队列。
- [ ] PDF 每个选定页都有 `evidence/page-routes/pNNN.json`，路线只能是 `native-only`、`native-plus-visual` 或 `visual-only`；“如图”等文字只作候选，必须记录图片对象/面积、bbox 空白、`pdfplumber` 表格或本地渲染结构佐证。
- [ ] 新 LAN page-hybrid 全页工程包通过工程验收后，读者投影按锁定的 3.0.0 规则复核每个终稿页（含无图引用页）：仅对 Apple Vision 扫描候选采集本地 Tesseract 证据；验收离线重算正文字符重数与顺序、像素与 PDF 对象否决门及 SHA-256。不允许忽略标点或正负号冒充覆盖，也不认隐藏层或同源 OCR 自证。缺证、失败、冲突、可读浅色叠印或未证实图形均保留。历史 2.0.0 按旧规则只读复验。
- [ ] 读者验收确认正文、页序及保留链接/资产逐字节不变。待裁和待复核整页图继续保留；无图且未证实的页令 reader 返回 needs-review，不自动重写工程包或补渲染。已提交的旧报告仍按旧规则复核，不重写历史工程包。
- [ ] 原生或 page-hybrid 整本 PDF 通过后，同目录首次生成与 `index.md` 字节一致的书名工作副本；续跑不覆盖手工修改，验收仍只读取正式稿和工程证据。
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
- [ ] 混合正文与局部复杂视觉不得仅以 `retain` 把整页工程截图留在读者 `assets/`；必须标记 `crop-required` 并生成最小充分区域图。只有全页关系不可分割时才保留整页。
- [ ] 裁图候选默认先使用 Apple Vision OCR 坐标、图/表题锚点、空白带/墨迹密度/连通区域等确定性方法；局域网视觉模型只为低置信例外提供候选坐标，不直接授权删除，也不逐页重复读取已进入 Markdown 的正文。
- [ ] reader-only 简化包的每个裁图质量分至少 95/100，且视觉内容、必要标签、图题/上下文、四边安全、正文污染五项硬检查全部通过；横置图的旋转参数、源图删除前哈希和输出哈希可重读。
- [ ] 每个已引用 reader crop 均有 `evidence/text-heavy-reader-asset-audit.json` 二次门记录；默认 `auto` 路线使用 Apple Vision 坐标 OCR，失败或低置信时本机回退 Tesseract，全程默认模型 token 为 0。
- [ ] 二次门同时检查 Markdown 字符覆盖和顺序覆盖、非文字墨迹/包围面积/横纵结构线、建议裁切框和来源哈希；稀疏箭头/坐标轴不得因墨迹比例低而忽略，只有简单线性顺序已在 Markdown 中明确表达时才算视觉增量已覆盖。相同字符乱序、OCR 低置信或结构边界不清必须 `needs-review`，不得自动删除。
- [ ] 只有 `candidate-remove`、`decision_confidence_score >= 95`、`deletion_eligible=true` 且全部 deletion gates 通过的 reader crop 才可进入 hash 锁定删除计划；`crop-required` 继续裁、`retain` 保留、`needs-review` 失败关闭。audit evidence 95 分与 crop visual quality 95 分分别验收，不得互相替代。
- [ ] 物理清理只通过独立 `--apply-plan` 调用执行；应用前重读 Markdown/图片 SHA-256、原引用行、删除分和全部硬门，先移除 Markdown 引用、后删除图片，失败时恢复两者并写出 removal result。
- [ ] 只有 `regenerable-engineering-page-render` 可在全部裁图生成、Markdown 精确换链、全部新资产被引用并通过回滚门后删除；原始 PDF/PPTX/独立图片不得删除。最终 reader `assets/` 中无未引用图和残留 `page-NNN` 整页工程图。
- [ ] 启用图级视觉资产升级时，每批只覆盖同一个 page-unit、segment 或其他已登记最小来源单元中的 1–2 张图；前一批 F0 需求盘点 → F1 来源页 → F2 区域定位 → F3 crop 接受 → F4 可选增强 → F5 可选 Mermaid → F6 同一 Manifest/读者投影 → F7 回填/再生成 → F8 验收未闭合时不得扩大。
- [ ] F0 已记录当前 content unit、图号/图注、现有 Markdown 链接、Manifest 资产、legacy/缺失分类和批次锁；尚无专用 adapter 时如实标为 procedure-only，不伪装成代码执行。
- [ ] F0 的 `expected_figure_ids` 来自原始/来源最小单元、cleaned/page-unit、派生 Markdown、现有资产和同一 Manifest 的对账并集；cleaned/final 缺失但来源存在的图号仍标为 `markdown-reference-missing`，不得静默移出需求集合或删除图片。
- [ ] `figure_coverage` 中每个 expected figure ID 恰好有一条 content unit、来源页、资产、Markdown、生命周期和批次状态记录；`batch_status` 与 `document_status` 分开，单批 passed 不得推导全书 passed。
- [ ] 现有图片与同批页图尺寸一致或仍含页眉、页码、章节标题、正文段落时，按整页图候选复核；判断不清时保留来源页图并进入 `needs-review`。
- [ ] F1 复用 legacy 页图副本时记录两端路径/SHA-256 和 `source_equivalence`；仅在双方哈希一致时写 `byte-identical`，发生渲染或转换时保存工具、参数和来源链。
- [ ] F1 页定位不确定时仅探查候选页，保存候选页列表、明确页序、页面图和 contact sheet 哈希、发现与选择结论；contact sheet 只作 evidence，不进入读者 `assets/`。
- [ ] contact sheet/thumbnail 只用于选页；预览图候选框保存 preview/full-resolution 尺寸与 scale/rotation transform，并在带哈希的全分辨率 parent 坐标空间重新定位和逐边复核，不直接复用预览坐标裁切。
- [ ] F2 候选区域记录 `region_id`、定位锚点、触发依据、candidate geometry、必须保留/允许排除元素、覆盖对象、置信度和诊断；候选区域不得直接标成最终 crop。
- [ ] 同一 page-render 包含多个图时，各图具有不同 `figure_id`、`region_id`、`source_occurrence_id`、ordinal 和 crop decision；不得按页码、哈希或视觉相似度合并为一个图。
- [ ] F3 crop 的 `parent_asset_id`、来源页、candidate/accepted `WxH+X+Y`、来源像素坐标空间、横纵向策略、安全边距、调整历史、尺寸和 SHA-256 可重读；top/right/bottom/left 任一为 clipped/unknown，或前景触边无解释/证据时不得 `available`。
- [ ] F3 同时保留来源页、crop preview 和 overlay 或等价复核证据；坐标合法、文件存在和图片可打开均不能单独代替区域正确性验收。
- [ ] F4 enhanced-raster 记录实际工具/版本及 colorspace、auto-level、brightness-contrast、sharpen 参数，并保存 crop/enhanced 比较证据、选择理由和 `structure_preserved=true`；增强变差或结构可能改变时使用 crop fallback，不能标为完整。
- [ ] 新包使用 `output_layout: flat-assets-v1`，所有资产直接位于 `assets/`；不得创建 `assets/pages/`、`assets/regions/` 或 `assets/embedded/`。
- [ ] 对复杂结构或明确需要重新生成的视觉资产，存在 `evidence/visual-reconstruction-profile.yaml`，且结构版、提示词版、来源字段和未决项可独立重读；纯装饰或证据不足时明确记录 `not-applicable`/`insufficient-evidence`。
- [ ] Mermaid 只用于结构清晰且节点、标签、边、方向都有来源证据的流程/推理链/层级/时间线/节点—边关系；影响结构的不确定项存在时状态必须为 `needs-review`，不得回填 Mermaid。
- [ ] F5 可用 Mermaid 在 profile/Manifest 中记录路径、状态、置信度、source asset IDs、Markdown target 和 PNG `fallback_asset_id`；语法解析、实际渲染、节点/标签/边/方向/层级语义对照和 fallback 链接检查必须全部 passed，Mermaid 不得替代来源页图。
- [ ] 邻近正文仅以精确 source span 佐证图中可见标签的拼写/消歧，不得新增节点、边、方向、层级或文字；正文、OCR/视觉候选与图片冲突时记录 `text_corroboration.conflicts`，相关关系和 Mermaid 为 `needs-review`。
- [ ] F5 的放大图、节点子图、OCR probe、overlay 和比较预览记录稳定诊断 ID、父资产、目的、路径、SHA-256、保留状态及 `reader_projection=evidence-only`；OCR/视觉候选不得用来猜测图中事实。
- [ ] A4 报告记录实际视觉复核范围。mac96 返回明确的未归类组合图、嵌套容器、颜色/线型编码、跨区/跨页关系、图例/注释时，必须登记为 `unknown_forms` 并保持 `needs-review`；模型没有返回可选字段或未执行开放集合扫描，不得单独制造 `needs-review`。
- [ ] 存在真实失败、冲突或未裁决关系时，页面/资产包最多为 `needs-review`，不能标记 `accepted`。
- [ ] 路线质量报告至少逐页检查标题层级、正文分段、目录、列表、页眉页脚、标点、跨页连续性和异常内容保留；非空输出、文件存在或 Markdown 可打开都不能单独通过。
- [ ] 原始可见文字有静默遗漏、结构有臆造、适用维度缺少复核证据，或广告候选未能追溯到原始文字时，页面最多为 `needs-review`。
- [ ] 读者层修复严格按第 1 页单页、第 2–5 页小批量、第 6-total_pages 页全量推进；前一阶段页面结构守卫未通过时不得启动下一阶段。
- [ ] 修复一页时输入只包含当前页面单元和当前页面图；不得加载其他页面正文，也不得修改 OCR JSON、bbox、置信度、逐页 Vision JSON、页面图或原始 `manifest.yaml`。
- [ ] 页面单元具备页面编号、语义标题、正文、列表/图示文字、表格与公式、解读、举例与思考、模型结构、已确认的关系说明和待核对项；原始页面图只在 reader image 处置不为 omit 时出现。
- [ ] `实用公式` 与公式各自单独成行；举例不与公式或解读粘成同一个列表项；图示标签不伪装成普通正文。
- [ ] 页面单元按固定页面编号升序合并；reader image 处置与已有原始页面图链接保持不变；合并不得重新推断、摘要、翻译或补写来源不存在的文字。
- [ ] `document-temp.md` 合并后、`index.md` 落盘前，允许执行一次保守的 Markdown 整理/修复；该步骤必须保留页面标记顺序和全部图片引用路径。
- [ ] 启用模型整理时，实际 route、transport、model/profile、分块、耗时、失败与回退已写入 `reports/markdown-post-merge-formatting.json`，且报告不含 API key、Authorization、cookie 或其他认证内容。
- [ ] `MARKDOWN_FORMATTER_ROUTE` 显式指定 route 时没有自动切换 provider；`auto` 才按 TOML 顺序回退。`model-required` 全部失败时没有替换已有目标文件。
- [ ] `auto` 的模型顺序固定为 LAN 直连 → `ssh mac96` 内部 loopback Ollama → Codex default；直连失败而 SSH 成功时没有继续调用 Codex。
- [ ] SSH Ollama 请求体通过 stdin 传输，命令参数中没有提示词；远端端点只能是 loopback HTTP `/api/generate`。
- [ ] Codex provider 通过用户级 profile 和 `codex exec --profile` 选择；不得把不存在的通用 `--provider` 参数或用户认证写入仓库配置。
- [ ] `codex-default` 只表示新建 Codex CLI 运行并继承机器配置；`current-conversation` 不得被子脚本伪装成可递归调用的成功路线。
- [ ] F6 在同一 Manifest 中登记所有父子关系并明确 primary-raster、PNG fallback 和 evidence-only 来源页；只把读者实际使用资产投影到现有扁平 `assets/`。
- [ ] F6 迁移历史逐图 JSON/YAML 时保存源路径和 SHA-256，资产记录合入唯一 `manifest.yaml`；原记录只作 evidence-only。历史 `success` 未通过当前来源、crop、增强/Mermaid、Markdown binding 和 validator 门时归一化为 `needs-review`。
- [ ] F7 图级回填只修改当前 page-unit/segment/content unit；可靠 Mermaid 固定使用“fenced Mermaid → PNG fallback → 图题”，不可靠时只使用 crop/enhanced PNG。回填证据记录 before/after SHA-256、插入的 asset/representation ID 和逐链接解析结果；合并必须逐字保留 Mermaid、fallback 和图题。
- [ ] page-unit/segment 的图片链接继续使用可解析的 `../assets/...`，根 `index.md` 使用 `assets/...`；不得复制来源项目的 `pages/crops/enhanced/derived/manifests` 为第二套读者目录。
- [ ] Markdown 中缺少预期图片引用时，来源 page-render、crop、enhanced-raster、Manifest 记录和哈希仍保留，并产生 `needs-review` 诊断定位投影、写出或合并环节。
- [ ] `document-temp.md` 只能是页面单元的派生预览，不能替代最终 `index.md` 或成为新的事实来源。
- [ ] F8 独立 validator 已重读资产类型、父子关系、crop decision、哈希、Markdown binding 和链接；裁切、增强和 Mermaid 的视觉保真仍有单独人工复核证据。任何 procedure-only/not-run/未接线步骤不得被总状态吞掉。
- [ ] F8 重新计算覆盖计数和 document status：所有 expected figure ID 均已分类、无 missing/reference-missing/needs-review，且每项为 passed 或有证据的 not-applicable 时才允许 document passed；否则即使当前批次 passed 也必须保留文档级未闭合状态。
- [ ] 未确认图示关系保持 `Unknown`/`needs-review`，不得生成未经视觉确认的箭头、层级、流程、矩阵或颜色关系说明。
- [ ] 第 1 页页面结构守卫通过后，才允许启用第 2–5 页入口；第 2–5 页结构守卫与人工复核通过后，才允许启用第 6–total_pages 页入口。
- [ ] `file_name` 为稳定英文 ASCII 名称，`display_name` 保留中文语义；原始文件名只作为 `original_filename` 证据，不参与命名。
- [ ] 同名资产通过 `source_occurrence_id`、来源指纹和 SHA-256 区分；来源出现记录与文件名解耦。
- [ ] 冲突、缺失和不可用复核均显式标记为 `Unknown`、`needs-review` 或失败态。
- [ ] 不调用 GPT-image-2，不生成 PPTX/PDF。
- [ ] native-first、原生文本优先和显式 `--no-model` 边界不变；Marker、Surya OCR、固定 8/40 页分块和 GPT 清理没有成为新默认路线。

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
