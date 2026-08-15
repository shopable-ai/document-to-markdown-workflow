# 遗漏环节与横向门禁

A1–A7、B1–B7 已覆盖各自的主要业务链路，但一个可落地 Workflow 还需要横向控制层。横向控制层不一定新增业务阶段，而是通过运行记录、输出契约、handoff 和验收规则补齐。

## 已补齐的规格门禁

- **入口与选择**：根目录 `WORKFLOWS.zh.md` 和 `workflow-registry.yaml`。
- **A→B 交接**：`contracts/workflow-handoff.yaml`，明确可选、来源、资产 ID 和授权边界。
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
5. **真实适配器**：SSH/mac96 默认视觉复核、Apple Vision OCR 兜底和 GPT-image-2 单图调用，包含连通性、超时、限流、下载完整性和秘密隔离。
6. **人工复核界面或报告消费端**：`needs-review` 不能只写入 YAML 后无人处理。
7. **Schema 运行时校验**：当前 YAML 是契约描述，规格验证器已检查路径和关键字段；仍需对真实产物执行字段、链接、哈希、图片 MIME 和数量校验。

## 不应误判为遗漏

- A1–A7、B1–B7 不需要各自升级成独立 Workflow；它们是阶段。已有转换结果审计属于原工作流的后续阶段。
- A 的页面图、复杂区域图不自动升级为模板资产；模板泛化需要独立人工确认。
- 测试运行目录不等于业务输出目录；测试证据不能替代 `output_root` 产物。
