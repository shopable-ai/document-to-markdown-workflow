# 遗漏环节与横向门禁

A1–A7、B1–B7 已覆盖各自的主要业务链路，但一个可落地 Workflow 还需要横向控制层。横向控制层不一定新增业务阶段，而是通过运行记录、输出契约、handoff 和验收规则补齐。

## 已补齐的规格门禁

- **入口与选择**：根目录 `WORKFLOWS.zh.md` 和 `workflow-registry.yaml`。
- **A→B 交接**：`contracts/workflow-handoff.yaml`，明确可选、来源、资产 ID、`reconstruction_profiles` 和授权边界。
- **复杂图片重建输入**：`contracts/visual-reconstruction-profile.yaml`，要求结构字段、提示词、来源证据和 Unknown/needs-review 一起保存；profile 不是生成等价性的证明。
- **A 正式输出**：`contracts/workflow-a-output-layout.yaml`。
- **B 正式输出**：`contracts/workflow-b-output-layout.yaml`。
- **运行生命周期**：`contracts/workflow-run-record.yaml`，包含 run ID、输入摘要、版本快照、状态、重试和证据等级。
- **逐阶段证据**：`contracts/stage-evidence.yaml`，强制 A1–A7/B1–B7 分别记录状态、证据等级、路径、诊断和验证命令。
- **失败报告**：`contracts/failure-report.yaml`，即使没有失败也输出空报告；P0、来源缺失和完整性失败不能被总体验收吞掉。
- **人类理解**：两个 Workflow 子目录的 `README.zh.md`。
- **关系调研**：根目录 `WORKFLOW-MAP.zh.md`。

## 实现阶段仍需完成

1. **幂等与恢复实现**：同一输入和配置不重复生成相同运行；中断后能从阶段证据恢复。
2. **资源与保留策略**：临时文件、页面图、OCR 中间结果和在线请求记录的保留/清理规则。
3. **隐私与授权执行**：哪些来源资产允许上传到 mac96 或 GPT-image-2，由运行前策略检查决定。
4. **确定性工具实现**：PPTX XML、PDF 布局、DOCX OOXML、渲染、媒体提取、裁剪和本地 OCR。
5. **真实适配器**：SSH/mac96 默认视觉复核、Apple Vision OCR 兜底、明确授权的在线视觉单图逐字转写和 GPT-image-2 单图调用，包含连通性、超时、限流、下载完整性和秘密隔离。
6. **人工复核界面或报告消费端**：`needs-review` 不能只写入 YAML 后无人处理。
7. **Schema 运行时校验**：当前 YAML 是契约描述，规格验证器已检查路径和关键字段；仍需对真实产物执行字段、链接、哈希、图片 MIME 和数量校验。
8. **语言质量修复的真实运行证明**：已补充独立的 `markdown-content-repair` 局域网优先适配器、契约和页面级证据路径；当前规格维护轮只证明路由和结构守卫，不证明 mac96 在本机此刻可达、模型可用或真实页面已修复。不得把适配器存在写成真实模型修复完成。
9. **实时进度与硬停止**：当前运行记录主要在阶段结束后落盘；仍需验证逐页/逐阶段心跳、smoke 总耗时预算、无进展停止和 selected-pages/evidence-pages 遗漏差集。

## 风险目录中不可前移的项目

这些项目必须在问题目录中保留，但不能被执行前门禁“提前判定通过”：

| 项目 | 最早可靠检测点 | 当前证据状态 | 允许结论 |
| --- | --- | --- | --- |
| 页面语义标题、正文、表格和列表层级 | A2/A5 + 人工复核 | `unknown` / `needs-review` | 不能由页数或 native text 非空代替 |
| OCR 乱码和候选正确性 | A4 + 人工复核 | `not-run` 或 candidate | OCR 候选不能升级为 confirmed |
| 图形方向、父子、箭头和未知形式 | A4 + 视觉/人工复核 | `unknown` | 未决关系必须保留原图和 occurrence |
| DPI、字体替代、裁剪边界和视觉可读性 | A3/A4 + 实际打开资产 | `production-only` | 工具存在不等于视觉质量通过 |
| 语言质量修复 | A5 后的受控 draft/re-audit | `planned` | raw layer 不可覆盖 |
| 长运行实时进度、停止点和恢复 | smoke/batch 真实运行 | `needs-verification` | checkpoint 不得覆盖旧失败包 |
| 人工复核是否完成 | A7 消费端 | `not-implemented` | `needs-review` 不能无人消费 |

“Unknown”是证据状态，不是失败原因的替代词；每项仍需写出来源页/区域、下一步和是否阻断交付。

## 不应误判为遗漏

- A1–A7、B1–B7 不需要各自升级成独立 Workflow；它们是阶段。已有转换结果审计属于原工作流的后续阶段。
- A 的页面图、复杂区域图不自动升级为模板资产；模板泛化需要独立人工确认。
- 测试运行目录不等于业务输出目录；测试证据不能替代 `output_root` 产物。
