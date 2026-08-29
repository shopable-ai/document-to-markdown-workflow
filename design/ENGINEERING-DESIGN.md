# 文档资产化候选工程设计

状态：candidate-blocked-by-V3。本文件不宣称现有实现符合尚未确认的需求。

## 数据流

Source request → source identity/scope → immutable raw capture → content/relation candidates → visual retention decision → reader Markdown/asset links → provenance closure → independent validation。

可选下游只消费已验收输出，产生新的派生身份和 Verdict。

## I/O 和 Data

| boundary | input | output | 不变量 |
|---|---|---|---|
| Source Boundary | source、pages、candidate declaration | source fingerprint、selected scope | 不从文件名自动合并来源 |
| Content Capture | current source bytes/page | text/structure candidates | raw 不可被整理层覆盖 |
| Relation Review | current page evidence | confirmed/candidate/unknown relations | observed order 不自动变 causal |
| Visual Asset | page/region、permission | retain/crop/omit/needs-review | complex visual 不因 OCR 非空自动 omit |
| Reader Assembly | content/relations/assets | index/page Markdown | 只输出实际存在内容 |
| Provenance | artifact and source occurrence | stable refs and hashes | 语义名与追溯字段分层 |
| Validation | current package、criteria、source refs | passed/needs-review/failed | generator self-report 不替代 Verdict |

## State

- source: identified、accepted、needs-review、invalid、unsupported；
- page: not-started、extracting、assembled、review-pending、passed、needs-review、failed；
- asset: candidate、retain、crop-required、omit、needs-review；
- run: planned、running、succeeded、needs-review、failed、cancelled；
- evidence: collecting、complete、stale、invalidated；
- verdict: pending、passed、needs-review、failed；
- claim: blocked、bounded、invalidated。

不同 namespace 的同名状态不能互相赋值。

## Permission 与 Privacy

- source read、原图保留、局域网模型、在线模型、外发和下游使用分别授权；
- secret 不写入仓库、prompt、manifest 或报告；
- 未授权路线为 blocked/unavailable，不静默 fallback 到外部服务；
- reader asset 的保留和发布范围不得超过来源授权；
- 权限变化使受影响的 projection、Run、Evidence 和 Claim 失效。

## Error 与恢复

| error_class | 示例 | 最早责任对象 | 恢复 |
|---|---|---|---|
| source-boundary | 来源不明、候选误配 | FUN-DOC-001/002 | 重新确认来源 |
| extraction | 遮挡、低清、文字冲突 | FUN-DOC-003/010 | 受控复核或 needs-review |
| relation | 箭头、层级、颜色意义不明 | FUN-DOC-004/009 | 保留 Unknown |
| asset | 裁切不足、未授权保存 | FUN-DOC-005/006 | 扩裁、整页或 structured-only |
| assembly | 标题污染、断链、扩写 | FUN-DOC-007/008 | 从候选重组装 |
| run-control | 超时、遗漏、取消 | FUN-DOC-011 | checkpoint 恢复 |
| validation | 来源不符、Evidence 陈旧 | FUN-DOC-012 | 重建包和新 Evidence |

## Quality Realization

| 质量需求 | 候选实现机制 | 验证入口 |
|---|---|---|
| fidelity | immutable raw + conservative reconciliation | AC-DOC-003/004/017 |
| readability | semantic titles、adaptive Markdown、reader-only assets | AC-DOC-002/009/011 |
| visual completeness | simple/complex gate + minimal sufficient evidence | AC-DOC-005..007/010 |
| uncertainty safety | typed Unknown and fail-closed status | AC-DOC-012/018 |
| recoverability | page state、checkpoint、stage gates | AC-DOC-015..017 |
| permission/privacy | explicit projection authorization and no implicit external fallback | AC-DOC-014 |
| cost/performance | same-input route benchmark | AC-DOC-021 |
| anti-contamination | source fingerprint and context isolation | AC-DOC-001/022 |

