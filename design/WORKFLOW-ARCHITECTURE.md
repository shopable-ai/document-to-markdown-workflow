# Workflow、Stage、Skill、Tool、Model 与 Context 架构

状态：Brownfield description。它解释现有工程层，不是项目 Solution Architecture，也不定义需求。

## 层级

| 层 | 回答 | 当前权威 |
|---|---|---|
| Requirement/Function/Behavior | 用户要什么、观察到什么 | requirements Markdown |
| Design/Spec | 如何实现、精确约束是什么 | design + contracts |
| Task | 工程要完成什么工作包 | execution |
| Workflow | 一次执行如何编排阶段和门 | workflows/*/workflow.yaml |
| Skill | 可复用判断与操作指导 | skills/*/SKILL.md |
| Tool | 确定性程序、系统能力或模型适配器 | tool-registry.yaml |
| Model/Context | 本次投影实际选了什么和加载什么 | projection record/runtime context |
| Run/Evidence/Verdict/Claim | 发生了什么、能证明什么 | runs + independent acceptance |

阶段不是 Workflow，Skill 不是 Tool，Tool available 不等于当前 Run 已使用，模型连通不等于质量通过。

## 现有 Workflow A

document-to-markdown-assets 现有 A1–A7：来源预检、原生结构、视觉资产、视觉/关系复核、Markdown 组装、命名/来源登记、资产包验收。它是 FUN-DOC-001..012 的 Brownfield 候选投影；V3 前不得反向修改业务功能卡。

## 现有 Workflow B

markdown-to-gpt-image 现有 B1–B7：内容选择、视觉用途、结构/密度、参考资产、提示词、图像调用、单图验收。它只候选投影 FUN-DOC-014，且可独立消费任意经授权 Markdown。

## 关系

- A 和 B 独立；A 不默认调用图片生成。
- A 到 B 的 handoff 可选，只传选定 Markdown、允许的视觉参考和来源引用。
- knowledge-map-image-to-md 是 A 的兼容入口，不增加第三套阶段。
- lan-pdf-markdown-simple 是 independent-unregistered worker，不成为通用需求入口。
- Workflow acceptance 是项目 AC-DOC 的后置投影；二者冲突时回到 V2/V3，不让 Workflow 覆盖需求。

## 注册表与人类入口

- workflow-registry.yaml：Workflow 入口索引；
- skill-registry.yaml：Skill ID 到 SKILL.md；
- tool-registry.yaml：工具/适配器和实现状态；
- contracts/CONTRACTS.md：所有活跃 YAML 的人类卡片；
- execution/WORKFLOW-A-EXECUTION.md：现有 Workflow A 的人工执行说明。

planned、optional、available、selected、executed 和 verified 必须分开。

