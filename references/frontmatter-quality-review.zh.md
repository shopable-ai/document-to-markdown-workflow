# 前置需求文档专家评分

## 评分范围

本评分只评审前置文档的需求表达和职责边界，不评审运行器、候选比较器、OCR、真实输入或生产 package。因此它不是 Workflow A 的 `passed`，也不能形成业务 Claim。

评审对象：

- [`GOAL.zh.md`](../GOAL.zh.md)
- [`TASK-TREE.zh.md`](../TASK-TREE.zh.md)
- [`WORKFLOW-MAP.zh.md`](../WORKFLOW-MAP.zh.md)
- [`contracts/requirement-chain.yaml`](../contracts/requirement-chain.yaml)

## 上一版专家评分：99/100（历史基线）

该分数对应本轮需求回写前的文档版本。当前版本新增了“读者标题与来源定位分层”“局域网视觉资源与降级路径”和独立的已知错误目录，以下评分不再作为当前版本的通过结论；需要重新评审后才能更新分数。

| 维度 | 满分 | 得分 | 评审结论 |
|---|---:|---:|---|
| 需求覆盖 | 20 | 20 | 背景、目标、用户问题、硬性边界、范围和非目标完整 |
| 人工可读性 | 20 | 20 | 需求树以自然语言表达动机、担忧、期望和推进顺序 |
| 职责分离 | 20 | 20 | TASK-TREE 只保留需求表达；工程 Chain/Spec/Workflow 归入 WORKFLOW-MAP |
| 工程映射可追溯 | 20 | 19 | 需求组到工程映射已经单独列出，但尚未获得人工确认 |
| 复核边界清楚 | 20 | 20 | GOAL 和 TASK-TREE 明确停在需求确认；工程补充集中到 WORKFLOW-MAP |
| **总分** | **100** | **99** | **达到 95 分前置文档门槛** |

## 扣分项与恢复条件

- 扣 1 分：工程 Chain 仍是“映射草案”，尚未由用户确认；确认后复核 `WORKFLOW-MAP.zh.md` 与 contracts 的一致性即可恢复。
- 扣 1 分：工程映射尚未获得人工确认；确认后复核 WORKFLOW-MAP 与 contracts 的一致性即可恢复。

## 上一版结论（仅作历史记录）

- 前置需求文档：上一版为 `passed`（99/100，范围仅限人工需求稿、需求任务分解和职责边界）。
- 工程 Chain：`needs-review`（等待需求任务树确认和工程映射复核）。
- Run / Evidence / Verdict / Claim：`blocked`。

## 当前版本状态

- 前置需求文档：`needs-review`（等待对新增门禁和已知错误回链重新评分）。
- `references/known-failures.zh.md`：已建立目录，但其中条目仍需按当前 source 的 Run/Evidence 才能晋级为 `observed` 或 `resolved`。

## 下一步门槛

只有用户确认需求树中的各个需求组和推进顺序后，才允许把工程映射投影到 Workflow、Skill/Tool、contracts 和 adapters；届时必须重新评分，且不能把本评分当作运行结果。
