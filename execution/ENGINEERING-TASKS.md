# 工程任务列表

状态：candidate-blocked-by-V3。任务只为展示需求到工程投影形态；V3 通过前不得作为正式实现授权。

| task_id | task | requirement_refs | function_refs | candidate_spec_refs | output | acceptance_refs | status |
|---|---|---|---|---|---|---|---|
| ET0-01 | 固定来源身份、范围、授权和运行记录 | REQ-DOC-001、008、014、015、022 | FUN-DOC-001、002、011 | source inventory、run record | source/scope/projection record | AC-DOC-001、008、014、015、022 | blocked-before-V3 |
| ET0-02 | 固定逐页状态、停止、取消和 checkpoint | REQ-DOC-015..017 | FUN-DOC-011 | long-task、page-unit | progress/checkpoint | AC-DOC-015..017 | blocked-before-V3 |
| ET1-01 | 完成单页内容和结构保真纵向切片 | REQ-DOC-003、004、007、012、022 | FUN-DOC-003、004、009 | native/reconciliation | current-page candidates | AC-DOC-003、004、007、012、022 | blocked-before-V3 |
| ET1-02 | 完成简单/复杂视觉判定和必要资产闭环 | REQ-DOC-005、006、010、011、014 | FUN-DOC-005、006、008 | asset contracts | referenced reader assets | AC-DOC-005、006、010、011、014 | blocked-before-V3 |
| ET1-03 | 完成读者 Markdown、语义标题和来源分层 | REQ-DOC-002..004、009、012 | FUN-DOC-007、008、009 | page unit/output layout | reader Markdown | AC-DOC-002..004、009、012 | blocked-before-V3 |
| ET1-04 | 完成相同输入的识别路线校准 | REQ-DOC-013、014、021 | FUN-DOC-010 | benchmark/quality assessment | route decision evidence | AC-DOC-013、014、021 | blocked-before-V3 |
| ET1-05 | 独立验证单页或单包纵向切片 | REQ-DOC-018 | FUN-DOC-012 | acceptance scorecard | independent Verdict | AC-DOC-018 | blocked-before-V3 |
| ET2-01 | 第 1 页通过后扩展第 2–5 页 | REQ-DOC-016、017 | FUN-DOC-011 | page-unit gates | small-batch evidence | AC-DOC-016、017 | blocked-before-V3 |
| ET2-02 | 小批量人工通过后扩展全量并确定性合并 | REQ-DOC-015..018 | FUN-DOC-007、011、012 | page-unit/run/output | full document candidate | AC-DOC-015..018 | blocked-before-V3 |
| ET3-01 | 多来源分别建包和逐包验收 | REQ-DOC-001、008、018、022 | FUN-DOC-001、002、012 | batch/run records | per-source packages | AC-DOC-001、008、018、022 | blocked-before-V3 |
| ET4-01 | 建立输入、内容、视觉、权限、失败和恢复矩阵 | REQ-DOC-001..022 | FUN-DOC-001..014 | verification design | test matrix | AC-DOC-001..022 | blocked-before-V3 |
| ET5-01 | 后置知识库清洗的 hash-lock、ledger 和重建 | REQ-DOC-019 | FUN-DOC-013 | ebook-page-pruning | cleaned working copy | AC-DOC-019 | blocked-before-V3 |
| ET5-02 | 下游视觉派生的独立身份、授权和验收 | REQ-DOC-020 | FUN-DOC-014 | handoff/Workflow B | derived output | AC-DOC-020 | blocked-before-V3 |

范围符号只聚合已定义连续 ID，不生成任务来源。正式任务还必须绑定 confirmed V3、具体 Spec 版本和 owner。

