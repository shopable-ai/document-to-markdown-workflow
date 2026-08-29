# C01-C18 需求治理手册

这份手册回答的是“未来一个新项目怎样由 AI 主动推进，而人类在哪些地方阅读、修改和确认”。它属于通用治理框架，不承载当前样板项目的业务细节。

## 1. 这份手册管什么

- 它管理 C01-C18，也就是从用户原始表达走到完整需求基线 V2 之前的阶段。
- 它规定 AI 默认可以做什么，哪些地方必须停下来交给人。
- 它规定哪些 Markdown 是人类控制面，哪些对象只是候选、派生、参考或运行事实。
- 它不负责具体工程设计、Spec、Task、Workflow、代码、测试或 Run。

## 2. 两层边界

- 通用治理框架回答：任何新项目都应该怎样推进。
- 样板项目实例回答：当前这个仓库里的文档转换项目，其需求到底是什么。

判断方法：

- “AI 在 V1 前不得进入正式工程设计”属于通用治理框架。
- “当前 PDF/PPTX 要转换成独立 Markdown”属于样板项目实例。
- “某个模型、适配器、YAML 字段或命令怎么写”属于工程投影或候选设计。

## 3. AI 默认推进范围

只要没有撞上人工门，AI 默认负责：

- 把用户原始表达整理成可追溯的原始需求记录；
- 识别目标、约束、样例、反例、事实、冲突和 Unknown；
- 从原始表达推导场景、业务链路、能力、功能、行为、状态、时序和验收方向；
- 起草和维护人类可读的 Markdown；
- 维护需求内部追溯和变更影响；
- 检查当前草稿是否存在跳步、反向定义、因果混淆或自证倾向。

AI 默认不能做的事：

- 把自己的草稿升级为 confirmed 需求；
- 用现有工程实现反向定义业务语义；
- 用一次 Run、一个测试通过或一个 validator 绿灯替代人工确认；
- 在 V2 前生成正式工程任务并推动实施；
- 同时担任起草者、确认者和独立 Verdict 签发者。

## 4. 人类治理控制面

人类主要看和改这些 Markdown：

1. `requirements/PROJECT-GOAL-AND-SCOPE.md`
2. `requirements/REQUIREMENTS.md`
3. `requirements/FUNCTIONS.md`
4. `requirements/BUSINESS-SCENARIOS-AND-CHAINS.md`
5. `requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md`
6. `requirements/ACCEPTANCE-AND-TEST-SPACE.md`
7. `requirements/BASELINES.md`

人类主要负责：

- 判断 AI 是否理解对了目标和边界；
- 直接改写不准确的自然语言；
- 决定阻断 Unknown、冲突和候选关系；
- 确认 V1、V2，以及后续 V3；
- 决定风险接受和最终声明边界。

## 5. C01-C18 的推进方式

### C01-C04 原始表达到事实/Unknown

AI 可以先整理：

- 用户想解决什么问题；
- 谁会受到影响；
- 什么结果不能接受；
- 哪些是事实，哪些只是推断；
- 哪些地方还缺信息。

这时人类主要阅读：

- `requirements/discovery/RAW-REQUESTS.md`
- `requirements/PROJECT-GOAL-AND-SCOPE.md`

停止条件：

- 原始目标仍然说不清；
- 事实和推断混在一起；
- Unknown 被写成已知结论。

### C05-C07 需求发现到草稿列表

AI 可以继续：

- 对对象分类；
- 形成业务叙事；
- 起草稳定 ID 的需求列表、需求树和功能候选。

这时人类主要阅读：

- `requirements/REQUIREMENTS.md`
- `requirements/REQUIREMENT-TREE.md`
- `requirements/FUNCTIONS.md`

停止条件：

- 新需求找不到来源；
- 树或索引生成了列表中不存在的新对象；
- 功能描述开始混入工程接口、YAML 或代码路径。

### C08-C09 V1 业务范围基线

这里必须停给人。

V1 至少要确认：

- 项目目标；
- 角色；
- 范围和非目标；
- 核心需求；
- 硬约束；
- 阻断型 Unknown。

未通过 V1 时：

- AI 可以继续整理表述；
- AI 不得进入正式工程设计；
- `design/` 只能保持候选和 Brownfield 对账。

### C10-C17 场景、行为、时序与验收

V1 后，AI 才能继续展开：

- 场景与触发；
- 业务链路；
- 业务能力与功能；
- 正常、异常和恢复行为；
- 状态、时序与因果边界；
- 验收判据与测试空间。

这时人类主要阅读：

- `requirements/BUSINESS-SCENARIOS-AND-CHAINS.md`
- `requirements/FUNCTIONS.md`
- `requirements/BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md`
- `requirements/ACCEPTANCE-AND-TEST-SPACE.md`

停止条件：

- “先发生”被误写成“导致”；
- 正常路径有了，异常/恢复没写；
- 验收判据无法判断通过或失败；
- 行为或验收无法回指上游需求和场景。

### C18 V2 完整需求基线

这里再次必须停给人。

V2 至少要确认：

- 需求列表；
- 场景和业务链路；
- 功能与功能需求；
- 行为、状态、时序和因果边界；
- 质量、数据、权限、安全、隐私和恢复要求；
- 验收判据和测试空间。

未通过 V2 时：

- AI 可以继续修正文档；
- AI 不得把 design、Spec、Task、Workflow 或代码升级为正式投影。

## 6. 草稿怎样成为基线

- `candidate`：AI 发现或推导出的候选，还不能直接当需求。
- `draft`：已经进入正确文档位置，方便人阅读和修改，但仍未确认。
- `confirmed`：经过合法基线记录确认。

晋升规则：

1. 候选进入 draft，至少要有来源、影响、owner 和未决项说明。
2. draft 进入 confirmed，必须在 `requirements/BASELINES.md` 有明确的人工确认记录。
3. 上游变化后，confirmed 可以转成 `stale` 或 `invalidated`，并从最早受影响门重建。

## 7. Markdown 权威与派生边界

- 前期业务语义权威在 Markdown，不在 YAML。
- 需求树、功能树和索引只整理已有权威对象，不能生成新需求。
- 机器索引必须标明 `generated_from`、`source_hash`、`generated_at`、`generator_version`、`do_not_edit: true`。
- 一旦 Markdown 变更，下游派生索引、设计、Spec、Task、Workflow、Test、Run、Evidence、Verdict、Claim 都要重新判断是否 `review_required`、`stale` 或 `invalidated`。

## 8. 默认加载范围

新项目按阶段加载：

- C01-C09：只默认加载 `requirements/` 与必要的 `governance/` 摘要；
- C10-C18：按需再加载场景、行为、验收和追溯；
- V2 前不默认加载 `contracts/`、`workflows/`、代码或测试；
- Brownfield 项目只有进入 C19 时，才加载既有实现和工程资产做差距对账。

## 9. 防漂移硬约束

- AI 起草不等于人工确认。
- 现有实现不等于业务需求。
- 测试通过不等于需求满足。
- Run 成功不等于 Verdict。
- Verdict 存在不等于 Claim 可无限外推。
- 冲突时停止并显式记录 drift，不静默选择更顺手的解释。

## 10. 需求阶段完成的定义

只有同时满足以下条件，C01-C18 才算真正完成：

- C01-C18 每个节点都有当前文件映射；
- U01-U24 在需求阶段相关部分都可定位；
- 人可以只读 Markdown 判断 AI 是否理解正确；
- 所有阻断型 Unknown 都已处理或被明确保留在基线中；
- V1 和 V2 都有合法人工确认记录；
- 没有需求直接跳到工程任务、Workflow 或代码；
- 没有把 Run、测试或历史评分冒充验收结论。
