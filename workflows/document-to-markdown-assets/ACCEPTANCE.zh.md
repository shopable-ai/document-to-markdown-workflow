# Workflow A 验收

## 必须通过

`stage_evidence` 的字段约束见 [`../../contracts/stage-evidence.yaml`](../../contracts/stage-evidence.yaml)，失败和降级记录见 [`../../contracts/failure-report.yaml`](../../contracts/failure-report.yaml)。即使没有失败，也必须输出 `reports/failure-report.yaml`，并写 `status: none` 与空 `failures`；缺少该文件不得视为“无失败”。

真实 `output_root/<package-name>/` 必须再运行 [`tests/validate_real_output_root.py`](../../tests/validate_real_output_root.py)。它独立重读产物，不信任 runner 自报的 `complete/passed`；返回 `status: passed` 且退出码为 0 后，A7 才能作为真实包验收通过。`tests/validate_workflow_package.py` 和 fixture runner 只证明规格/夹具边界。

runner 完成生成后会自动调用独立业务包验收器，并写入可选的
`reports/validation-result.json`。该文件是本次执行的最终可观察状态：
`passed` 才能继续使用，`needs-review` 必须人工复核，`failed` 不能作为业务结果。

- [ ] 每个 A1–A7 阶段都有 `stage_evidence`：阶段状态、证据等级、证据路径、诊断信息和验证命令；不能只用总体验收勾选代替。
- [ ] A1–A4 中间证据包含上游 `source_fingerprint`，A5 每个 section 有 `source_occurrences`/资产 ID，A6/A7 对其做一致性校验。
- [ ] `status: available` 的每个资产都有可读取 MIME、宽高和 SHA-256，且哈希与文件内容一致。

- [ ] 输入文件类型和来源指纹已记录，原始文件未被修改。
- [ ] 同名同页数的 PPTX/PDF 候选先经过代表页比较；同名文字存在时 PPTX XML 作为语义/数字资产来源；若截图差异超阈值或 PPTX 渲染异常，则 PDF 页面图作为主视觉证据，PPTX Office 渲染只作为独立候选证据。
- [ ] Markdown 能按页面/章节检索，并保留来源定位。
- [ ] `index.md` 只包含读者语义和必要页面图链接；不得出现 `标题下正文文字块`、`视觉关系`、`来源与不确定性`、`A4 状态`、原生 bbox、对象 ID、模型调用或 OCR 诊断字段。
- [ ] 有可确认语义标题时，标题前不得拼接源文件名、页码、哈希或对象 ID；来源文件名、页码和 occurrence 必须仍可在 `manifest/evidence` 中追溯。无语义标题的页码 fallback 必须带 `needs-review`。
- [ ] 每个页面/章节遵循固定阅读顺序：标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 场景/限制/来源与不确定性 → 【需要保留的】原始页面图、按需复杂区域图、嵌入图片 → 待核对/未知形式清单；图片不得置于正文文字之前。
- [ ] A5/A6 已执行视觉资产语义筛选：封面、目录和已被可靠文字覆盖的纯文字页图可从读者层移除；复杂图、流程、表格关系、颜色/空间编码不得因 OCR 完整而移除。移除动作必须有 `evidence/visual-asset-pruning.json`，含页码、SHA-256、理由和同步结果；`assets/`、manifest `assets`、`source_occurrences` 和来源哈希必须保留。
- [ ] 每个引用的视觉资产存在、可打开，且 manifest 中有来源页码/区域。
- [ ] PPTX/XML 或 PDF 布局提取结果与 OCR 候选分层保存；DOCX 只有在单独接入 active runner、契约和真实验收后才能加入输入范围。
- [ ] A4 默认通过 `vision-page-analysis` 经 SSH 调用 `mac96`，使用 `mode=full`、`pass_strategy=single`，每个请求只包含当前一张页面图或区域图；提示词不得调用 OCR、读取其他文件或参考前后图片/页面。
- [ ] 统一视觉证据保存在 `evidence/vision-page-analysis.json`，记录图片资产 ID、来源指纹、图片 SHA-256、页码/区域、模式、请求策略、实际调用次数、提示词版本、provider、模型、transport、请求时间、`text`、`visual` 和状态；不得记录密钥、认证信息或订阅地址。
- [ ] 分页执行器每处理一页都必须同时落盘 `evidence/vision-page-analysis-pNNN.json`、`evidence/vision-text-transcription-pNNN.json` 和 `page-units/pNNN.md`；三者缺一不得把该页记录为可恢复完成，也不得要求用户在提示词中重复声明生成 `page-units`。
- [ ] 每个 `page-units/pNNN.md` 生成后必须进入 `markdown-content-repair`；默认先调用局域网语言模型，输入只包含当前页 Markdown，不包含图片、OCR JSON、bbox、manifest、其他页面或 evidence。
- [ ] 局域网修复成功后在线模型调用次数必须为 0；局域网不可用、超时、不可解析或未通过结构守卫时，只有明确授权才允许当前在线模型回退一次，并记录失败原因与授权状态。
- [ ] 修复写回前必须检查并保留页面编号、原始页面图链接和 `Unknown/needs-review` 关系边界；失败或未决结果不得覆盖原始 page-unit。
- [ ] 每页修复必须写入 `evidence/markdown-content-repair-pNNN.json`，并在 `reviews/markdown-content-repair.yaml` 中记录输入/输出哈希、路由、逐项差异和写回状态。
- [ ] 页面 Markdown 修复不单独运行 pytest 或页面独立 validator；修复适配器内的结构守卫和规定阶段的人工查看是该步骤的验证边界。完整资产包仍由 A7 独立验收。
- [ ] 关系密集、图表复杂、颜色/空间编码重要或明确要求高置信度时，才显式使用 `pass_strategy=dual`；文字层 fallback 可以调用当前对话视觉模型或 Apple Vision OCR，但不得用 OCR 伪造视觉结构。
- [ ] 复杂图、图文混排或关系密集区域有整页图或必要的局部图证据；整页图已足够时不生成 `complex-region`/`crop` 文件，并在报告中记录该判断。
- [ ] 新包使用 `output_layout: flat-assets-v1`，所有资产直接位于 `assets/`；不得创建 `assets/pages/`、`assets/regions/` 或 `assets/embedded/`。
- [ ] 对复杂结构或明确需要重新生成的视觉资产，存在 `evidence/visual-reconstruction-profile.yaml`，且结构版、提示词版、来源字段和未决项可独立重读；纯装饰或证据不足时明确记录 `not-applicable`/`insufficient-evidence`。
- [ ] A4 报告记录实际视觉复核范围。mac96 返回明确的未归类组合图、嵌套容器、颜色/线型编码、跨区/跨页关系、图例/注释时，必须登记为 `unknown_forms` 并保持 `needs-review`；模型没有返回可选字段或未执行开放集合扫描，不得单独制造 `needs-review`。
- [ ] 存在真实失败、冲突或未裁决关系时，页面/资产包最多为 `needs-review`，不能标记 `accepted`。
- [ ] 读者层修复严格按第 1 页单页、第 2–5 页小批量、第 6–100 页全量推进；前一阶段页面结构守卫未通过时不得启动下一阶段。
- [ ] 修复一页时输入只包含当前页面单元和当前页面图；不得加载其他页面正文，也不得修改 OCR JSON、bbox、置信度、逐页 Vision JSON、页面图或原始 `manifest.yaml`。
- [ ] 页面单元具备页面编号、语义标题、正文、列表/图示文字、表格与公式、解读、举例与思考、模型结构、已确认的关系说明、原始页面图和待核对项。
- [ ] `实用公式` 与公式各自单独成行；举例不与公式或解读粘成同一个列表项；图示标签不伪装成普通正文。
- [ ] 页面单元按固定页面编号升序合并；原始页面图链接保持不变；合并不得重新推断、摘要、翻译或补写来源不存在的文字。
- [ ] `document-temp.md` 只能是页面单元的派生预览，不能替代最终 `index.md` 或成为新的事实来源。
- [ ] 未确认图示关系保持 `Unknown`/`needs-review`，不得生成未经视觉确认的箭头、层级、流程、矩阵或颜色关系说明。
- [ ] 第 1 页页面结构守卫通过后，才允许启用第 2–5 页入口；本轮不处理第 2–100 页。
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
