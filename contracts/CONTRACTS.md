# 活跃工程 YAML 人类契约目录

## 配对规则

- YAML 对精确字段、枚举、required 集合和机器 schema 权威。
- 本文解释目的、适用范围、上游、I/O、状态/失败、正常/异常/恢复、不变量、关系、失效和最小示例。
- 本文不能改写 requirements 中的业务语义；V2/V3 前，已有合同是 Brownfield candidate。
- human_reference 可以多对一指向本文，但必须落到唯一逐 YAML 锚点。

## contract-acceptance-scorecard

- contract_id: acceptance-scorecard；contract_version: 0.1.0；lifecycle: design-active；YAML: [acceptance-scorecard.yaml](acceptance-scorecard.yaml)
- 目的与范围：对 package、run 或治理对象执行 95+ 维度评分、硬门和独立性检查；上游是事前判据、baseline、projection、Run 和 Evidence。
- I/O：输入各维度分、hard gates、identity 和 digest；输出 Verdict status、quality band、claim ceiling。
- 状态/案例：正常为全部硬门通过；缺 Evidence、分数不足或生成者自验为 needs-review/failed；恢复需要新 validator execution。
- 不变量/失效/示例：高分不能掩盖硬失败；输入 digest 或基线变化使旧 scorecard stale；最小例为 100 分且生成者与验证者不同。

## contract-advertising-content-policy

- contract_id: advertising-content-policy；contract_version: 0.1.0；lifecycle: active；YAML: [advertising-content-policy.yaml](advertising-content-policy.yaml)
- 目的与范围：在忠实提取之后分类广告/推广内容；上游是完整可见文字，不能在基础提取时删除。
- I/O：输入已提取页面；输出 preserve、classify 或后置处理决定。
- 状态/案例：正常默认 preserve-only；不确定时 needs-review；恢复为保留原文并重新分类。
- 不变量/失效/示例：策略变化不改 raw；删除型处理需新授权；二维码领取页可后置分类但正文先保留。

## contract-artifact-reference

- contract_id: artifact-reference；contract_version: 0.1.0；lifecycle: design-active；YAML: [artifact-reference.yaml](artifact-reference.yaml)
- 目的与范围：统一引用 Source、Requirement、Baseline、Projection、Run、Evidence、Verdict 和 Claim；上游是已有权威对象。
- I/O：输入 locator、version/hash、authority/status/scope；输出可回放 ref。
- 状态/案例：正常引用可解析；candidate/test/historical 不能支持 Claim；恢复为补权威和版本。
- 不变量/失效/示例：引用不复制内容；目标版本变化使 ref stale；示例为 source:001 + sha256。

## contract-asset-extraction-strategy

- contract_id: asset-extraction-strategy；contract_version: 0.4.0；lifecycle: active；YAML: [asset-extraction-strategy.yaml](asset-extraction-strategy.yaml)
- 目的与范围：登记 PPT/PDF/图片资产提取、裁切/增强派生和路线比较；上游是当前 source 和授权。
- I/O：输入 source fingerprint、media type、pages；输出 assets、routes、quality/cost Evidence。
- 状态/案例：正常 complete；提取不足 needs-review；路线不可用 failed/not-run；恢复为保留原资产并换受控路线。
- 不变量/失效/示例：提取和理解分离；父图/子图可追溯；source hash 变化使全部派生失效；示例为 PDF page-render → crop。

## contract-change-record

- contract_id: change-record；contract_version: 0.1.0；lifecycle: design-active；YAML: [change-record.yaml](change-record.yaml)
- 目的与范围：记录上游变化、影响闭包和重验；上游是 versioned artifact 和 TRACEABILITY。
- I/O：输入 changed refs、reason、supersedes；输出 affected refs、impact classification、revalidation status。
- 状态/案例：正常 unaffected/review_required/invalidated；缺边按 review_required；恢复为补边、重建和重验。
- 不变量/失效/示例：invalidated 必须列 affected refs；示例为 requirement v2 使旧 Workflow/Test/Evidence stale。

## contract-cognitive-reasoning-overlay

- contract_id: cognitive-reasoning-overlay；contract_version: 0.1.0；lifecycle: reference-candidate；YAML: [cognitive-reasoning-overlay.yaml](cognitive-reasoning-overlay.yaml)
- 目的与范围：记录认知对象、候选推理和时序叠加；上游是授权来源，不是实现。
- I/O：输入原始陈述、事实、替代解释、Unknown；输出 candidate reasoning record。
- 状态/案例：candidate 可 confirmed/rejected/unknown；缺 owner 或依据不晋升；恢复为人工确认或保留 Unknown。
- 不变量/失效/示例：non-normative，不能直达 Claim；来源变化使推理 stale；示例为“PDF 可能更稳定”保留替代解释。

## contract-cross-page-markdown-reconciliation

- contract_id: cross-page-markdown-reconciliation；contract_version: 0.1.0；lifecycle: active；YAML: [cross-page-markdown-reconciliation.yaml](cross-page-markdown-reconciliation.yaml)
- 目的与范围：只对显式相邻页断点生成派生合并建议；上游是两个不可变页面结构记录。
- I/O：输入 previous/current record；输出 merge suggestion、Evidence 和三态 status。
- 状态/案例：正常 passed；边界不清 needs-review；输入缺失 failed；恢复为保留独立页。
- 不变量/失效/示例：不跨非相邻页、不改 raw；任一页 hash 变化使建议失效；示例为跨页目录条目延续。

## contract-document-driven-project-workflow

- contract_id: document-driven-project-workflow；contract_version: 0.2.0；lifecycle: design-active；YAML: [document-driven-workflow.yaml](document-driven-workflow.yaml)
- 目的与范围：把 C01–C40 人类主链投影为机器可检的 C 节点和 P01–P16 压缩导航；上游是 PROJECT-DELIVERY-WORKFLOW。
- I/O：输入主链、U 覆盖和文件 refs；输出 stage mapping、gates、rules。
- 状态/案例：正常 C40/P16 无缺口；C/P 冲突为 contract-doc-drift；恢复为先修人类主链再刷新合同。
- 不变量/失效/示例：C 是权威，P 不是第二主链；示例 P07 回指 C10–C18。

## contract-ebook-page-pruning

- contract_id: ebook-page-pruning；contract_version: 1.0.0；lifecycle: active-optional；YAML: [ebook-page-pruning.yaml](ebook-page-pruning.yaml)
- 目的与范围：完整页集合汇合后清洗知识库工作副本；上游是完整 page-units 和显式用户选择。
- I/O：输入页集合、hash-lock 计划和理由；输出 preview/apply ledger 与重建结果。
- 状态/案例：正常先 preview 后 apply；hash/页数变化时删除零项；恢复为重建计划。
- 不变量/失效/示例：不删原 PDF/raw Evidence，不提升转换 Verdict；示例为删除重复封面后重建 index。

## contract-failure-report

- contract_id: failure-report；contract_version: 0.1.0；lifecycle: active；YAML: [failure-report.yaml](failure-report.yaml)
- 目的与范围：记录可诊断失败、降级和未决项；上游是阶段事件和 diagnostics。
- I/O：输入 failure id、stage、severity、retryability、Evidence；输出 none/needs-review/failed 报告。
- 状态/案例：无失败也输出空报告；异常必须有 remediation；恢复后保留旧项并转 resolved。
- 不变量/失效/示例：失败报告不是 Verdict；示例为 asset link missing → failed + evidence path。

## contract-gpt-image-generation-request

- contract_id: gpt-image-generation-request；contract_version: 0.1.0；lifecycle: active；YAML: [gpt-image-generation-request.yaml](gpt-image-generation-request.yaml)
- 目的与范围：Workflow B 的无秘密单图请求/结果；上游是选定 Markdown、visual intent 和授权。
- I/O：输入 section digest、visual plan、prompt；输出 provider request、单图和调用摘要。
- 状态/案例：正常生成一个 raster；未授权或秘密字段失败；恢复为清理请求或重新授权。
- 不变量/失效/示例：不生成 PPT/PDF/批次；source digest 变化使请求 stale；示例为一节 Markdown → one PNG。

## contract-knowledge-map-image-to-md

- contract_id: knowledge-map-image-to-md；contract_version: 0.1.0；lifecycle: active-profile；YAML: [knowledge-map-profile.yaml](knowledge-map-profile.yaml)
- 目的与范围：旧 knowledge-map 调用名到 Workflow A 的兼容 profile；上游是明确知识地图来源。
- I/O：输入 profile 和 source；输出 A1–A7 的差异参数，不新增阶段。
- 状态/案例：正常路由 A；独立执行历史流程被拒绝；恢复为 canonical Workflow A。
- 不变量/失效/示例：compatibility 不是第三 Workflow；示例为旧 alias 加载 profile 后进入 A。

## contract-long-task-interaction

- contract_id: long-task-interaction；contract_version: 1.1.0；lifecycle: active；YAML: [long-task-interaction.yaml](long-task-interaction.yaml)
- 目的与范围：隔离后台资源消耗和对话消息预算；上游是已选显式路线和长任务阈值。
- I/O：输入 run/task identity、source、pages；输出 start/status/cancel/resume 摘要和 progress files。
- 状态/案例：正常 detached-silent；异常才通知；取消需身份匹配；恢复从 checkpoint。
- 不变量/失效/示例：不静默改路线、不周期刷屏；示例 start 后只返回 run id 和任务目录。

## contract-markdown-asset-package

- contract_id: markdown-asset-package；contract_version: 0.4.0；lifecycle: active；YAML: [markdown-asset-package.yaml](markdown-asset-package.yaml)
- 目的与范围：显式 engineering-output 的完整工程包；上游是当前 source Run。
- I/O：输入 source/records/assets；输出 index、manifest、reports、Evidence。
- 状态/案例：完整包按 required 校验；reader-only 不强制此布局；缺文件 failed；恢复为重建当前包。
- 不变量/失效/示例：不能把 engineering 包要求强加给纯文本 reader；示例 index + manifest + reports。

## contract-markdown-collection-output-layout

- contract_id: markdown-collection-output-layout；contract_version: 0.1.0；lifecycle: active；YAML: [markdown-collection-output-layout.yaml](markdown-collection-output-layout.yaml)
- 目的与范围：多个已验收页面单元整理成读者集合；上游是来源 package 的 page-units/assets。
- I/O：输入 source units、assets、category map；输出集合 Markdown、assets 和迁移记录。
- 状态/案例：正常重写链接；冲突或缺资产 needs-review/failed；恢复为回到独立 source package。
- 不变量/失效/示例：不是 Workflow A canonical package；示例多个页面单元合成一个中文集合。

## contract-markdown-content-repair

- contract_id: markdown-content-repair；contract_version: 0.4.0；lifecycle: active；YAML: [markdown-content-repair.yaml](markdown-content-repair.yaml)
- 目的与范围：页面单元后的读者层内容结构修复；上游是一个当前 page-unit 和只读来源边界。
- I/O：输入当前页 Markdown；输出修复文本、diff、input/output hash 和 Evidence。
- 状态/案例：正常局域网优先；外部 fallback 需授权；失败时不替换原文；恢复为重试或人工。
- 不变量/失效/示例：一次只处理当前页，不改 raw；输入 hash 变化使旧 repair stale；示例修正标题层级。

## contract-markdown-post-merge-model-routing

- contract_id: markdown-post-merge-model-routing；contract_version: 1.0.0；lifecycle: active；YAML: [markdown-post-merge-model-routing.yaml](markdown-post-merge-model-routing.yaml)
- 目的与范围：合并后 Markdown 的模型路由、回退、结构守卫和审计；上游是 document-temp/heading repair。
- I/O：输入 merged Markdown、route config、authorization；输出候选 final Markdown 和 audit。
- 状态/案例：正常 deterministic 或受控 route；explicit route 失败时 fail closed；恢复不静默换模型。
- 不变量/失效/示例：模型失败不替换输入；示例 LAN route 失败后按授权选择 codex-default。

## contract-native-content-model

- contract_id: native-content-model；contract_version: 0.1.0；lifecycle: active；YAML: [native-content-model.yaml](native-content-model.yaml)
- 目的与范围：A2 跨格式原生提取最小模型；上游是当前 source。
- I/O：输入 source fingerprint；输出 text blocks、nodes、relations、coordinates、locations 和 diagnostics。
- 状态/案例：complete/partial/needs-review/unavailable/failed；原生不足时进入候选复核；恢复保留 partial。
- 不变量/失效/示例：不伪造缺失结构；source hash 变化失效；示例 PPTX text blocks + source locations。

## contract-ocr-markdown-structure

- contract_id: ocr-markdown-structure；contract_version: 0.2.0；lifecycle: active；YAML: [ocr-markdown-structure.yaml](ocr-markdown-structure.yaml)
- 目的与范围：把 OCR line、bbox、confidence 保守投影为 Markdown；上游 OCR JSON 不可变。
- I/O：输入 candidates；输出 Markdown、candidate provenance 和三态 status。
- 状态/案例：正常 passed；低置信/关系未决 needs-review；输入非法 failed；恢复回到 OCR/人工。
- 不变量/失效/示例：bbox/confidence 不被覆盖；输入 hash 变化失效；示例多行标题候选。

## contract-page-markdown-noise-cleanup

- contract_id: page-markdown-noise-cleanup；contract_version: 1.0.0；lifecycle: active；YAML: [page-markdown-noise-cleanup.yaml](page-markdown-noise-cleanup.yaml)
- 目的与范围：页面单元生成后、语言修复前确定性清理页眉页脚/边界噪声；上游为选定 page-units。
- I/O：输入 pages、profile、scan/apply mode；输出 decisions、Evidence 和修复页。
- 状态/案例：默认 scan；apply 需明确；不确定项不删；恢复为保留原页和 plan。
- 不变量/失效/示例：只处理边界规则，不做语义改写；profile/hash 变化需重扫；示例删除重复页脚。

## contract-page-markdown-unit

- contract_id: page-markdown-unit；contract_version: 0.5.0；lifecycle: active；YAML: [page-markdown-unit.yaml](page-markdown-unit.yaml)
- 目的与范围：定义单页 reader Markdown、图级回填、阶段门、确定性合并和派生预览。
- I/O：输入当前页 Markdown/图及 metadata；输出 pNNN.md、document-temp preview 和 merge record。
- 状态/案例：第1页→2–5→6–total；守卫失败停止；恢复只读取当前页。
- 不变量/失效/示例：raw Evidence 只读、固定页序、preview 非事实源；示例 p001.md passed 后解锁小批量。

## contract-pdf-route-detection

- contract_id: pdf-route-detection；contract_version: 0.4.0；lifecycle: active；YAML: [pdf-route-detection.yaml](pdf-route-detection.yaml)
- 目的与范围：转换前文档级预检和运行时逐页路线候选；上游是当前 PDF。
- I/O：输入 source/pages；输出 native-only、native-plus-visual、visual-only 候选和 Evidence。
- 状态/案例：可判定时选路；冲突 needs-review；不可读 failed；恢复为人工路线或保守 visual。
- 不变量/失效/示例：后台管理层不静默改线；source hash 变化重检；示例扫描页选择 visual-only。

## contract-projection-record

- contract_id: projection-record；contract_version: 0.1.0；lifecycle: design-active；YAML: [projection-record.yaml](projection-record.yaml)
- 目的与范围：记录已确认 baseline 到 Workflow/Skill/Tool/Model/Context 的实际选择和排除。
- I/O：输入 baseline、候选 refs、authorization；输出 selected/excluded refs、reason 和 status。
- 状态/案例：正常 projected；无 confirmed baseline 或授权 blocked；恢复为回到 V3/选择记录。
- 不变量/失效/示例：projection 不能批准需求；baseline 或 capability version 变化使其 stale；示例选本地 OCR、排除在线模型。

## contract-promotion-matrix

- contract_id: promotion-matrix；contract_version: 0.1.0；lifecycle: design-active；YAML: [promotion-matrix.yaml](promotion-matrix.yaml)
- 目的与范围：隔离 reasoning、baseline、run/evidence、verdict 和 claim 状态空间。
- I/O：输入 layer、from/to、所需 Evidence/owner；输出允许或 forbidden transition。
- 状态/案例：candidate 可在证据齐全后 confirmed；candidate 不能直达 claim.allowed；恢复为补合法前驱。
- 不变量/失效/示例：confidence 不等于 status；矩阵版本变化使旧 promotion 需复核；示例 candidate + confirmation record → confirmed。

## contract-reader-markdown-output-layout

- contract_id: reader-markdown-output-layout；contract_version: 1.1.0；lifecycle: active；YAML: [reader-markdown-output-layout.yaml](reader-markdown-output-layout.yaml)
- 目的与范围：Workflow A 默认最小 reader 包；上游是已组装 Markdown 和实际引用资产。
- I/O：输入 index/assets candidates；输出 index.md 和可选 assets。
- 状态/案例：纯文本只有 index；有必要图时 assets；断链 failed；恢复为复制/重写链接。
- 不变量/失效/示例：不强制 manifest/reports；示例 index.md + assets/flow.png。

## contract-recognition-route-benchmark

- contract_id: recognition-route-benchmark；contract_version: 0.1.0；lifecycle: active；YAML: [recognition-route-benchmark.yaml](recognition-route-benchmark.yaml)
- 目的与范围：相同真实页、图片 hash 和维度下比较识别路线。
- I/O：输入 run/source/page images 和 routes；输出各路线原始结果、耗时和资源。
- 状态/案例：正常所有候选可比；输入不一致则 comparison invalid；恢复为统一输入重跑。
- 不变量/失效/示例：不按模型说明书选路；image/prompt/schema 变化重跑；示例 OCR vs 视觉模型同页比较。

## contract-recognition-route-quality-assessment

- contract_id: recognition-route-quality-assessment；contract_version: 0.1.0；lifecycle: active；YAML: [recognition-route-quality-assessment.yaml](recognition-route-quality-assessment.yaml)
- 目的与范围：人工可复核地评估 benchmark 的文字和结构质量。
- I/O：输入 benchmark report 和 page assessments；输出 passed/needs-review 及 route selection。
- 状态/案例：非空不等于 passed；关键结构缺失 needs-review；恢复为修 schema/prompt/route 重测。
- 不变量/失效/示例：必须回指相同 image hash；benchmark 变化使 assessment stale；示例路线 A 文字全但表格层级失败。

## contract-reconciliation

- contract_id: reconciliation；contract_version: 0.1.0；lifecycle: active；YAML: [reconciliation.yaml](reconciliation.yaml)
- 目的与范围：分层合并 native、OCR 和 visual candidates；上游候选保持可追溯。
- I/O：输入三类 candidates；输出 decisions、conflicts、unresolved、coverage 和 diagnostics。
- 状态/案例：complete/needs-review/unavailable/failed；冲突不静默覆盖；恢复为人工裁决或保留 Unknown。
- 不变量/失效/示例：每个 decision 回指 input ids；任一候选 hash 变化重算；示例 native 数字与 OCR 冲突。

## contract-document-to-markdown-requirement-chain

- contract_id: document-to-markdown-requirement-chain；contract_version: 0.2.0；lifecycle: awaiting-human-confirmation；YAML: [requirement-chain.yaml](requirement-chain.yaml)
- 目的与范围：把 requirements Markdown 到 design/spec/task/workflow 的交接门机器化；上游是 V1/V2/V3。
- I/O：输入 authority files、required fields、gates；输出 allowed/blocked handoff。
- 状态/案例：当前 awaiting-human-confirmation；V1/V2/V3 未过保持 blocked；恢复为人工基线。
- 不变量/失效/示例：机器合同不定义需求；requirements version 变化使 handoff stale；示例 V2 未确认禁止正式工程任务。

## contract-source-inventory

- contract_id: source-inventory；contract_version: 0.1.0；lifecycle: active；YAML: [source-inventory.yaml](source-inventory.yaml)
- 目的与范围：A1 当前来源只读预检；上游是单一 source 和范围。
- I/O：输入文件；输出 fingerprint、media type、page/dimensions、layers、readability 和 diagnostics。
- 状态/案例：complete/needs-review/source-invalid/unsupported-format；恢复为修来源或明确不支持。
- 不变量/失效/示例：fingerprint 来自内容而非文件名；文件内容变化重检；示例 PDF 104 pages + sha256。

## contract-source-visual-asset

- contract_id: source-visual-asset；contract_version: 0.5.0；lifecycle: active；YAML: [source-visual-asset.yaml](source-visual-asset.yaml)
- 目的与范围：统一登记 page render、embedded、crop、enhanced 和 input image。
- I/O：输入资产、来源位置、parent/occurrence；输出单一 manifest item。
- 状态/案例：正常有 stable asset id/hash；缺 parent 或位置 needs-review；恢复为补登记，不改文件来源。
- 不变量/失效/示例：派生链可回到 source；source/asset hash 变化使 ref stale；示例 page-3 crop 继承 parent id。

## contract-stage-evidence

- contract_id: stage-evidence；contract_version: 0.1.0；lifecycle: active；YAML: [stage-evidence.yaml](stage-evidence.yaml)
- 目的与范围：记录 A/B 每阶段可验证 Evidence；上游是一次 Run 的阶段结果。
- I/O：输入 stage/status/level/paths/diagnostics/commands；输出 append-only Evidence item。
- 状态/案例：complete/needs-review/unavailable/failed/not-run；缺路径不能 complete；恢复产生新 item。
- 不变量/失效/示例：设计、fixture 和生产 Evidence 分层；input/version 变化失效；示例 A3 failed + log path。

## contract-temporal-case

- contract_id: temporal-case；contract_version: 0.1.0；lifecycle: design-active；YAML: [temporal-case.yaml](temporal-case.yaml)
- 目的与范围：分开事件、状态迁移、顺序、分支、恢复和因果依据。
- I/O：输入 events/relations/decision points；输出可检查 temporal case。
- 状态/案例：关系可 observed/inferred/unknown；causal 缺 mechanism/evidence 被拒绝；恢复改为 unknown 或补依据。
- 不变量/失效/示例：先发生不等于导致；事件证据变化重审；示例 page1 pass 是 page2-5 dependency。

## contract-test-run-record

- contract_id: test-run-record；contract_version: 0.1.0；lifecycle: active；YAML: [test-run-record.yaml](test-run-record.yaml)
- 目的与范围：记录测试运行，不是业务转换结果；上游是 test command/fixture。
- I/O：输入测试范围、版本、日志；输出 tests/runs 下 report/summary。
- 状态/案例：正常 pass/fail/skip；基础设施不可用单列；恢复为新 test run。
- 不变量/失效/示例：test evidence 不支持生产 Claim；代码或 fixture 变化使结果 stale；示例 45 pytest passed。

## contract-vision-page-analysis

- contract_id: vision-page-analysis；contract_version: 0.3.0；lifecycle: active；YAML: [vision-page-analysis.yaml](vision-page-analysis.yaml)
- 目的与范围：对当前一张页面或区域图生成文字/布局/关系候选。
- I/O：输入 image、mode、authorization、model/prompt version；输出 candidates、Evidence、diagnostics。
- 状态/案例：路线可用产生 candidate；传输/解析失败 unavailable/failed；真实冲突 needs-review；恢复换授权路线。
- 不变量/失效/示例：候选不自动 confirmed；image/model/prompt 变化失效；示例 full mode 返回 text + relations。

## contract-vision-text-transcription

- contract_id: vision-text-transcription；contract_version: 0.2.0；lifecycle: compatibility-only；YAML: [vision-text-transcription.yaml](vision-text-transcription.yaml)
- 目的与范围：vision-page-analysis text mode 的兼容契约。
- I/O：输入当前图和旧调用形态；输出映射到 canonical analysis 的文字候选。
- 状态/案例：正常 compatibility route；不支持历史固定 fallback；恢复为直接使用 vision-page-analysis。
- 不变量/失效/示例：不成为第二套主路线；canonical version 变化需复核；示例旧 transcription 调用映射 text mode。

## contract-visual-asset-classification

- contract_id: visual-asset-classification；contract_version: 0.2.0；lifecycle: active；YAML: [visual-asset-classification.yaml](visual-asset-classification.yaml)
- 目的与范围：只读生成 retain/crop/candidate-remove/needs-review 候选。
- I/O：输入 page assets、hash、prompt/model；输出 candidate-only decisions 和 diagnostics。
- 状态/案例：complete/needs-review/failed；模型判断永不直接删除；恢复为人工或 deterministic audit。
- 不变量/失效/示例：apply=false；输入/prompt/model 变化失效；示例 page 5 candidate-remove 但等待硬门。

## contract-visual-asset-pruning

- contract_id: visual-asset-pruning；contract_version: 0.6.0；lifecycle: active；YAML: [visual-asset-pruning.yaml](visual-asset-pruning.yaml)
- 目的与范围：读者视觉资产 preview/apply 决策和同步结果。
- I/O：输入 source/pages/assets/hash；输出 kept/excluded、reason、synchronization 和 Evidence。
- 状态/案例：preview 正常；apply 需 hash-lock 和质量门；冲突 needs-review；恢复为删除零项。
- 不变量/失效/示例：原 source 禁删，复杂视觉 fail closed；hash 变化计划失效；示例移除纯文字冗余截图。
- Page-hybrid：工程验收后复核终稿每一页（含原本无图页）。3.0.0 对合格扫描候选使用本地 Tesseract 与原 Apple Vision 交叉验证精确文字、阅读顺序和独立像素遮罩；验收离线重放绑定到同页终稿的证据，历史 2.0.0 不变。省图仅发生在内部 reader/ 投影，无可靠证据又无图保底的页为 needs-review；不改工程正文或旧交付。

## contract-visual-reconstruction-profile

- contract_id: visual-reconstruction-profile；contract_version: 0.4.0；lifecycle: active；YAML: [visual-reconstruction-profile.yaml](visual-reconstruction-profile.yaml)
- 目的与范围：保存复杂视觉的结构、文字、样式、prompt 和可选 Mermaid 候选。
- I/O：输入 accepted visual asset 和 confirmed relations；输出 profile、negative constraints、source refs。
- 状态/案例：可确认字段写入；未知留 Unknown；恢复为人工复核，不补全关系。
- 不变量/失效/示例：保留原图/PNG fallback，不声称像素等价；source asset 变化失效；示例流程图 nodes/edges profile。

## contract-workflow-a-batch-run

- contract_id: workflow-a-batch-run；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-a-batch.yaml](workflow-a-batch.yaml)
- 目的与范围：多来源队列或长 PDF 的 smoke、分批、checkpoint 和单包边界。
- I/O：输入 sources/pages/concurrency；输出 per-source run refs、attempts、batch summary。
- 状态/案例：running/succeeded/needs-review/failed；单包失败不能被批次掩盖；恢复从 source checkpoint。
- 不变量/失效/示例：一来源一 package；source set 变化重算；示例 3 source queue with one needs-review。

## contract-workflow-a-output-layout

- contract_id: workflow-a-output-layout；contract_version: 0.2.0；lifecycle: active；YAML: [workflow-a-output-layout.yaml](workflow-a-output-layout.yaml)
- 目的与范围：显式 engineering-output 的完整目录。
- I/O：输入 assembled package artifacts；输出 index、manifest、processing/acceptance/failure reports。
- 状态/案例：所有 required 可解析才完整；reader-only 不使用本布局；恢复补当前包缺失文件。
- 不变量/失效/示例：目录存在不等于内容 passed；spec/source 变化需重建；示例 reports/validation-result.json。

## contract-workflow-a-preflight

- contract_id: workflow-a-preflight；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-a-preflight.yaml](workflow-a-preflight.yaml)
- 目的与范围：A1 和任何模型/转换前阻断廉价可确定风险。
- I/O：输入 source/output/pages/policy；输出 passed/needs-review/failed、can_start、failures/warnings。
- 状态/案例：passed 才可开始；路径/磁盘/范围风险阻断；恢复修前置条件重跑。
- 不变量/失效/示例：preflight 只证明可开始，不证明内容正确；输入变化重跑；示例 source readable + output writable。

## contract-workflow-a-production-runtime-context

- contract_id: workflow-a-production-runtime-context；contract_version: 1.1.0；lifecycle: active；YAML: [workflow-a-production-runtime-context.yaml](workflow-a-production-runtime-context.yaml)
- 目的与范围：生产执行最小上下文，避免重读需求史、完整 Workflow 和源码。
- I/O：输入 source/output/pages 和稳定接口；输出允许加载的精简契约/状态摘要。
- 状态/案例：正常 production trigger；设计或 debug 不适用；恢复转入对应开发上下文。
- 不变量/失效/示例：不加载 discovery/.audit/archive；contract version 变化更新 snapshot；示例日常“文档转 Markdown”。

## contract-workflow-a-source-runner

- contract_id: workflow-a-source-runner；contract_version: 0.3.0；lifecycle: active；YAML: [workflow-a-source-runner.yaml](workflow-a-source-runner.yaml)
- 目的与范围：单一 source 的稳定 runner 接口和 reader-only/engineering-output 边界。
- I/O：输入 source/output/pages/mode/profile；输出 summary 和对应 package。
- 状态/案例：passed/needs-review/failed + exit codes；参数错为 2；恢复修输入或重跑新 attempt。
- 不变量/失效/示例：不自动配对来源；plan-only 不证明执行；示例 one PDF all pages reader-only。

## contract-workflow-b-output-layout

- contract_id: workflow-b-output-layout；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-b-output-layout.yaml](workflow-b-output-layout.yaml)
- 目的与范围：Workflow B 单图成功输出目录。
- I/O：输入 selected section/visual plan/request；输出恰好 one generated image 和记录。
- 状态/案例：required 文件齐全；provider 失败为 failed；恢复新 request/run。
- 不变量/失效/示例：不包含 PPT/PDF/SVG/批次；source digest 变化失效；示例 generated-image.png。

## contract-workflow-handoff

- contract_id: workflow-handoff；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-handoff.yaml](workflow-handoff.yaml)
- 目的与范围：A 向 B 可选传递 Markdown 片段和允许的视觉参考。
- I/O：输入 source package/id/fingerprint/selected refs；输出 bounded handoff。
- 状态/案例：available/needs-review/unavailable；缺授权或来源指纹不可用；恢复重新选择。
- 不变量/失效/示例：handoff 不合并 Workflow、不提升 Evidence；source package 变化失效；示例 one section + one asset ref。

## contract-workflow-run-record

- contract_id: workflow-run-record；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-run-record.yaml](workflow-run-record.yaml)
- 目的与范围：Workflow Run 生命周期、输入指纹、版本 snapshot 和 Evidence level。
- I/O：输入 workflow/source/projection/environment；输出 append-only run record。
- 状态/案例：planned/running/needs-review/succeeded/failed/cancelled；恢复产生 attempt/checkpoint 记录。
- 不变量/失效/示例：succeeded 不等于 Verdict；input/workflow version 变化需新 Run；示例 run-001 page 1–5。

## yaml-workflow-registry

- contract_id: workflow-registry；contract_version: 0.1.0；lifecycle: active；YAML: [workflow-registry.yaml](../workflow-registry.yaml)
- 目的与范围：机器可读 Workflow 入口索引；上游是已登记 workflow/readme/acceptance。
- I/O：输入 registry entries；输出唯一 Workflow IDs 和入口路径。
- 状态/案例：路径和 ID 闭合；缺入口失败；恢复修登记，不复制阶段。
- 不变量/失效/示例：详细阶段只在 workflow.yaml；示例 document-to-markdown-assets → workflow path。

## yaml-skill-registry

- contract_id: skill-registry；contract_version: 0.1.0；lifecycle: active；YAML: [skill-registry.yaml](../skill-registry.yaml)
- 目的与范围：Skill ID、SKILL.md、兼容 alias/profile 的注册。
- I/O：输入 skill entries；输出唯一 ID 到文件映射。
- 状态/案例：orphan/duplicate/frontmatter mismatch 失败；恢复修注册或 Skill。
- 不变量/失效/示例：archive skill 不参与 runtime；示例 vision-text-transcription alias_of vision-page-analysis。

## yaml-tool-registry

- contract_id: tool-registry；contract_version: 0.1.0；lifecycle: active；YAML: [tool-registry.yaml](../tool-registry.yaml)
- 目的与范围：确定性程序、系统能力和模型适配器的实现状态与 Skill 使用关系。
- I/O：输入 tool entries；输出 planned/optional/available 和路径/contract。
- 状态/案例：未知 implementation 或 skill ref 失败；恢复修登记。
- 不变量/失效/示例：available 不等于 selected/executed/verified；示例 system-ocr available。

## workflow-document-to-markdown-assets

- contract_id: document-to-markdown-assets；contract_version: 0.10.0；lifecycle: active；YAML: [workflow.yaml](../workflows/document-to-markdown-assets/workflow.yaml)
- 目的与范围：现有 Workflow A A1–A7 执行投影；上游应为 confirmed V3/projection，但当前只作 Brownfield candidate。
- I/O：输入 one source/pages/output/authorization；输出 reader 或 engineering package、Run/Evidence。
- 状态/案例：阶段门通过后运行；任何 gate 失败停止；恢复按 checkpoint/allowed fallback。
- 不变量/失效/示例：不定义需求、不自动配对、不把 A7 自报当 Claim；需求/Spec version 变化使 projection stale。

## workflow-lan-pdf-markdown-simple

- contract_id: lan-pdf-markdown-simple；contract_version: 0.3.0；lifecycle: independent-unregistered；YAML: [workflow.yaml](../workflows/lan-pdf-markdown-simple/workflow.yaml)
- 目的与范围：显式 visual-only LAN PDF worker，不参与 registry 主路由。
- I/O：输入 one PDF/pages/run root；输出逐页 visual transcription candidate。
- 状态/案例：正常 explicit visual-only；未授权/不可用失败；恢复回 canonical route selection。
- 不变量/失效/示例：不替代 Workflow A、不自动注册；示例显式 smoke pages 1–3。

## workflow-markdown-to-gpt-image

- contract_id: markdown-to-gpt-image；contract_version: 0.1.0；lifecycle: active；YAML: [workflow.yaml](../workflows/markdown-to-gpt-image/workflow.yaml)
- 目的与范围：现有 Workflow B 把一个 Markdown 单元派生为一张图。
- I/O：输入 section/intent/optional references；输出 one raster + request/review records。
- 状态/案例：独立运行或可选 handoff；无授权/provider 失败；恢复新 run。
- 不变量/失效/示例：派生图不冒充来源图，不定义来源转换需求；source digest 变化使 run stale。
