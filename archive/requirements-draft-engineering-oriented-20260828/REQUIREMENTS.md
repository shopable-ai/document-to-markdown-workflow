---
title: "02 需求说明"
description: "集中定义稳定需求 ID、来源、状态、验收方向、责任人和 Unknown 台账。"
order: 30
---

# 文档资产化项目稳定需求列表

| 字段 | 值 |
|---|---|
| document_id | REQUIREMENTS |
| version | 0.3.0 |
| authority | business-requirement-list |
| status | draft-awaiting-V1-reconfirmation |

下表由迁移前的人类目标稿和需求树迁移，不从当前实现反推。draft 表示获得稳定 ID 便于人工确认，不表示已经批准。

| requirement_id | type | statement | source_refs | status | priority | unknown_ref | confirmation_owner | version | acceptance_direction | change_impact |
|---|---|---|---|---|---|---|---|---|---|---|
| REQ-DOC-001 | source | 每次处理必须绑定当前明确来源和范围，历史材料不得污染结果 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-001 | source-selection-to-claim |
| REQ-DOC-002 | deliverable | 直接交付必须是可脱离原文件阅读和搜索的 Markdown | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-002 | output-layout |
| REQ-DOC-003 | fidelity | 文字、数字、章节、分区、列表、表格和可确认关系必须尽量保留 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-003 | extraction-assembly |
| REQ-DOC-004 | transformation | 轻度整理只能改善可读性，不能改写语义、范围、条件、语气或结论 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-004 | repair-assembly |
| REQ-DOC-005 | visual | 文字和结构可完整表达且无待核对项的简单内容不默认保留读者截图 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | should | UNK-DOC-002 | ROLE-OWNER | 1 | AC-DOC-005 | asset-selection |
| REQ-DOC-006 | visual | 复杂视觉内容必须同时保留可搜索 Markdown 和可核对视觉证据 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-002 | ROLE-OWNER | 1 | AC-DOC-006 | asset-selection |
| REQ-DOC-007 | reviewability | 遗漏、错位、遮挡、顺序或关系问题必须可被发现、复核和修正 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-002 | ROLE-OWNER | 1 | AC-DOC-007 | visual-review-recovery |
| REQ-DOC-008 | source-selection | 多格式文件不得仅凭相似名称自动合并；候选关系和差异必须显式 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-003 | ROLE-OWNER | 1 | AC-DOC-008 | candidate-selection |
| REQ-DOC-009 | provenance | 读者语义标题必须与文件名、页码、哈希和内部定位分层 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-009 | reader-provenance |
| REQ-DOC-010 | asset | 最终资产只保留正文实际引用且有语义或复核价值的图片 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-002 | ROLE-OWNER | 1 | AC-DOC-010 | asset-package |
| REQ-DOC-011 | naming | 读者图片使用简短稳定语义名；页码、哈希和资产类型留在追溯层 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | should | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-011 | naming-manifest |
| REQ-DOC-012 | uncertainty | 无法确认的文字、数字、关系、裁切或来源必须保留 Unknown 或 needs-review | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-012 | all-content-and-claims |
| REQ-DOC-013 | model-governance | 识别和视觉复核路线必须有授权、同输入校准和当前证据，连通性不能代替质量 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-004 | ROLE-OWNER | 1 | AC-DOC-013 | model-route |
| REQ-DOC-014 | privacy-permission | 原图保存、外发、在线模型和下游使用必须遵守当前授权、隐私和保留范围 | SRC-LEGACY-GOAL | draft | must | UNK-DOC-004 | ROLE-OWNER | 1 | AC-DOC-014 | assets-models-retention |
| REQ-DOC-015 | operation | 长任务必须可观察、可停止、可恢复，并逐页显示完成、失败、未开始或跳过 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-005 | ROLE-OWNER | 1 | AC-DOC-015 | run-control |
| REQ-DOC-016 | staged-repair | 页面修复必须先第 1 页，再第 2–5 页，人工复核后才允许全量 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-006 | ROLE-OWNER | 1 | AC-DOC-016 | page-repair |
| REQ-DOC-017 | evidence | 原始 OCR、bbox、置信度、Vision JSON、页面图和原始 manifest 必须只读保留 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-017 | raw-evidence |
| REQ-DOC-018 | acceptance | 交付必须区分 passed、needs-review 和 failed，并由独立验收者重读当前包 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-VALIDATOR | 1 | AC-DOC-018 | evidence-verdict-claim |
| REQ-DOC-019 | optional-downstream | 知识库低价值整页清洗只能在完整页集合汇合后对哈希锁定工作副本执行 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft-optional | should | UNK-DOC-007 | ROLE-OWNER | 1 | AC-DOC-019 | downstream-kb |
| REQ-DOC-020 | downstream-boundary | 翻译、风格改写、重绘和图片生成是独立派生处理，不得冒充来源转换 | SRC-LEGACY-GOAL | draft-optional | must | UNK-DOC-007 | ROLE-OWNER | 1 | AC-DOC-020 | downstream-visual |
| REQ-DOC-021 | quality-risk | 质量、重复性、耗时、资源和成本必须在相同输入及质量维度下比较 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | should | UNK-DOC-004 | ROLE-OWNER | 1 | AC-DOC-021 | route-selection |
| REQ-DOC-022 | anti-contamination | fixture、旧示例和历史结果只能用于测试，不得成为当前内容的隐形答案 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | draft | must | UNK-DOC-001 | ROLE-OWNER | 1 | AC-DOC-022 | prompts-context-output |

## 与原需求树十二项的直接映射

| 原需求组 | 原始用户问题 | 新需求 ID | 对应功能 |
|---|---|---|---|
| 一 | 当前文件必须能转换成 Markdown | REQ-DOC-001、002、022 | FUN-DOC-001 |
| 二 | Markdown 必须尽量保留原文内容和关系 | REQ-DOC-003、004、012 | FUN-DOC-002 |
| 三 | 复杂图文问题必须能够被看见和纠正 | REQ-DOC-006、007、012、014 | FUN-DOC-003 |
| 四 | 不同来源不能被无意混成一份材料 | REQ-DOC-001、008、012 | FUN-DOC-004 |
| 五 | 人必须看得懂结果边界 | REQ-DOC-007、012、018 | FUN-DOC-005 |
| 六 | 读者层不能被来源元数据污染 | REQ-DOC-009、011、017 | FUN-DOC-006 |
| 七 | 文字识别和视觉复核要使用可验证候选资源 | REQ-DOC-013、014、021 | FUN-DOC-007 |
| 八 | 图片资产要覆盖多种提取方案并先成功再降本 | REQ-DOC-005、006、010、013、014、021 | FUN-DOC-008 |
| 九 | 读者层图片复制必须去除工程噪声 | REQ-DOC-002、009、010、011、017 | FUN-DOC-009 |
| 十 | 过程必须可观察、可停止、可恢复 | REQ-DOC-015、017、018 | FUN-DOC-010 |
| 十一 | 页面修复必须先单页、再小批量、最后扩大 | REQ-DOC-004、012、016、017 | FUN-DOC-011 |
| 十二 | 知识库低价值页清洗必须晚于完整汇合 | REQ-DOC-019 | FUN-DOC-012 |

REQ-DOC-020 是当前 Workflow 之外的派生边界，不新增当前项目功能。

## Unknown 台账

| unknown_id | 未决问题 | owner | 影响 | 解决入口 | 状态 |
|---|---|---|---|---|---|
| UNK-DOC-001 | 迁移后的 22 条业务需求是否仍完整代表当前用户意图 | ROLE-OWNER | 阻断 V1、V2 和正式工程扩张 | V1 逐条人工确认 | open-blocking |
| UNK-DOC-002 | 简单内容、复杂视觉和最小充分裁切的业务判定边界 | ROLE-OWNER | 影响资产保留和验收 | SCN-DOC-003 与 AC-DOC-005..010 复核 | open |
| UNK-DOC-003 | 多格式候选关系的默认规则与人工授权形式 | ROLE-OWNER | 影响来源选择和差异保留 | SCN-DOC-002 复核 | open |
| UNK-DOC-004 | 当前允许的局域网、在线模型、原图外发和保留策略 | ROLE-OWNER | 阻断真实模型和外部调用 | 每次 projection/authorization record | runtime-open |
| UNK-DOC-005 | 长任务的人工可见进度和恢复粒度 | ROLE-OWNER | 影响运行控制和交付时间 | SCN-DOC-005 复核 | open |
| UNK-DOC-006 | 严格页面阶段门适用哪些文档类型和修复模式 | ROLE-OWNER | 影响页面 Workflow 投影 | SCN-DOC-004 与 BHV-DOC-012 复核 | open |
| UNK-DOC-007 | 知识库清洗和视觉派生是否属于长期产品范围 | ROLE-OWNER | 影响可选功能和下游边界 | V1 范围确认 | open-nonblocking |
| UNK-DOC-008 | 未来除 PPT/PPTX/PDF/图片外支持哪些来源类型 | ROLE-OWNER | 影响来源能力和测试空间 | 新需求变更流程 | open-nonblocking |

修改本文件后必须重新生成 requirements.index.yaml，并按 [09 需求内部追溯](TRACEABILITY.md) 计算下游影响。
