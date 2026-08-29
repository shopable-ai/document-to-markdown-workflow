# 文档资产化验证设计

状态：candidate-blocked-by-V3。验证设计消费 requirements/ACCEPTANCE-AND-TEST-SPACE，不通过测试结果反推需求。

## 验证层

| level | 证明什么 | 不能证明什么 |
|---|---|---|
| L1 static | 文件、ID、链接、schema、映射和派生索引一致 | 真实内容正确 |
| L2 fixture/unit/contract | 已知输入下的确定性行为和错误门 | 未见来源的生产泛化 |
| L3 representative smoke | 代表来源的端到端风险样例 | 全量来源通过 |
| L4 controlled run | 当前来源和范围实际执行 | 独立验收通过 |
| L5 evidence/verdict | 事前判据对当前 Evidence 的独立判断 | 超出范围的 Claim |

## 判据到验证方法

| acceptance range | 主要方法 | 关键 Evidence |
|---|---|---|
| AC-DOC-001/008/022 | 来源切换、候选和污染反例 | source fingerprints、selected scope、diff |
| AC-DOC-002..004/009/012 | 内容复读、结构对比、标题和 Unknown 审查 | page units、source locations、review notes |
| AC-DOC-005..007/010/011 | 视觉分类、截图范围、引用和命名核对 | contact sheet、asset refs、manifest |
| AC-DOC-013/014/021 | 授权门和相同输入 route benchmark | authorization、input hashes、scorecard |
| AC-DOC-015..017 | 状态机、停止、恢复、页面阶段门、raw hash | progress、checkpoint、before/after hashes |
| AC-DOC-018 | 独立 package validator | validator identity、verdict report |
| AC-DOC-019/020 | 下游 hash-lock 和派生标记 | plan、ledger、rebuild、derivation refs |

## 独立性

生成者、验证器和 Claim 责任人必须可区分。自检可以产生问题清单，不能签发独立 Verdict。validator 读取当前 source/package/version；旧报告或旧 Evidence 不能复用。

## 本轮边界

本轮只运行 L1 和既有 L2 静态/单元验证，不启动 L3–L5 真实转换验证。因此最多证明治理和规格包一致性，不能证明业务需求已确认、真实转换通过或生产 readiness。

