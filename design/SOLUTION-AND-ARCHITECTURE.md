# 文档资产化候选方案与架构

| 字段 | 值 |
|---|---|
| document_id | SOLUTION-AND-ARCHITECTURE |
| version | 0.3.0 |
| status | Brownfield-candidate-blocked-by-V2 |
| input | requirements version 0.3.0 draft |

## 边界

系统接收一个已确认来源和范围，输出独立 reader Markdown 包以及必要的工程追溯和验收记录。知识库清洗、翻译、重绘和图片生成是可选下游，不属于默认来源转换边界。

## 候选方案选择

| decision_id | 问题 | 采用的候选 | 替代方案 | 重新开启条件 |
|---|---|---|---|---|
| DEC-001 | 来源如何隔离 | 一个 source 对应一个独立 package | 自动合并同名多格式 | V2 明确新的候选合并规则 |
| DEC-002 | 复杂视觉如何交付 | Markdown + 必要视觉证据双表达 | 纯 OCR 或纯截图 | 用户确认另一验收模式 |
| DEC-003 | reader 与工程证据如何分层 | reader 语义层 + provenance/evidence 工程层 | 所有元数据写入正文 | REQ-DOC-009 被修改 |
| DEC-004 | 不确定性如何处理 | Unknown/needs-review/failed fail closed | AI 自动补全 | 合法需求和可验证机制改变 |
| DEC-005 | 长任务如何放大 | 单页/smoke → 小批量 → 全量 | 直接全量 | V2/V3 重新定义风险门 |
| DEC-006 | 模型如何选 | 确定性优先；授权后同输入校准模型路线 | 固定模型主次 | 新来源类别或质量证据变化 |

这些只是由现有需求草稿和 Brownfield 资产形成的候选，不是已批准 ADR。

## 候选组件

| component_id | 责任 | consumes | produces | requirement_refs |
|---|---|---|---|---|
| CMP-01 Source Boundary | 识别当前来源、页码和候选关系 | source request | source identity and scope | REQ-DOC-001、008、022 |
| CMP-02 Content Capture | 获取文字、数字、层级和结构候选 | current source | content candidates | REQ-DOC-003、004、012 |
| CMP-03 Relation Review | 判断节点、边、分组和视觉关系 | page evidence | confirmed relations and Unknown | REQ-DOC-003、007、012 |
| CMP-04 Visual Asset | 判定、提取、裁切和保留必要视觉 | page/region and permission | reader visual assets | REQ-DOC-005..007、010、011、014 |
| CMP-05 Reader Assembly | 组装正文、标题和相对链接 | content, relations, assets | reader Markdown | REQ-DOC-002..004、009、012 |
| CMP-06 Provenance | 保存来源、哈希、occurrence 和派生链 | all current artifacts | manifest/evidence refs | REQ-DOC-009..011、017 |
| CMP-07 Run Control | 阶段门、进度、停止、取消和恢复 | selected scope and gates | run/checkpoint records | REQ-DOC-015..017 |
| CMP-08 Independent Validation | 重读当前包并签发三态 Verdict | package and criteria | verdict and bounded delivery | REQ-DOC-018 |
| CMP-09 Optional Downstream | 知识库清洗或视觉派生 | accepted output + explicit request | separate derived result | REQ-DOC-019、020 |

## 方案边界

- 当前代码、Workflow 和 Contract 只用于判断这些候选组件“已有、部分或缺失”，不能确认需求。
- V2 通过前不冻结方案；V3 通过前不生成正式 Spec 或任务。
- Permission、privacy、cost、quality 和 recovery 首先来自需求，再由工程设计说明如何实现。

