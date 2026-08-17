# Workflow A 新对话续接提示词：mac96 优先与真实提取

原始需求：

继续完善 `/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets` 内的 Workflow A `document-to-markdown-assets`。目标不是只通过夹具断言，而是对来源 PPTX/PDF/DOCX/图片真实执行“原生结构提取 → 来源视觉资产 → Markdown + manifest + 报告”。图片文字、复杂关系和视觉布局必须有实际复核路径。

本轮目标与验收：

- 把执行优先级固定为：
  `原生 PPTX/PDF/DOCX 结构提取 → SSH/mac96 单图视觉文字转写 → mac96 不可用时本机 Apple Vision OCR → 两者均失败时 failed/unavailable；真实冲突/未裁决才 needs-review`。
- mac96 返回非空、可解析的单图 Markdown 后，Workflow A 不应因为缺少可选 `unknown_forms` 字段而强制降级。
- 先检查 `ssh mac96` 的只读可达性、视觉模型服务和模型标识；不要把历史状态当成当前可用。
- 对固定夹具 `tests/fixtures/18个工作思维模型-last-2-slides/` 只处理原文档第 24、25 页。
- 若 mac96 可用，真实发送第 24、25 页或必要区域图，并保存结构化复核证据；若不可用，记录失败原因后执行 Apple Vision OCR。
- 不能用 fixture-sample、页面图片存在或关键文字断言替代图片内容提取。
- 只有真实解析器实际写出正式包后，才能报告“夹具范围内真实解析结果”。

任务拆分：

- 1. 审计并修正执行入口
  - 1.1 阅读 `WORKFLOW-A-EXECUTION.zh.md`、`workflows/document-to-markdown-assets/workflow.yaml`、A5/A6/A7 Skill 和相关契约。
  - 1.2 确认 mac96 是默认图片文字转写路径，Apple Vision OCR 是第一兜底；`needs-review` 只记录真实冲突或未裁决关系。
- 2. 验证运行依赖
  - 2.1 只读检查 SSH/mac96 连通性、模型/服务和当前能力。
  - 2.2 若 mac96 不可用，记录可复核错误并转 Apple Vision OCR；不得静默跳过。
- 3. 真实处理固定夹具
  - 3.1 PPTX 验证 XML 文字、节点、连线、分组和坐标。
  - 3.2 PDF 验证文本块、阅读顺序和页面视觉布局。
  - 3.3 对第 24、25 页执行 mac96 或 Apple Vision OCR，并分层保留原生事实、OCR 候选、已观察视觉形式、冲突和 Unknown。
  - 3.4 生成正式结果目录：`index.md`、`manifest.yaml`、扁平 `assets/`、`reports/`；页面图、嵌入媒体和区域图的类型写入 manifest，不创建分类子目录。
- 4. 验收与报告
  - 4.1 检查 Markdown 图片链接、manifest 路径、来源页码、哈希和报告状态。
  - 4.2 明确区分规格静态校验、夹具契约、真实本地解析、mac96、Apple OCR、GPT-image-2。
  - 4.3 跑两条固定命令，并额外提供真实解析命令和其完整输出；如果解析器不存在，明确阻断点，不伪造完成。

当前状态与证据：

- 已确认：规格静态校验和 7 项夹具契约测试通过；夹具运行记录为 `fixture-contract-20260814-0118`。
- 已确认：当前 `tests/runs/fixture-contract-20260814-0118/outputs/document-asset-package/` 是 `fixture-sample`，不是正式业务结果。
- 已确认：此前本次夹具测试没有调用 SSH/mac96、Apple Vision OCR 或 GPT-image-2。
- 待核实：目标机器当前 SSH、视觉服务、模型能力，以及目标目录内是否已有真实解析器实现。

约束与未知项：

- 只查看和修改上述目标目录；不要读取或修改其他项目目录及原始下载目录。
- 不新增测试 Workflow 或测试 Skill；不扩展 Workflow B。
- 不泄露密码、Token、Cookie、Authorization 或订阅 URL。
- 不把 mac96 连接成功、HTTP 200、模型列表或空响应当成视觉复核成功；必须有真实结构化输出。
- Apple Vision OCR 只提供候选文字，不得覆盖原生结构事实。

交付要求：

- 修改后的执行说明、契约/Skill/Workflow 配置和必要测试记录。
- SSH/mac96 或 Apple Vision OCR 的真实运行证据；若均失败，提供 `failed/unavailable` 证据和失败原因。
- 正式业务结果包路径；如果没有，明确写 `real_business_result_package: null`。
- 两条固定测试命令、真实解析命令、运行路径和未执行项的真实输出。
