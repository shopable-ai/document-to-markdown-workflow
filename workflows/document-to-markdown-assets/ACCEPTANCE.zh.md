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
- [ ] `document.md` 只包含读者语义和必要页面图链接；不得出现 `标题下正文文字块`、`视觉关系`、`来源与不确定性`、`A4 状态`、原生 bbox、对象 ID、模型调用或 OCR 诊断字段。
- [ ] 有可确认语义标题时，标题前不得拼接源文件名、页码、哈希或对象 ID；来源文件名、页码和 occurrence 必须仍可在 `manifest/evidence` 中追溯。无语义标题的页码 fallback 必须带 `needs-review`。
- [ ] 每个页面/章节遵循固定阅读顺序：标题 → 主题/范围 → 核心文字、列表、表格、分区 → 图示关系说明 → 场景/限制/来源与不确定性 → 【需要保留的】原始页面图、按需复杂区域图、嵌入图片 → 待核对/未知形式清单；图片不得置于正文文字之前。
- [ ] 每个引用的视觉资产存在、可打开，且 manifest 中有来源页码/区域。
- [ ] PPTX/XML、PDF 布局或 DOCX 结构提取结果与 OCR 候选分层保存。
- [ ] 复杂图、图文混排或关系密集区域有整页图或必要的局部图证据；整页图已足够时 `assets/regions/` 为空是通过条件。
- [ ] A4 报告记录实际视觉复核范围。mac96 返回明确的未归类组合图、嵌套容器、颜色/线型编码、跨区/跨页关系、图例/注释时，必须登记为 `unknown_forms` 并保持 `needs-review`；模型没有返回可选字段或未执行开放集合扫描，不得单独制造 `needs-review`。
- [ ] 存在真实失败、冲突或未裁决关系时，页面/资产包最多为 `needs-review`，不能标记 `accepted`。
- [ ] `file_name` 为稳定英文 ASCII 名称，`display_name` 保留中文语义；原始文件名只作为 `original_filename` 证据，不参与命名。
- [ ] 同名资产通过 `source_occurrence_id`、来源指纹和 SHA-256 区分；来源出现记录与文件名解耦。
- [ ] 冲突、缺失和不可用复核均显式标记为 `Unknown`、`needs-review` 或失败态。
- [ ] 不调用 GPT-image-2，不生成 PPTX/PDF。

## 推荐测试夹具

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
