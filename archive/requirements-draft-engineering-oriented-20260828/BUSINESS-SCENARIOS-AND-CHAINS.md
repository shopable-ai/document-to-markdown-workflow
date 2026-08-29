---
title: "06 使用场景与业务链路"
description: "描述单一来源转换、候选确认、视觉处理、页面修复和下游边界等业务链路。"
order: 70
---

# 文档资产化业务场景与链路

状态：authoritative-draft。所有场景都等待 V1/V2 人工确认。

## 场景列表

| scenario_id | name | trigger | roles | requirement_refs | terminal_outcomes | status |
|---|---|---|---|---|---|---|
| SCN-DOC-001 | 单一来源转换 | 用户提供一个 source、输出范围和页码 | ROLE-READER、ROLE-OPERATOR | REQ-DOC-001..007、009..012、018、022 | passed、needs-review、failed | draft |
| SCN-DOC-002 | 多格式候选确认 | 用户显式提供或确认多个文件的逻辑候选关系 | ROLE-OPERATOR、ROLE-REVIEWER | REQ-DOC-001、008、012 | selected、separate-sources、needs-review | draft |
| SCN-DOC-003 | 复杂视觉内容处理 | 页面意义依赖位置、方向、层级、颜色、图例或图形组合 | ROLE-READER、ROLE-REVIEWER | REQ-DOC-003、005..007、010..014、021 | retain、crop、structured-only、needs-review | draft |
| SCN-DOC-004 | 页面 Markdown 分阶段修复 | 已有页面单元需要读者层修复 | ROLE-OPERATOR、ROLE-REVIEWER | REQ-DOC-004、012、016、017 | gate-passed、needs-review、failed | draft |
| SCN-DOC-005 | 长文档受控运行 | 预计超过短交互或需要多页处理 | ROLE-OPERATOR、ROLE-VALIDATOR | REQ-DOC-015、017、018、021 | completed、cancelled、needs-review、failed | draft |
| SCN-DOC-006 | 模型路线校准 | 确定性提取不足且模型调用已获授权 | ROLE-OPERATOR、ROLE-REVIEWER | REQ-DOC-007、013、014、021 | route-selected、unavailable、needs-review | draft |
| SCN-DOC-007 | 知识库工作副本清洗 | 完整页集合汇合且用户显式启用 | ROLE-DOWNSTREAM、ROLE-OPERATOR | REQ-DOC-019 | applied、aborted、needs-review | draft-optional |
| SCN-DOC-008 | 下游视觉派生 | 用户另行请求翻译、重绘或图片生成 | ROLE-DOWNSTREAM | REQ-DOC-020 | derived-output、blocked | draft-optional |

## 业务链路

### CHAIN-DOC-001 单一来源到交付

当前来源确认 → 内容与关系提取 → 简单/复杂视觉判断 → Markdown 表达 → 资产命名和来源登记 → Unknown 汇总 → 独立验收 → 交付三态。

拒绝路径：

- 来源或候选关系不明确：回到来源确认；
- 模型、原图外发或保留未授权：停止对应路线；
- 内容或关系不可确认：保留 Unknown/needs-review；
- 原始证据将被覆盖：failed；
- 独立验收不满足：不得产生 passed。

### CHAIN-DOC-002 页面修复

第 1 页工作单元 → 页面结构守卫和人工查看 → 第 2–5 页小批量 → 人工复核 → 第 6–total_pages → 确定性合并 → 独立验收。

任一门失败时停止；document-temp.md 只是派生预览，不是新的事实源。

### CHAIN-DOC-003 模型校准

确定性候选 → 授权检查 → 相同输入和 schema 的候选路线 → 质量/重复性/耗时/资源/成本比较 → 选择、降级或 unavailable。

HTTP 200、模型列表或队列接收只证明连接或受理，不证明文字、结构和视觉关系正确。

### CHAIN-DOC-004 可选下游

已独立验收的 Markdown/页面单元 → 用户显式选择 → 哈希锁定预览 → 清洗或派生 → 新的独立结果和声明。下游结果不得反向改写来源转换的需求、Evidence 或 Verdict。
