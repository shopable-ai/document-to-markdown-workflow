---
title: "03 功能需求"
description: "将用户问题映射为业务能力、功能列表、功能卡和派生功能树。"
order: 40
---

# 文档资产化业务能力、功能列表与功能卡

| 字段 | 值 |
|---|---|
| document_id | FUNCTIONS |
| version | 0.4.0 |
| authority | business-function-list-and-cards |
| status | draft-awaiting-V2-confirmation |
| direct_source | 迁移前 TASK-TREE 第 5 节“我们真正要解决的十二件事” |

本文件只把原需求树的十二项用户问题转成业务功能。它不按现有 A1–A7、解析器、适配器或 Contract 重新分组。REQ-DOC-020 是“下游视觉派生不得冒充来源转换”的非目标边界，不因此增加当前项目功能。

## 业务能力

| capability_id | 名称 | 用户价值 | 原需求范围 | status |
|---|---|---|---|---|
| CAP-DOC-01 | 当前来源正确性 | 换文件后只讲当前文件，不串入旧内容 | 原需求一、四 | draft |
| CAP-DOC-02 | 内容与关系保真 | 文字、结构、视觉关系不过度丢失或改写 | 原需求二、三、七、八 | draft |
| CAP-DOC-03 | 可读且可携带的 Markdown | 正文可读，必要图片随包移动，工程噪声不污染读者层 | 原需求一、六、九 | draft |
| CAP-DOC-04 | 不确定性与结果边界 | 人能看出已确认、待核对和不能使用的内容 | 原需求三、五、七 | draft |
| CAP-DOC-05 | 受控处理与独立验收 | 长任务可停止恢复，页面逐步放大，结果独立验收 | 原需求十、十一 | draft |
| CAP-DOC-06 | 可选知识库清洗 | 完整汇合后再清理工作副本并重建索引 | 原需求十二 | draft-optional |

## 功能列表

| function_id | name | original_requirement_group | user_problem_solved | capability_ref | requirement_refs | status |
|---|---|---|---|---|---|---|
| FUN-DOC-001 | 把当前来源转换成独立 Markdown | 一 | 换入一个文件后，结果是否真正讲这个文件且能独立阅读 | CAP-DOC-01、CAP-DOC-03 | REQ-DOC-001、002、022 | draft |
| FUN-DOC-002 | 保留原文内容、层级和关系 | 二 | 文字、数字、表格、图表和关系是否被遗漏或改写 | CAP-DOC-02 | REQ-DOC-003、004、012 | draft |
| FUN-DOC-003 | 让复杂图文问题可见并可纠正 | 三 | 遮挡、错位、顺序和关系问题是否能被发现和复核 | CAP-DOC-02、CAP-DOC-04 | REQ-DOC-006、007、012、014 | draft |
| FUN-DOC-004 | 识别同一材料的不同格式并避免误合并 | 四 | PDF/PPTX 等候选是否被错误混成一个来源 | CAP-DOC-01 | REQ-DOC-001、008、012 | draft |
| FUN-DOC-005 | 如实表达结果的确认边界 | 五 | 人是否知道哪些已确认、待核对、失败或不能使用 | CAP-DOC-04 | REQ-DOC-007、012、018 | draft |
| FUN-DOC-006 | 分离读者标题与来源追溯 | 六 | 标题是否被文件名、页码和内部定位污染，同时还能回源 | CAP-DOC-03 | REQ-DOC-009、011、017 | draft |
| FUN-DOC-007 | 使用可验证的文字识别和视觉复核路线 | 七 | 识别资源是否真的可用且质量足够，而不是只证明连通 | CAP-DOC-02、CAP-DOC-04 | REQ-DOC-013、014、021 | draft |
| FUN-DOC-008 | 按来源格式提取必要图片并校准质量与成本 | 八 | PPT/PDF/图片中的视觉资产是否完整、可追溯并能先成功再降本 | CAP-DOC-02 | REQ-DOC-005、006、010、013、014、021 | draft |
| FUN-DOC-009 | 生成可携带、语义命名的读者图片资产 | 九 | Markdown 复制后图片是否仍可读，目录和文件名是否没有工程噪声 | CAP-DOC-03 | REQ-DOC-002、009、010、011、017 | draft |
| FUN-DOC-010 | 让长时间处理可观察、可停止、可恢复 | 十 | 人是否知道当前页、阶段、失败和下一步，并能停止或续接 | CAP-DOC-05 | REQ-DOC-015、017、018 | draft |
| FUN-DOC-011 | 先单页、再小批量、最后全量修复 | 十一 | 页面问题是否在放大前被发现，且原始证据不被覆盖 | CAP-DOC-05 | REQ-DOC-004、012、016、017 | draft |
| FUN-DOC-012 | 在完整汇合后清洗知识库工作副本 | 十二 | 低价值整页是否被提前误删，清洗后索引是否真实重建 | CAP-DOC-06 | REQ-DOC-019 | draft-optional |

## 功能卡

### FUN-DOC-001 把当前来源转换成独立 Markdown

- original_requirement_ref: TASK-TREE 第 5 节第一项
- user_problem_solved: 换一个来源后，Markdown 仍残留旧文档内容，或只有文件外壳而不能独立阅读。
- requirement_refs: REQ-DOC-001、REQ-DOC-002、REQ-DOC-022
- capability_ref: CAP-DOC-01、CAP-DOC-03
- roles: ROLE-READER、ROLE-OPERATOR
- triggers: 用户提供一个明确来源、页码和输出范围
- inputs: 当前来源和明确范围
- outputs: 只讲当前来源的独立 Markdown
- normal_behavior: 只消费当前来源可确认内容；结果无需依赖原文件才能阅读
- exception_behavior: 来源不清、不可读或内容被旧上下文污染时不输出 passed
- recovery_behavior: 回到来源确认，清除非当前来源上下文后重新形成候选
- dependencies: none
- non_goals: 自动寻找另一格式、摘要改写、知识库建模
- behavior_refs: BHV-DOC-001、BHV-DOC-003
- acceptance_refs: AC-DOC-001、AC-DOC-002、AC-DOC-022
- spec_refs: pending-V3
- status: draft

### FUN-DOC-002 保留原文内容、层级和关系

- original_requirement_ref: TASK-TREE 第 5 节第二项
- user_problem_solved: 转换只剩连续文字，原有数字、层级、表格、箭头、分组和关系丢失。
- requirement_refs: REQ-DOC-003、REQ-DOC-004、REQ-DOC-012
- capability_ref: CAP-DOC-02
- roles: ROLE-READER、ROLE-REVIEWER
- triggers: 当前来源含文字、结构或关系
- inputs: 当前来源可见内容和来源位置
- outputs: 忠实的 Markdown 内容、结构表达和 Unknown
- normal_behavior: 保留文字、数字、章节、分区、列表、表格及可确认关系
- exception_behavior: 关系依据不足时保留 Unknown，不为完整而猜写
- recovery_behavior: 回到当前页或区域证据，由人工复核
- dependencies: FUN-DOC-001
- non_goals: 总结、扩写、翻译或跨来源补全
- behavior_refs: BHV-DOC-003、BHV-DOC-004、BHV-DOC-005
- acceptance_refs: AC-DOC-003、AC-DOC-004、AC-DOC-012
- spec_refs: pending-V3
- status: draft

### FUN-DOC-003 让复杂图文问题可见并可纠正

- original_requirement_ref: TASK-TREE 第 5 节第三项
- user_problem_solved: 复杂页面出现遮挡、错位、遗漏或关系错误时，结果看似完整但无法发现问题。
- requirement_refs: REQ-DOC-006、REQ-DOC-007、REQ-DOC-012、REQ-DOC-014
- capability_ref: CAP-DOC-02、CAP-DOC-04
- roles: ROLE-REVIEWER、ROLE-READER
- triggers: 页面意义依赖布局、颜色、连线、图例或图形组合
- inputs: 当前页面/区域、文字与关系候选、授权范围
- outputs: 可搜索 Markdown、可核对视觉证据、问题和修复说明
- normal_behavior: 让问题、修复依据和仍未确认部分都可见
- exception_behavior: 无法确认或不能保存原图时明确记录缺失和授权限制
- recovery_behavior: 扩大证据范围、人工复核或保留 needs-review
- dependencies: FUN-DOC-002
- non_goals: 用修复图冒充原图，或用非空 OCR 证明视觉关系正确
- behavior_refs: BHV-DOC-005、BHV-DOC-006、BHV-DOC-007、BHV-DOC-011
- acceptance_refs: AC-DOC-006、AC-DOC-007、AC-DOC-012、AC-DOC-014
- spec_refs: pending-V3
- status: draft

### FUN-DOC-004 识别同一材料的不同格式并避免误合并

- original_requirement_ref: TASK-TREE 第 5 节第四项
- user_problem_solved: 相似 PDF/PPTX 被误当同一材料，或真实候选的差异被合并隐藏。
- requirement_refs: REQ-DOC-001、REQ-DOC-008、REQ-DOC-012
- capability_ref: CAP-DOC-01
- roles: ROLE-OPERATOR、ROLE-REVIEWER
- triggers: 本次输入出现多个可能相关的不同格式来源
- inputs: 候选来源、基础文件名、内容/页数/视觉差异、人工声明
- outputs: same-logical-document、separate-sources 或 needs-review，以及差异
- normal_behavior: 只有规则和人工确认支持时才作为候选比较；正文仍保留来源差异
- exception_behavior: 名称过短、泛化、不同或关系不清时分别处理
- recovery_behavior: 请求用户明确候选关系和主视觉依据
- dependencies: FUN-DOC-001
- non_goals: 仅凭后缀或相似名称自动合并
- behavior_refs: BHV-DOC-002
- acceptance_refs: AC-DOC-008
- spec_refs: pending-V3
- status: draft

### FUN-DOC-005 如实表达结果的确认边界

- original_requirement_ref: TASK-TREE 第 5 节第五项
- user_problem_solved: “转换完成”掩盖内容缺失、关系未决或结果不可使用。
- requirement_refs: REQ-DOC-007、REQ-DOC-012、REQ-DOC-018
- capability_ref: CAP-DOC-04
- roles: ROLE-READER、ROLE-REVIEWER、ROLE-VALIDATOR
- triggers: 内容、关系、来源或包的状态需要对人说明
- inputs: 已确认内容、Unknown、失败和 Evidence
- outputs: passed、needs-review 或 failed 的候选边界及理由
- normal_behavior: 区分已确认、待核对、不可用和失败
- exception_behavior: 缺 owner、影响或解决入口时不能声称完成
- recovery_behavior: 回到最早责任功能补证据或修正
- dependencies: cross-cutting
- non_goals: 让 runner 自报状态替代独立 Verdict
- behavior_refs: BHV-DOC-011、BHV-DOC-015
- acceptance_refs: AC-DOC-007、AC-DOC-012、AC-DOC-018
- spec_refs: pending-V3
- status: draft

### FUN-DOC-006 分离读者标题与来源追溯

- original_requirement_ref: TASK-TREE 第 5 节第六项
- user_problem_solved: 页面标题被源文件名、页码、哈希和对象 ID 污染，读者层难以阅读。
- requirement_refs: REQ-DOC-009、REQ-DOC-011、REQ-DOC-017
- capability_ref: CAP-DOC-03
- roles: ROLE-READER、ROLE-REVIEWER
- triggers: 为页面或内容单元生成标题并登记来源
- inputs: 可确认语义标题、来源文件名、页码、hash、occurrence
- outputs: 纯语义标题和独立追溯记录
- normal_behavior: 有语义标题时只表达主题；来源信息进入追溯层
- exception_behavior: 无可确认标题时使用临时页码标题并 needs-review
- recovery_behavior: 人工确认标题后更新正文，保留来源记录
- dependencies: FUN-DOC-002
- non_goals: 为了追溯把工程字段前置到每个标题
- behavior_refs: BHV-DOC-009、BHV-DOC-010
- acceptance_refs: AC-DOC-009、AC-DOC-011、AC-DOC-017
- spec_refs: pending-V3
- status: draft

### FUN-DOC-007 使用可验证的文字识别和视觉复核路线

- original_requirement_ref: TASK-TREE 第 5 节第七项
- user_problem_solved: 模型或 OCR 只证明可连接，却被误称为已经正确识别文字和结构。
- requirement_refs: REQ-DOC-013、REQ-DOC-014、REQ-DOC-021
- capability_ref: CAP-DOC-02、CAP-DOC-04
- roles: ROLE-OPERATOR、ROLE-REVIEWER
- triggers: 原生读取不足且需要 OCR 或视觉复核
- inputs: 同一真实页面、同一 hash、schema、质量维度和授权
- outputs: 可比较候选、selected route、unavailable 或 needs-review
- normal_behavior: 记录输入输出、结构质量、耗时、资源和失败
- exception_behavior: 未授权、传输失败或质量不足时不升级为默认路线
- recovery_behavior: 修正输入/schema/提示边界或选择授权 fallback
- dependencies: FUN-DOC-001、FUN-DOC-002
- non_goals: 按参数量、模型列表或 HTTP 200 选择路线
- behavior_refs: BHV-DOC-013
- acceptance_refs: AC-DOC-013、AC-DOC-014、AC-DOC-021
- spec_refs: pending-V3
- status: draft

### FUN-DOC-008 按来源格式提取必要图片并校准质量与成本

- original_requirement_ref: TASK-TREE 第 5 节第八项
- user_problem_solved: PPT/PDF/图片中的视觉内容被单一路线漏掉，或还没成功就只追求低成本。
- requirement_refs: REQ-DOC-005、REQ-DOC-006、REQ-DOC-010、REQ-DOC-013、REQ-DOC-014、REQ-DOC-021
- capability_ref: CAP-DOC-02
- roles: ROLE-OPERATOR、ROLE-REVIEWER
- triggers: 当前来源含需要保留的嵌入图、整页图、矢量图或区域图
- inputs: 当前来源、媒体类型、授权、代表资产和质量基准
- outputs: 可追溯资产、父子派生关系和路线比较
- normal_behavior: 先用合适方式完整取得资产，再在相同输入下比较本地和授权路线
- exception_behavior: 资产不完整、裁切不足或质量未过门时 needs-review/failed
- recovery_behavior: 更换提取方式、扩大证据或重做校准
- dependencies: FUN-DOC-001、FUN-DOC-003、FUN-DOC-007
- non_goals: 把“提取图片”和“理解图片”混成一步
- behavior_refs: BHV-DOC-006、BHV-DOC-007、BHV-DOC-013
- acceptance_refs: AC-DOC-005、AC-DOC-006、AC-DOC-010、AC-DOC-013、AC-DOC-014、AC-DOC-021
- spec_refs: pending-V3
- status: draft

#### FUN-DOC-008 的来源格式差异

- PPT/PPTX：分别考虑无损嵌入图、整页渲染、图表、组合形状、矢量图和复杂区域；不能用单一截图覆盖全部候选。
- PDF：分别考虑嵌入图、原始页面渲染、矢量图、扫描页、复杂表格和图文混排区域。
- 独立图片：原图本身是来源，默认不先转码；裁切必须保留 parent 和来源位置。
- “取得图片资产”和“理解图片内容”是两件事；前者优先确定性，后者才进入受授权识别/视觉候选。
- 先用获授权的高质量路线建立相同输入基准，再比较本地路线；本地路线只有在质量、重复性、成本和追溯都过门后才可成为默认低成本候选。

### FUN-DOC-009 生成可携带、语义命名的读者图片资产

- original_requirement_ref: TASK-TREE 第 5 节第九项
- user_problem_solved: Markdown 复制后图片断链，或文件名和目录充满页码、hash、内部类型等工程噪声。
- requirement_refs: REQ-DOC-002、REQ-DOC-009、REQ-DOC-010、REQ-DOC-011、REQ-DOC-017
- capability_ref: CAP-DOC-03
- roles: ROLE-READER、ROLE-REVIEWER
- triggers: 最终 Markdown 实际引用必要视觉资产
- inputs: 被引用资产、语义主题、来源 occurrence 和 hash
- outputs: 扁平 reader assets、短语义名、相对链接和独立追溯
- normal_behavior: 只复制实际引用资产；真实同名冲突追加最少序号
- exception_behavior: 断链、未引用图或命名冲突未解决时不通过
- recovery_behavior: 重命名、复制并同步正文与追溯
- dependencies: FUN-DOC-003、FUN-DOC-006、FUN-DOC-008
- non_goals: 批量复制所有页面渲染或删除工程层原始证据
- behavior_refs: BHV-DOC-007、BHV-DOC-010
- acceptance_refs: AC-DOC-002、AC-DOC-009、AC-DOC-010、AC-DOC-011、AC-DOC-017
- spec_refs: pending-V3
- status: draft

### FUN-DOC-010 让长时间处理可观察、可停止、可恢复

- original_requirement_ref: TASK-TREE 第 5 节第十项
- user_problem_solved: 数小时后只得到总结果，期间不知道当前页、失败、遗漏或能否停止恢复。
- requirement_refs: REQ-DOC-015、REQ-DOC-017、REQ-DOC-018
- capability_ref: CAP-DOC-05
- roles: ROLE-OPERATOR、ROLE-VALIDATOR
- triggers: 长文档、多页或预计超过短交互
- inputs: 选定范围、逐页状态、阶段、Evidence 和 checkpoint
- outputs: 当前进度、停止/取消结果、可恢复 checkpoint 和遗漏差集
- normal_behavior: 每个已选页明确完成、失败、未开始或跳过
- exception_behavior: 超时、无进展、取消或身份不匹配时停止并保留证据
- recovery_behavior: 从已验证 checkpoint 续接，不覆盖旧失败
- dependencies: FUN-DOC-001、FUN-DOC-005
- non_goals: 用总页数、队列提交或周期“仍运行”消息代替 Evidence
- behavior_refs: BHV-DOC-014
- acceptance_refs: AC-DOC-015、AC-DOC-017、AC-DOC-018
- spec_refs: pending-V3
- status: draft

### FUN-DOC-011 先单页、再小批量、最后全量修复

- original_requirement_ref: TASK-TREE 第 5 节第十一项
- user_problem_solved: 单页问题未解决就扩到整本，错误被批量放大且原始证据被修复层覆盖。
- requirement_refs: REQ-DOC-004、REQ-DOC-012、REQ-DOC-016、REQ-DOC-017
- capability_ref: CAP-DOC-05
- roles: ROLE-OPERATOR、ROLE-REVIEWER
- triggers: 读者层页面 Markdown 需要修复
- inputs: 当前页工作单元、当前页图和只读原始 Evidence
- outputs: 第 1 页、小批量和全量各自的页面结果与人工门记录
- normal_behavior: 前一阶段通过才开放下一阶段，合并保持固定页序
- exception_behavior: 守卫失败、人工未确认或 raw 变化时停止
- recovery_behavior: 只回到当前页派生单元，保留原始 OCR/Vision/manifest
- dependencies: FUN-DOC-002、FUN-DOC-003、FUN-DOC-005
- non_goals: 为修一页加载全书正文，或让 document-temp 成为事实源
- behavior_refs: BHV-DOC-004、BHV-DOC-011、BHV-DOC-012
- acceptance_refs: AC-DOC-004、AC-DOC-012、AC-DOC-016、AC-DOC-017
- spec_refs: pending-V3
- status: draft

### FUN-DOC-012 在完整汇合后清洗知识库工作副本

- original_requirement_ref: TASK-TREE 第 5 节第十二项
- user_problem_solved: 生产者尚未完成时提前删页掩盖遗漏，或删页后仍复用包含旧内容的索引。
- requirement_refs: REQ-DOC-019
- capability_ref: CAP-DOC-06
- roles: ROLE-DOWNSTREAM、ROLE-OPERATOR
- triggers: 用户明确用于知识库，完整 page-units 已汇合
- inputs: 完整工作副本、页数对账、逐页理由和 hash-lock 预览
- outputs: 清洗副本、删除 ledger、从保留页重建的索引/全文
- normal_behavior: 只删除计划内整页，不改保留页和原始证据
- exception_behavior: 页数或 hash 变化时本次删除数量为零
- recovery_behavior: 重新汇合并生成新计划
- dependencies: FUN-DOC-005、FUN-DOC-010、FUN-DOC-011
- non_goals: 提升原转换 Verdict 或按固定前 N 页跨书删除
- behavior_refs: BHV-DOC-016
- acceptance_refs: AC-DOC-019
- spec_refs: pending-V3
- status: draft-optional

#### FUN-DOC-012 的候选页边界

- 可候选删除：纯目录、版权/出版说明、推广/领取/联系方式、出版社书目推荐、重复封面、空白装饰页。
- 默认复核：前言、导读、致谢、术语表、附录、参考文献、主题索引。
- 默认保留：正文、章节内容、定义、案例、公式、表格和图示关系。
- 这些是内容类型候选，不是“每本书前 N 页”的固定规则。
- 删除后必须从保留 page-units 重建 index、total 或集合文件；旧合并文件仍含被删页时不得称为已清洗。

## 功能树

功能树是派生视图：

- CAP-DOC-01 → FUN-DOC-001、FUN-DOC-004
- CAP-DOC-02 → FUN-DOC-002、FUN-DOC-003、FUN-DOC-007、FUN-DOC-008
- CAP-DOC-03 → FUN-DOC-001、FUN-DOC-006、FUN-DOC-009
- CAP-DOC-04 → FUN-DOC-003、FUN-DOC-005、FUN-DOC-007
- CAP-DOC-05 → FUN-DOC-010、FUN-DOC-011
- CAP-DOC-06 → FUN-DOC-012

功能树不得产生列表中不存在的 FUN ID。功能列表和功能卡才是字段级权威。
