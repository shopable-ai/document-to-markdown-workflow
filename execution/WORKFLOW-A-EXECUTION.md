# Workflow A 人工执行边界

状态：Brownfield guide。该指南解释已存在的执行入口，不表示 V1/V2/V3 已通过，也不授权本轮启动真实转换。

## 执行前

- 确认当前 source、output_root、pages、授权和候选关系；
- 读取精简 production runtime context，不默认加载需求史、推理史、全部 Workflow 或实现源码；
- 记录来源指纹、媒体类型、总页数、实际页码和 preflight 结果；
- 任何模型或外部路线必须有当前授权。

## 既有 A1–A7

1. A1 来源预检；
2. A2 原生结构提取；
3. A3 视觉资产保留；
4. A4 视觉文本与结构复核；
5. A5 Markdown 组装；
6. A6 命名与来源登记；
7. A7 当前包独立验收。

具体机器阶段以 workflows/document-to-markdown-assets/workflow.yaml 为准；本文件不复制字段和枚举。

## 页面修复阶段门

- 第 1 页是首个工作单元；
- 第 1 页通过后只开放第 2–5 页；
- 第 2–5 页有人工查看记录后才开放第 6-total_pages；
- raw OCR、bbox、confidence、Vision JSON、页面图和原始 manifest 只读；
- document-temp.md 是派生预览，不替代 index.md。

## 长任务

预计超过交互阈值的任务按已选 Workflow 路线脱离对话并写 progress-current、progress ledger 和 run log。只有启动、真实异常、用户查询、完成或取消事件进入对话；不把周期性“仍在运行”当证据。

## 结束报告

报告 source、fingerprint、actual pages、package path、status、independent validator 和 validation-result。passed 才能交给后续；needs-review 保留证据并请求人工；failed 不声称完成。

本轮明确不执行上述真实流程。
