---
title: "A3 需求发现记录"
description: "保存事实、样例、反例、候选解释、路线候选和迁移前章节去向。"
order: 30
---

# 文档资产化需求发现记录

状态：reference-candidate。这里保存从人类需求稿中拆出的事实、冲突、候选和待确认问题，不是第二份需求列表。

## 已有事实

| discovery_id | 类型 | 内容 | 来源 | 处置 |
|---|---|---|---|---|
| DISC-DOC-001 | fact | 页面意义可能来自位置、分组、箭头、层级、颜色和图例，不只来自文字顺序 | SRC-LEGACY-GOAL | 形成 REQ-DOC-003、006、007 |
| DISC-DOC-002 | fact | 同名或相近的 PPTX/PDF 可能是同一材料的不同版本，也可能不是 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | 保留为来源选择 Unknown |
| DISC-DOC-003 | fact | 旧示例可用于测试，但不能成为新来源的答案 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | 形成 REQ-DOC-022 |
| DISC-DOC-004 | fact | 语义标题被来源文件名和页码污染是已知需求风险 | SRC-LEGACY-GOAL、SRC-LEGACY-TREE | 形成 REQ-DOC-009 |

## 样例和反例

| example_id | 类型 | 内容 | 对应需求 | 状态 |
|---|---|---|---|---|
| EX-DOC-001 | positive | 纯文字页可靠转成标题、段落、列表后不保留读者截图 | REQ-DOC-005 | draft |
| EX-DOC-002 | positive | 流程图转写节点与已确认关系，同时保留原区域截图 | REQ-DOC-006 | draft |
| EX-DOC-003 | positive | 无可确认标题时使用临时页码标题并标记 needs-review | REQ-DOC-009、012 | draft |
| CEX-DOC-001 | counterexample | 已识别图片文字，因此删除流程图截图 | REQ-DOC-006 | prohibited |
| CEX-DOC-002 | counterexample | 从旧文档补入当前页面缺失的标题和结论 | REQ-DOC-001、022 | prohibited |
| CEX-DOC-003 | counterexample | 两个相似文件名自动合并为同一文档 | REQ-DOC-008 | prohibited |
| CEX-DOC-004 | counterexample | HTTP 200 后声称视觉关系已正确理解 | REQ-DOC-013、021 | prohibited |

## 候选与替代解释

| candidate_id | 候选结论 | 替代解释 | confirmation_owner | 当前状态 |
|---|---|---|---|---|
| CAND-DOC-001 | PDF 页面图通常可作为多格式候选的主视觉依据 | 某些 PPTX 才是更完整或更新的视觉版本 | ROLE-OWNER | candidate |
| CAND-DOC-002 | 复杂视觉默认保留原图或区域图 | 授权范围可能只允许结构化证据或重绘 | ROLE-OWNER | candidate |
| CAND-DOC-003 | 本地路线达标后可作为低成本默认 | 不同来源类型可能需要不同路线 | ROLE-OWNER | candidate |
| CAND-DOC-004 | 知识库低价值页清洗值得保留为可选功能 | 它可能属于独立下游项目 | ROLE-OWNER | candidate |

## 迁移前保留的路线候选

这些名称来自原人类需求稿，只表示待同页实测的候选，不表示当前可用、主次已定或业务需求被实现：

- 本地原生解析和渲染；
- Apple Vision OCR 加确定性结构重建；
- mac96 小视觉模型；
- mac96 30B 视觉模型；
- 当前对话或其他在线视觉 provider，仅在本次任务明确授权时参与。

SSH 只是曾用 transport，不是业务需求；mac96 只是候选资源标识，不是永久架构。所有候选必须使用相同真实页面、图片 hash、schema、提示边界和评分表比较文字、结构、关系、重复性、耗时、资源和成本。传输失败是 unavailable/failed，不应自动伪装成 needs-review；真实内容冲突或人工未决才进入 needs-review。

V2 通过后，本文件降为 reference 并退出正常执行上下文；只有发生变更、冲突、Unknown 复核或审计时重载。

## 迁移前人工文档逐章节去向

| 原文件章节 | 内容职责 | 新活动位置 | 迁移说明 |
|---|---|---|---|
| GOAL 1–2 | 问题、风险和为何容易出错 | PROJECT-GOAL-AND-SCOPE；本文件 | 目标长期保留；事实/风险进入 discovery |
| GOAL 3.1–3.2 | 读者、复核者和独立 Markdown 交付 | PROJECT-GOAL-AND-SCOPE；REQ-DOC-001..012 | 作为当前项目直接范围 |
| GOAL 3.3–3.4 | 知识库/AI/视觉下游 | PROJECT-GOAL-AND-SCOPE 非目标；REQ-DOC-019/020 | 下游边界保留，不混入默认 Workflow |
| GOAL 4.1 | 读对当前文件 | REQ-DOC-001、022；FUN-DOC-001 | 对应原需求一 |
| GOAL 4.2、4.2.1、4.2.2 | 内容、复杂视觉、资产路线和先成功再降本 | REQ-DOC-003、005..007、010、013、014、021；FUN-DOC-002/003/007/008 | 分开内容保真、视觉保存和路线验证 |
| GOAL 4.3 | 原意保真和轻度整理 | REQ-DOC-004、012；FUN-DOC-002 | 不进入工程修复实现细节 |
| GOAL 4.4 | Markdown 不套单一模板 | BHV-DOC-004、005、008 | 转成用户可观察行为 |
| GOAL 4.4.1 | 标题与来源定位分层 | REQ-DOC-009、011、017；FUN-DOC-006 | 对应原需求六 |
| GOAL 4.5 | PDF/PPTX 候选关系 | REQ-DOC-008；FUN-DOC-004；SCN-DOC-002 | 对应原需求四 |
| GOAL 4.6 | 逐步确认 | REQ-DOC-015、016、017；FUN-DOC-010/011 | 对应原需求十、十一 |
| GOAL 4.7、9 | 不过度结论和完成含义 | REQ-DOC-012、018；FUN-DOC-005；AC-DOC-018 | Run 与 Verdict 分离 |
| GOAL 4.8 | 知识库整页清洗 | REQ-DOC-019；FUN-DOC-012 | 对应原需求十二，保持 optional |
| GOAL 5 | 用户希望得到的回答 | BUSINESS-SCENARIOS；ACCEPTANCE | 转成场景和事前判据 |
| GOAL 6、8 | 边界、当前范围和暂不处理 | PROJECT-GOAL-AND-SCOPE | 当前状态段不再混入目标 |
| GOAL 7 | 需求确认后的推进 | BASELINES | 分为 V1/V2/V3 |
| GOAL 10 | 当前停点 | archive 历史快照；未来 runtime record | 退出长期需求权威 |
| TASK-TREE 1–4 | 一句话诉求、背景、目标和用户问题 | PROJECT-GOAL-AND-SCOPE；REQUIREMENTS；SCENARIOS | 保留人类语言，不压成工程组件 |
| TASK-TREE 5.一–十二 | 十二个原需求组 | REQUIREMENTS 直接映射表；FUNCTIONS FUN-DOC-001–012 | 一项原需求对应一项主业务功能 |
| TASK-TREE 6 | 需求之间关系 | REQUIREMENT-TREE；TRACEABILITY | 树为派生，不产生需求 |
| TASK-TREE 7 | 需求确认问题 | BASELINES；ACCEPTANCE | 转为 V1/V2 人工检查 |
| TASK-TREE 8 | 工程交接边界 | requirements/README；BASELINES | 保留“需求先于工程”的门 |
| TASK-TREE 9 | 当时运行状态 | archive 历史快照；未来 runtime record | 不进入需求、功能或长期任务树 |

这张表是判断迁移相关性的人工入口：如果原章节找不到新位置，或新功能找不到原需求组，应视为需求迁移失败，而不是用工程文件补齐。
