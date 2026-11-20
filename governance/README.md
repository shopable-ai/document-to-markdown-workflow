# 旧英文治理材料入口

本目录保留迁移前形成的通用治理候选材料，供当前真实项目验证临时方法时参考。它不是当前项目治理权威，不是已经验证完成的通用架构，也不预设最终抽离位置。

## 当前项目治理入口

当前项目自己的治理规则只在[项目治理说明](../项目治理/说明.md)及其链接文件中维护。当前业务语义只在[项目需求说明](../项目需求/说明.md)及其链接文件中维护。

## 现有文件的当前身份

| 文件 | 可参考的内容 | 当前身份 |
|---|---|---|
| [AI-WORKFLOW-UNIVERSE.md](AI-WORKFLOW-UNIVERSE.md) | U01—U24 完整性域候选映射 | method-reference |
| [REQUIREMENTS-GOVERNANCE.md](REQUIREMENTS-GOVERNANCE.md) | C01—C18 需求推进候选方法 | method-reference |
| [COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md](COGNITIVE-REASONING-AND-TEMPORAL-OVERLAY.md) | 推理、认知和时序候选约束 | method-reference |
| [ARTIFACT-AND-CHANGE-GOVERNANCE.md](ARTIFACT-AND-CHANGE-GOVERNANCE.md) | 生命周期、漂移和失效传播候选规则 | method-reference |

这些文件可以帮助检查遗漏，但不能直接覆盖当前项目边界、批准基线或要求当前项目照抄通用目录。

## 使用顺序

1. 先从当前中文业务文件和中文项目治理文件确定真实项目状态。
2. 只在发现方法缺口、U 域遗漏或变更失效问题时读取本目录的最小相关范围。
3. 把“临时方法建议”和“真实项目实际发生”分开记录。
4. 当前项目实际验证并经人工确认后，才讨论哪些共性可以抽离。

## 禁止升级

目录存在、旧评分较高、静态验证通过或既有 Workflow 可运行，都不能把本目录升级为正式通用治理架构。最终目录、仓库或是否进入 ai-project-os 仍需在真实项目 C40 后由人工决定。
