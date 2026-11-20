# 认知、需求推理与时序叠加层

> **方法参考：** 本文件不是当前项目治理权威。当前推理与时序规则见[AI 推理与时序关系](../项目治理/AI推理与时序关系.md)。

旧状态：reference-candidate；其中规则只作为临时方法输入，须经真实项目验证。

## 六类用户对象

| object_type | 问题 | 例子 | 可否直接成为需求 |
|---|---|---|---|
| goal | 最终想达到什么 | 当前来源成为可读 Markdown | 否，需展开边界和判据 |
| need | 必须满足什么 | 复杂视觉必须可核对 | 可形成 draft，需确认 |
| problem | 当前哪里不满足 | 标题被来源前缀污染 | 否，需判断责任层 |
| confusion | 用户哪里不确定 | 两个格式是否同一材料 | 否，保留 Unknown |
| failure | 哪次结果失败 | 页面关系未被识别 | 否，属于 Run/Evidence |
| expected-result | 希望看到的结果 | 流程图有 Markdown 和截图 | 可作为样例和验收方向 |

AI 先分类对象，再生成候选；不能把 failure 直接改写成永久需求，也不能把 expected-result 自动当完整验收。

## 推理记录

每条候选至少记录：

- source_ref 和原始陈述；
- object_type；
- facts、assumptions、alternatives 和 counterexamples；
- candidate_statement；
- confidence 只表达推断确信度，不表达批准状态；
- unknowns、decision_impact、confirmation_owner；
- promoted_to 或 rejected_reason。

缺任一关键字段时，候选留在 discovery。

## 时序与关系

- observed_order：实际先后；
- causal：有机制、规则或对照 Evidence 的因果；
- dependency：门或输入依赖；
- priority：风险/价值优先级；
- reading_order：人类理解顺序；
- workflow_order：批准规格顺序；
- run_order：本次真实顺序。

关系不明时使用 Unknown。A 先于 B 不足以证明 A 导致 B。

## 认知负荷和重新进入

- stable entry：根 README 和 C01–C40；
- local entry：每个目录 README；
- minimal context：只加载当前阶段的权威、当前 Unknown 和最新基线摘要；
- reentry：从当前基线、变更记录和最早受影响门恢复；
- compression guard：任何交接摘要必须保留 C01–C40、U01–U24、关键文件集合、样板状态和未执行边界。

## 晋升和回退

candidate → draft 需要来源、owner 和人工选择；draft → confirmed 需要合法基线记录。上游变化后 confirmed 可转 stale/invalidated，并回到最早责任对象。AI 生成者不得同时充当确认人、独立 validator 或 Claim 签发者。
