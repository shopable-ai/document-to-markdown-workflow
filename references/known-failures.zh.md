# 已知错误与失败模式目录

本目录保存从需求、Workflow、Skill、契约、验收器和已有执行路径中提炼出的长期错误模式。它不是某一次运行的报告，也不把“规格中已经考虑”写成“真实运行已经发生”。

每个条目的 `状态` 只说明当前需要如何对待：

- `spec-known`：需求或工程规格已经明确要求防止，但还不能证明真实运行发生过；
- `needs-verification`：需要用当前 source、当前运行 ID 和独立证据确认；
- `fixture-covered`：已有夹具或静态检查覆盖了边界，但不等于真实业务通过；
- `observed`：只有在真实运行证据明确记录后才能使用；
- `resolved`：有修改前后证据、独立复核和恢复记录，不能只凭代码改动标记。

错误目录的作用是把“问题是什么”与“本轮运行是否发生”分开，并让它们回到：

```text
Requirement → Chain → Spec → Task → Workflow → Skill/Tool → Run → Evidence → Verdict → Claim
```

## 目录字段与证据边界

每条 K 条目都应能回答“现在知道什么、最早在哪里能发现、谁负责裁决”。新增或修订条目使用以下字段；历史条目未补齐前，保留原文字并标记 `needs-verification`，不得把缺字段当成已解决：

| 字段 | 允许值或要求 |
| --- | --- |
| `证据类别` | `spec-known`、`code-inferred`、`fixture-covered`、`observed`、`unknown`、`historical-compatibility` |
| `最早检测点` | `preflight`、`A1`、`A2`、`A3`、`A4`、`A5`、`A6`、`A7`、`human-review`、`production-only` |
| `执行前可判定性` | `deterministic`、`partial`、`not-detectable` |
| `阻断级别` | `P0`、`P1`、`P2`；P0 必须 fail-closed |
| `对应机器规则` | `contracts/workflow-a-preflight.yaml` 中的 `check_id`，没有则写 `none` |
| `验证入口` | validator、测试或人工复核命令；没有执行过不得写 `passed` |
| `保留证据` | source SHA、页/节点/区域、资产、失败报告或 Unknown 的具体路径 |
| `不能前移原因` | 说明为什么必须等到阶段内、真实运行或人工复核 |

证据类别严格区分：规格/代码证明的是边界，fixture 证明的是夹具，真实 source + 独立 validator 才能支撑生产结论。没有真实运行证据时，不得把任何 K 条目标记为 `observed` 或 `resolved`。

## K-001 读者层泄漏来源文件名或页码前缀

- **症状**：每页的语义标题前被自动加上源文件名、`第 N 页` 或类似内部定位前缀，例如“源文件名｜第 24 页｜提升领导力的 3357”；语义标题因此被截断、重复或难以合并阅读。
- **影响**：`index.md` 把来源追溯信息误当成正文，读者层与工程层混在一起；跨文件合并时还会制造重复标题。
- **检测证据**：检查 `index.md` 的每个页面标题、正文和图片前后顺序；对照 `manifest.yaml` 的 `original_filename`、`selected_pages`、`source_occurrences`。原始文件名和页码应可在 manifest/evidence 中追溯，而不是作为语义标题前缀。
- **期望处理**：有语义标题时只输出语义标题；来源文件名、页码、对象 ID 和指纹放入 `manifest/evidence`。若读者确实需要定位，可在正文和关系说明之后增加单独的“来源与不确定性”说明，不得放在语义标题前。没有可确认标题时才允许使用“第 N 页”作为临时标题，并必须进入 `needs-review`。
- **当前实现风险**：历史兼容 runner 仍可能使用 `## 第 {page} 页：{title}`，但 active source runner 已改为只在无语义标题时使用页码 fallback，并把 source filename/page 放入 manifest/evidence。该静态修复仍需用当前 source 的真实 Run/Evidence 验证，不能直接晋级为 `resolved`。
- **状态**：`spec-known`；当前真实来源是否出现需 `needs-verification`。

## K-002 标题退化为页码或固定占位词

- **症状**：页面只有“第 N 页”、固定模板标题或空标题，正文却有可提取文字；或者旧示例标题被带入新 source。
- **影响**：人会误以为页面没有语义，或把上一份材料的内容当成当前文件内容。
- **检测证据**：smoke report、`content-classification`、native content model、当前 source 指纹和 `index.md` 的标题/正文对应关系。
- **期望处理**：优先从当前 source 的原生结构获得标题；必要时以 OCR/视觉模型给出候选并标为待复核。旧 fixture 只能作为测试边界，不能作为填充答案。
- **状态**：`spec-known`；已有 smoke/fixture 门禁，真实覆盖仍需按 source 验证。

## K-003 只剩关系表、图片或空语义占位

- **症状**：页面没有标题和正文，只输出关系表、整页图、`当前页面未提取到可确认的文字或关系`，或只写运行诊断。
- **影响**：Markdown 不可独立阅读，也无法判断是源文件真的没有文字还是提取失败。
- **检测证据**：smoke 的 `body-or-ocr-nonempty`、`no-relation-only`、`no-placeholder-only`；独立 validator 的 reader-semantic-content 检查。
- **期望处理**：保留已确认文字、OCR 候选和页面图，同时明确缺失范围；不能用图片存在或关系存在替代正文语义。
- **状态**：`spec-known`；夹具已有边界，真实发生需运行证据。

## K-004 OCR/视觉候选静默覆盖原生结构

- **症状**：OCR 或视觉模型的候选文字直接替换 PPTX XML、PDF 布局或 DOCX OOXML 的原生结果，差异和置信度没有保存。
- **影响**：无法判断改动来自来源还是模型；冲突会被伪装成确定事实。
- **检测证据**：`native-content-model`、`ocr-candidates`、`reconciliation` 的输入 ID、决策、冲突和未决项是否齐全。
- **期望处理**：原生结果保持事实层；OCR/视觉只作为候选或关系复核，冲突进入 `needs-review`，不能静默覆盖。
- **状态**：`spec-known`；真实是否发生需检查当前 package 的差异证据。

## K-005 声称使用局域网视觉模型，但没有可回放证据

- **症状**：报告写“已完成 mac96/视觉复核”，但没有连接方式、目标、模型、请求时间、输入资产、结构化响应和失败状态；或仅凭本机无法访问就断言局域网资源不存在。
- **影响**：局域网资源是否可用、是否真正参与判断、失败后是否正确降级都无法验证。
- **检测证据**：`capability-probe.json`、`vision-page-analysis`、`evidence/vision-page-analysis.json`、`workflow-execution` 和当前 source 指纹；历史运行中的 `mac96-visual-review` 仅作为兼容别名保留。远端查找应先从 `ssh mac96` 的已知工作目录开始，不能把本机路径当成远端缺失。
- **期望处理**：局域网视觉模型是候选资源；SSH 只是现有 transport，未来可替换为其他受控本地网络调用。每次尝试必须记录文字层、结构层、视觉层、耗时和资源证据；Apple Vision OCR 加结构重建、小视觉模型和 30B 的主次由同页真实基准决定，视觉层不能由 OCR 替代，未决关系才进入 `needs-review`。
- **状态**：`spec-known`；当前网络、模型和能力必须在每次 Run 中重新验证。

## K-006 未授权或过度使用在线视觉模型

- **症状**：默认把整份文档或页面发送给在线模型，或没有用户明确授权、输入范围、成本和结果记录。
- **影响**：增加隐私、版权、成本和可复现风险，也会掩盖本地/局域网路径未完成的问题。
- **检测证据**：运行记录中的 provider、输入摘要、请求次数、成本摘要和授权边界；Workflow A 默认在线视觉模型调用数应为 0。
- **期望处理**：本地解析/渲染/复核优先，局域网模型作为视觉复核资源；在线视觉模型只在明确授权后调用，并单独记录成本、范围和失败。
- **状态**：`spec-known`。

## K-007 PPTX/PDF 候选误配或静默合并

- **症状**：仅因文件名相似、格式相近或页码相同，就把两个来源当成同一逻辑文档；或者候选比较后丢掉了未选来源的独立证据。
- **影响**：文字、页面图和资产可能来自不同材料，产生不可追溯的混合结果。
- **检测证据**：source inventory、候选声明、基础文件名语义、页数、代表页比较报告和两份来源指纹。
- **期望处理**：无明确候选声明时一源一 package；有候选声明时先比较代表页，再选择主来源，但保留两份候选证据，不能把比较报告当成第三个来源。
- **状态**：`spec-known`；已有 fixture/静态契约，真实候选仍需独立运行。

## K-008 视觉资产或来源出现记录断链

- **症状**：Markdown 图片链接不存在、MIME/尺寸/SHA 不匹配，或资产虽存在却找不到来源页、区域、对象和 occurrence。
- **影响**：人无法回看页面，后续复核和 Claim 不能成立。
- **检测证据**：独立 validator、`manifest.yaml`、`source_occurrences`、资产哈希和实际文件可打开性。
- **期望处理**：完整性或 provenance 失败时至少 `needs-review`，必要时 `failed`；不能以目录非空、runner 自报 complete 或 HTTP 200 代替资产证据。
- **状态**：`spec-known`；夹具覆盖目录契约，真实包仍需 validator。

## K-009 把 runner 自报完成、fixture 或静态检查当成业务通过

- **症状**：看到文件生成、stdout 为 complete、fixture 通过或契约校验通过，就声称当前真实转换正确。
- **影响**：静态规格、模拟输入和真实 source 的边界被抹平，失败、冲突和未决关系无法阻止错误 Claim。
- **检测证据**：当前 source 的真实 `run_id`、`reports/validation-result.json`、独立 validator 输出、失败报告和证据等级。
- **期望处理**：只有独立 validator 重读当前 package 后才能产生 `passed`；fixture 只能证明夹具边界，不能证明真实 source 或 mac96 E2E。
- **状态**：`spec-known`。

## K-010 失败后重跑覆盖旧证据或跳过 smoke/批次门禁

- **症状**：长文档直接全量运行，失败重试覆盖原目录，或把未通过的前一批当成可恢复起点。
- **影响**：无法知道哪一次输入、配置和页面产生了结论，也无法比较修复前后差异。
- **检测证据**：run record、attempt 目录、smoke report、batch checkpoint、source SHA 和不可变 prior package 记录。
- **期望处理**：先 smoke，再小批次；失败批次使用新 attempt 目录，保留旧证据，只从最近一个通过批次恢复。
- **状态**：`spec-known`；长时真实运行仍需观测。

## K-011 OCR/原生文字提取后没有语言质量修复

- **症状**：流程生成了 OCR 或原生文字，却直接进入 Markdown；错字、乱码、断句、重复、术语不一致和低质量转写没有经过受来源证据约束的语言修复。
- **影响**：页面可能“有文字”但不可读，质量评分会被结构/视觉资产掩盖；重复运行只会重复产生同一种糟糕文字。
- **检测证据**：原始页面单元、`evidence/markdown-content-repair-pNNN.json`、输入/输出哈希、逐项差异、模型/人工修复记录和修复后 re-audit。缺少这些证据时，不得声称文字已优化。
- **期望处理**：先保留页面单元和视觉证据，再优先调用局域网语言模型进行受限修复；局域网失败时只有明确授权才能调用当前在线模型；不新增来源外事实，无法确认的内容保持 `Unknown`/`needs-review`。
- **状态**：`adapter-available; real-LAN-proof-pending`；适配器存在不等于当前运行已成功，也不能把视觉复核当作语言优化证明。

## K-012 长时间运行没有逐页进度、停止点和遗漏报告

- **症状**：终端长时间没有可读进度，结束时只显示一个总状态；无法知道当前页/阶段、调用路径、已处理页、未开始页或哪个环节被跳过。
- **影响**：人无法及时停止错误运行，也无法判断“三小时运行”究竟完成了什么；后续重跑容易重复消耗并继续掩盖遗漏。
- **检测证据**：`progress-current.json`、`progress.jsonl` 阶段账本、对应页的 `progress-pNNN.json`、逐页 stage evidence、smoke report、checkpoint、selected-pages 与 evidence-pages 差集。
- **期望处理**：smoke 必须是独立、限时、可见的 Run；每个阶段开始/完成/失败/降级都输出事件和下一步；无进展或超时立即停止并保留证据，未有 smoke Verdict 不得继续后续页。
- **状态**：`spec-known`；现有 runner 有 smoke 参数和最终报告，但实时心跳、硬预算和遗漏差集仍需实际验证。

## K-013 输入契约声明的格式与生产 runner 不一致

- **症状**：历史 Skill/说明写有 DOCX，但当前 active runner 只实现 PPT/PPTX、PDF 和图片；调用方误以为 DOCX 已经具备同等级的原生提取和视觉资产证据，或绕过能力表直接调用。
- **影响**：需求、入口帮助和真实能力不一致；会把“规格支持”误报成“生产支持”，浪费后续提取、渲染和模型调用时间。
- **检测证据**：`workflow.yaml` 与 `workflow-registry.yaml` 的 active 输入声明、扩展 Skill/历史说明、runner 的实际后缀分支以及 `evidence/preflight.json` 的 `source-format-supported`。
- **期望处理**：在执行前用 production-capability registry 对照当前 runner；未实现格式直接 `failed`，而不是静默走图片/OCR 或生成空 package。新增格式必须补齐原生提取、渲染、资产、验收器和真实夹具后再扩大声明范围。
- **状态**：`spec-known`；preflight 已能在 A1 前阻断，DOCX 生产实现仍未完成。

## 使用规则

1. 新发现的错误先登记在这里，并注明它是规格风险、夹具边界还是当前 Run 的实际观察。
2. `observed` 只能由当前 source 的 Evidence 晋级，不能从旧运行、固定样例或模型自报推断。
3. 修复条目必须同时更新对应需求、Workflow/Skill/contract、测试或验收入口；只改说明文字不算 `resolved`。
4. 任何条目都不能替代 `reports/validation-result.json`、失败报告或人工复核。
