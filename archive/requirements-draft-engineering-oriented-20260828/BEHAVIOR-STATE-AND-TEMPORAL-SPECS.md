---
title: "07 行为、状态与先后关系"
description: "定义可观察行为、异常恢复、状态模型、关系类型和页面阶段门。"
order: 80
---

# 文档资产化行为、状态与时序规格

状态：authoritative-draft。行为描述用户或复核者能观察到什么，不规定使用哪个脚本、模型或 Workflow 阶段。

## 行为规格

| behavior_id | trigger | preconditions | normal | exception | recovery | function_refs | requirement_refs | status |
|---|---|---|---|---|---|---|---|---|
| BHV-DOC-001 | 用户提供来源 | 路径和范围可读 | 绑定当前 source 和页码，不加载旧材料作答案 | 来源缺失、不支持或范围冲突 | blocked 并回到来源确认 | FUN-DOC-001 | REQ-DOC-001、022 | draft |
| BHV-DOC-002 | 出现多个候选来源 | 候选均在本次授权输入内 | 保留差异并要求确认 same 或 separate | 名称泛化或内容关系不明 | 分别处理或 needs-review | FUN-DOC-004 | REQ-DOC-008、012 | draft |
| BHV-DOC-003 | 当前来源可读取 | 来源已绑定 | 提取文字、数字、层级和结构，保留来源位置 | 遮挡、低清或解析冲突 | 进入受控复核，不猜写 | FUN-DOC-001、FUN-DOC-002 | REQ-DOC-003、012、022 | draft |
| BHV-DOC-004 | 需要轻度整理 | 忠实内容候选已建立 | 只调整标点、段落、标题层级、列表或表格表达 | 整理会改变语义、范围或结论 | 保留原表达并标记问题 | FUN-DOC-002、FUN-DOC-011 | REQ-DOC-004 | draft |
| BHV-DOC-005 | 页面含结构关系 | 当前页证据可读 | 只表达证据支持的节点、边和分组 | 先后、邻近或颜色不足以证明关系 | 保留 Unknown | FUN-DOC-002、FUN-DOC-003 | REQ-DOC-003、007、012 | draft |
| BHV-DOC-006 | 判断是否保留截图 | 文字覆盖和关系候选已知 | 全部简单条件满足才选择 markdown-only | 任一内容或关系待核对 | retain-visual 或 needs-review | FUN-DOC-003、FUN-DOC-008 | REQ-DOC-005、006、012 | draft |
| BHV-DOC-007 | 需要视觉证据 | 授权范围明确 | 保存最小充分区域或必要整页，不裁掉语义上下文 | 原图不可保存或裁切不充分 | structured-only 或 needs-review | FUN-DOC-003、FUN-DOC-008、FUN-DOC-009 | REQ-DOC-006、007、010、014 | draft |
| BHV-DOC-008 | 组装正文 | 内容、关系、资产和状态齐备 | 只输出当前内容实际存在的标题、正文、列表、表格和关系说明 | 无语义标题或关系未决 | 临时标题或 Unknown，不填空壳 | FUN-DOC-001、FUN-DOC-002 | REQ-DOC-002..004、009、012 | draft |
| BHV-DOC-009 | 生成页面标题 | 有可确认语义或可解释缺失 | 语义标题不带文件名、页码、哈希和对象 ID 前缀 | 无可确认标题 | 使用临时页码标题并 needs-review | FUN-DOC-006 | REQ-DOC-009、012 | draft |
| BHV-DOC-010 | 保存读者资产 | 资产被正文实际引用 | 使用短语义名和相对链接，追溯字段写入工程层 | 真同名冲突 | 追加最少序号并保持 manifest 关联 | FUN-DOC-006、FUN-DOC-009 | REQ-DOC-009..011、017 | draft |
| BHV-DOC-011 | 发现无法确认项 | 有候选、冲突或失败证据 | 登记 Unknown/needs-review/failed、owner、影响和入口 | 状态被隐藏或改成确定答案 | fail closed 并回到最早责任功能 | FUN-DOC-003、FUN-DOC-005、FUN-DOC-011 | REQ-DOC-007、012、018 | draft |
| BHV-DOC-012 | 启动页面修复 | 当前页工作单元和只读证据可用 | 先第 1 页，再 2–5 页，人工复核后再全量 | 前一门失败或未复核 | 停止后续页并保留当前证据 | FUN-DOC-011 | REQ-DOC-016、017 | draft |
| BHV-DOC-013 | 需要模型复核 | 本次路线获授权且有相同输入基准 | 比较文字、结构、关系、重复性、耗时、资源和成本 | 未授权、传输失败或质量不足 | unavailable、fallback 或 needs-review | FUN-DOC-007、FUN-DOC-008 | REQ-DOC-013、014、021 | draft |
| BHV-DOC-014 | 长任务运行 | 已选页、停止条件和 checkpoint 明确 | 逐页登记阶段、状态、Evidence 和下一步 | 超时、无进展、取消或遗漏 | 停止并从验证 checkpoint 恢复 | FUN-DOC-010 | REQ-DOC-015、017 | draft |
| BHV-DOC-015 | 候选包完成 | 来源指纹、包和事前判据齐备 | 独立 validator 重读并输出三态 Verdict | 生成者自检或 Evidence 陈旧 | Verdict blocked，回到最早失败功能 | FUN-DOC-005 | REQ-DOC-018 | draft |
| BHV-DOC-016 | 请求知识库清洗 | 完整页集合汇合且计划哈希未变 | 只删除计划内整页并重建索引 | 页数或哈希变化 | 删除数量为零并重建计划 | FUN-DOC-012 | REQ-DOC-019 | draft-optional |
| BHV-DOC-017 | 请求下游视觉派生 | 来源 Markdown 已验收且授权明确 | 生成独立标记的派生产物并保留来源引用 | 来源未验收或范围不明 | blocked | none-current-project | REQ-DOC-020 | non-goal-boundary |

## 状态模型

### 来源

unregistered → identified → accepted；异常终态为 needs-review、source-invalid 或 unsupported。多个候选只有在人工确认后才能进入 selected。

### 页面或内容单元

not-started → extracting → assembled → review-pending → passed、needs-review 或 failed。修复产生新派生版本，不覆盖 raw Evidence。

### 视觉资产

candidate → retain、crop-required、omit 或 needs-review。omit 只在全部简单条件满足时成立；复杂视觉不能因已生成 Markdown 就自动进入 omit。

### Run、Evidence、Verdict 和 Claim

- Run：planned → running → succeeded、needs-review、failed 或 cancelled。
- Evidence：collecting → complete、needs-review 或 failed；来源、规则或输入变化后为 stale/invalidated。
- Verdict：pending → passed、needs-review 或 failed；不能直接复制 Run 状态。
- Claim：blocked → bounded；只有当前 passed Verdict 能支持，Evidence 失效后转为 invalidated。

## 关系类型

| relation_type | 含义 | 依据 | 禁止 |
|---|---|---|---|
| observed_order | 本次看到的先后 | 事件序号或时间戳 | 直接推出因果 |
| causal | A 的机制导致 B | 机制、规则或对照 Evidence | 仅凭先后或邻近 |
| dependency | B 启动前必须满足 A | 基线或阶段门 | 说成 A 必然导致 B |
| priority | 风险或价值决定先做什么 | 人工决策 | 冒充执行依赖 |
| reading_order | 人理解内容的推荐顺序 | 文档信息架构 | 冒充 Workflow |
| workflow_order | 规格规定的步骤顺序 | 已批准 Workflow | 冒充本次 Run |
| run_order | 本次实际执行顺序 | Run/Evidence | 反向改写业务规则 |

## 时序案例

### TC-DOC-001 页面阶段门

第 1 页 passed 是第 2–5 页启动的 dependency；第 2–5 页人工复核 passed 是第 6–total_pages 启动的 dependency。它们是 workflow order，不是关于页面内容的 causal relation。

### TC-DOC-002 多格式候选

“PDF 在 PPTX 后被发现”只是 observed_order；选择 PDF 作为主视觉依据需要当前页面质量和人工确认，不能从时间先后推导。

### TC-DOC-003 Evidence 失效

来源哈希变化后，旧 Evidence 变为 stale/invalidated，旧 Verdict 和 Claim 随后失效。这里的因果依据是来源身份契约和变更规则，不是文件修改时间的先后。
