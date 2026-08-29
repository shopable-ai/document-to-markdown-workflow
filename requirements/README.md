---
title: "00 需求文档入口"
description: "说明需求文档的来源、首选阅读顺序、当前状态和修改边界。"
order: 10
---

# 需求文档入口

这里先回答人最关心的四个问题：为什么做、要解决什么问题、需要哪些功能、怎样判断结果符合需要。

阅读和修改这些需求，不需要先理解 YAML、Contract、Spec、Workflow、代码或测试。那些工程对象只能在需求得到人工确认之后引用这里的内容，不能反过来改变这里的需求。

## 原始需求稿在哪里

下列文件是本次迁移的原始人工材料，完整保留，没有删除：

- [原项目目标与详细需求叙述](../archive/governance-migration-20260828/GOAL.zh.md)
- [原需求树、十二类需求和人工确认问题](../archive/governance-migration-20260828/TASK-TREE.zh.md)
- [原工程任务树](../archive/governance-migration-20260828/EXECUTION-TASK-TREE.zh.md)
- [原项目交付治理主链](../archive/governance-migration-20260828/PROJECT-DELIVERY-WORKFLOW.zh.md)
- [原 Workflow 总览](../archive/governance-migration-20260828/WORKFLOWS.zh.md)

前两份是当前需求重建的主要来源。后三份用于以后核对需求到工程的投影，不能作为用户需求来源。

## 现在先看哪些文件

建议普通项目参与者按下面顺序阅读：

1. [01 项目目标与范围](PROJECT-GOAL-AND-SCOPE.md)：要解决什么问题，直接交付什么，哪些事不在当前范围。
2. [02 需求说明](REQUIREMENTS.md)：逐条阅读和修改已经从原稿识别出的用户需求。
3. [03 功能需求](FUNCTIONS.md)：站在使用者角度说明每项功能需要做什么，不写实现方式。
4. [04 验收问题](ACCEPTANCE-AND-TEST-SPACE.md)：人工用什么问题判断需求是否得到满足。

需要进一步核对时再看：

- [05 需求树](REQUIREMENT-TREE.md)：需求的分组和阅读导航，不产生新需求。
- [06 使用场景与业务链路](BUSINESS-SCENARIOS-AND-CHAINS.md)：人在什么情况下使用，期望发生什么。
- [07 行为、状态与先后关系](BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md)：正常、异常、恢复和先后关系的人工说明。
- [08 需求确认与三次基线](BASELINES.md)：每次确认究竟确认什么，以及确认后允许继续到哪里。
- [09 需求内部追溯](TRACEABILITY.md)：需求、场景、功能、行为和验收之间的对应关系。

## 当前状态

本目录正在根据原始 GOAL 和 TASK-TREE 重建，尚未得到人工确认。

- 原始材料：reference，完整保留，只用于核对来源和防止迁移遗漏。
- 本目录文档：draft，供人工阅读、纠正和确认。
- 需求树和追溯表：derived，只能整理已经写入需求说明的内容。
- 工程设计、Spec、任务、Workflow、代码、测试和运行结果：当前不参与需求定义。

在人工确认完成之前，不把本目录中的草稿表述当成用户已批准需求，也不从现有实现补写需求。

## 怎样修改

优先直接修改自然语言：问题、期望、不能接受的结果、仍待确认的内容。稳定编号只是为了讨论时能够准确指向一条需求，不要求参与者填写机器字段。

如果发现原稿中的内容没有被新文档承接，请保留原文并标记“待归类”；不要为了让结构整齐而删除，也不要把不确定内容改写成已确认结论。
