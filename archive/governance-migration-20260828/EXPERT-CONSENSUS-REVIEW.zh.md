# 文档驱动工作流专家共识审查

> 审查日期：2026-08-27
> 审查方式：5 个专业角色 × 8 轮交叉审议 = 40 次专家发言
> 结论性质：设计质量审查，不等同于真实来源转换验收。

## 1. 参与角色

| 角色 | 审查重点 |
|---|---|
| 需求/产品专家 | 用户对象、需求语句、需求晋升和业务链 |
| 信息架构/系统架构专家 | U/P/A-B/J 分层、单一权威和对象关系 |
| 时序/状态专家 | 事件、状态迁移、因果、分支、恢复和回退 |
| AI 治理/安全专家 | 自证、上下文污染、证据权威、失效传播 |
| 验证/工程落地专家 | Schema、评分卡、负例、可执行验收和兼容性 |

## 2. 40 次审议后的共识

### 2.1 采用的方案

采用“最小对象化调整”，不做全量图数据库或统一状态平台：

```text
来源材料
→ 需求推理记录
→ 业务需求 / 场景 / 能力 / 功能 / 功能需求
→ 需求基线
→ 执行投影记录
→ 运行记录 / 证据
→ 验收评分卡 / 独立验收结论
→ 有边界的声明
```

保留现有编号和独立边界：`U01–U24`、`P01–P16`、`A1–A7`、`B1–B7`、`J01–J18` 均不重编号、不合并。

### 2.2 必须形成硬门的内容

1. `confidence`、对象生命周期 `status`、独立 `verdict` 三者分离。
2. `candidate → confirmed` 必须有来源、事实、替代解释、确认责任人和范围摘要。
3. `temporal` 不等于 `causal`；只有先后证据不能生成因果结论。
4. 每个失败必须记录 `earliest_owner_layer`、`owner_artifact_ref` 和 `reopen_from_stage`。
5. Claim 必须绑定当前 baseline、当前非 stale Run/Evidence、独立 Verdict 和输入摘要。
6. `95` 是最低质量通过线，`97` 是推荐目标；分数不新增生命周期状态。
7. `fixture`、`reference-candidate`、`prompt`、历史 Run 不得单独支撑生产 Claim。

### 2.3 明确后置

- 完整 Persona/角色本体；当前先用 `role_ref` 表达“谁说、谁确认、谁验收”。
- 全量关系图谱、跨项目知识图、统一状态平台。
- 复杂评分权重学习、多评审者加权和自动因果发现。
- 全局事件总线、分布式锁和跨 Workflow 回滚编排。

## 3. 质量门

本次方案只有同时满足以下条件，才能称为“设计层面 95+”：

| 维度 | 门槛 |
|---|---|
| 分层兼容 | U/P/A-B/J 边界和 A/B 独立运行测试通过 |
| 对象可追溯 | 引用、晋升、投影、运行、证据、验收和声明可形成闭环 |
| 时序安全 | 只有 observed order 的 causal 关系被拒绝 |
| 状态安全 | 非法直跳、stale 复活、自证放行被拒绝 |
| 评分安全 | 总分、维度线、硬门和独立身份均通过机器检查 |
| 证据安全 | fixture/reference/旧 Run 不能冒充当前生产证据 |
| 变更安全 | 上游变化能使受影响 Claim 进入 invalidated 或 review_required |

“设计层面 95+”不等于任何真实转换已经 95+。真实交付仍必须按当前 source、Run、Evidence、独立验收和 Claim 范围逐次证明。

## 4. 已落地的机器入口

- [artifact-reference.yaml](contracts/artifact-reference.yaml)：跨层引用和证据权威等级。
- [promotion-matrix.yaml](contracts/promotion-matrix.yaml)：分层状态及允许迁移。
- [temporal-case.yaml](contracts/temporal-case.yaml)：事件、状态、关系、因果证据和失败回退。
- [projection-record.yaml](contracts/projection-record.yaml)：P12 实际选择与排除。
- [acceptance-scorecard.yaml](contracts/acceptance-scorecard.yaml)：95/97、硬门和独立验收。
- [change-record.yaml](contracts/change-record.yaml)：P16 变更影响和失效传播。
- [adapters/document_governance.py](adapters/document_governance.py)：纯校验函数；不读取运行目录、不改变状态、不自动晋升候选。

## 5. 结论与限制

专家共识认为，当前最优解不是再增加固定环节，而是让关键环节有明确对象、引用、状态、证据和失败出口。当前已完成设计契约和测试实现；尚未声称已有真实生产 Run 自动生成完整 Projection、Scorecard 或 Invalidation Ledger，这些需要后续接入具体运行器时逐项验证。
