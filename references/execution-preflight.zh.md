# 执行前风险预检（横向门禁）

顶级专家不会把“错误目录”当成知识库里的提醒，而会把它变成执行前的可判定门禁：先用廉价、确定性的检查阻止注定失败的运行，再进入 A1–A7；不可廉价确定的问题才留给阶段内的 fallback、`needs-review` 和独立验收。

这不是新增 A8。它是 A1 之前的 `execution_preflight` 横向环节，机器契约为 [`contracts/workflow-a-preflight.yaml`](../contracts/workflow-a-preflight.yaml)，每次真实运行写入 `evidence/preflight.json`。核心预检只做廉价确定性检查，不做转换、不调用 OCR、不调用在线模型；通过后另有一个只读 `capability_probe` 检查 mac96，失败时只决定是否走已声明的 Apple Vision fallback。

本文件是人类可审阅的规则来源；`known-failures.zh.md` 保存完整问题目录，`workflow-a-preflight.yaml` 只编译其中可确定的子集。三者必须通过 `check_id` 和 `risk_ids` 对齐，不再新增第二份同义 preflight 文档。

## 预检顺序

| 预检 | 对应阶段/错误 | 执行前动作 | 阻断条件 |
| --- | --- | --- | --- |
| 规格与失败目录存在 | 横向；契约漂移、已知风险被遗忘 | 确认当前 Workflow、preflight contract、`known-failures` 都存在 | 任一缺失 |
| 授权策略边界 | 横向/K-006；在线模型被默认调用、成本或隐私边界漂移 | 读取 Workflow 的在线视觉模型策略，要求 `explicit-authorization-only` | 策略缺失或默认放开 |
| source 可读且非空 | A1；路径错误、权限错误、空文件 | 检查普通文件、读取权限、大小、SHA-256 | 不存在、不可读、0 字节 |
| 格式与生产入口一致 | A1；声明支持 DOCX 或其他格式但 runner 未实现等错配 | 只允许当前 runner 真正支持的 PPT/PPTX/PDF/图片 | 不支持的后缀 |
| 页码和 smoke 范围 | 横向/A1；越界页、smoke 绕过、页码漂移 | 解析 `--pages` 和 `--smoke-pages`，检查范围与子集关系 | 空、非法、越界或 smoke 不属于 selected pages |
| 来源容器完整性 | A1/A2；损坏 PPTX、缺少原生包 | 对 PPTX 做 ZIP/`presentation.xml` 预检 | ZIP 损坏或核心 XML 缺失 |
| 必需解析/渲染器可执行 | A2/A3；运行很久后才发现没有 pdfinfo、pdftotext、soffice 或 pdftoppm | 只检查当前来源真正需要的工具存在和可执行，不提前渲染 | 当前来源必需工具缺失 |
| 输出目录与幂等性 | 横向/A6；权限失败、重跑覆盖旧证据 | 检查输出根目录可写、目标 package 不存在 | 目录不可写或目标已存在 |
| 候选来源一致性 | A1；PPTX/PDF 误配、静默合并 | 若传入 comparison，复核 preferred source、两候选、页码 | 候选缺失/不可读/主来源或页码不一致 |
| 局域网能力探测 | A4；mac96 不可达、模型能力漂移 | 在原生提取前记录 SSH、服务和模型状态；不可用只切换已声明 fallback | 没有 fallback 证据或把不可达写成已使用 |

## 机器规则映射

每次 preflight 的 `checks[]` 必须同时写出风险、检测边界和下一步。以下矩阵是最小可执行集合；它不把阶段内质量问题伪装成执行前通过：

| `check_id` | 风险 | `detectability` | 失败动作 | 通过后的下一步 |
| --- | --- | --- | --- | --- |
| `governance-files` | K-009/K-013 | `preflight-deterministic` | P0 阻断 | 读取 source 能力表 |
| `source-readable` | K-013 | `preflight-deterministic` | P0 阻断 | 记录 source SHA |
| `source-format-supported` | K-013 | `preflight-deterministic` | P0 阻断 | 锁定 profile/active branch |
| `page-scope-valid` / `smoke-scope-valid` | K-010/K-012 | `preflight-deterministic` | P0 阻断 | 进入 A1，保持 selected-pages 不变 |
| `source-container-integrity` | K-003/K-013 | `preflight-deterministic` | P0 阻断 | 生成 source inventory |
| `source-page-inventory` | K-010/K-013 | `stage-required` | `.ppt` 页数未知时保留 `needs-review`；`pages=all` 直接阻断 | preflight 完成后才允许 A1 Office 页数发现 |
| `required-render-tools` | K-012 | `preflight-deterministic` | P1/P0，按工具是否必需决定 | 进入 A2/A3 |
| `output-root` / `output-collision` | K-008/K-010 | `preflight-deterministic` | P0 阻断 | 使用新 package/attempt |
| `candidate-comparison` | K-007 | `preflight-deterministic` | 仅在显式候选时 P0 阻断 | 保留两份候选证据 |
| `candidate-fingerprint` | K-007/K-009 | `preflight-deterministic` | 候选路径、记录 SHA-256 与当前文件不一致时 P0 阻断 | 重新生成 comparison，禁止使用被替换的候选 |
| `authorization-policy` / `external-route-policy` | K-005/K-006 | `preflight-deterministic` | P0 阻断 | 不调用未授权 provider |

每个 check 还必须输出 `evidence_class`、`evidence_paths` 和 `next_action`。`semantic-title-quality`、`visual-relation-fidelity`、`ocr-accuracy`、语言质量和人工复核完整性只能标为 `stage-required`、`human-review` 或 `production-only`，不能由 preflight 生成 `passed`。

## 与已有 K 条目的关系

`references/known-failures.zh.md` 继续保存长期失败模式及其 `spec-known`、`needs-verification`、`fixture-covered`、`observed`、`resolved` 边界。preflight 只负责把其中可以在运行前廉价确定的部分变成阻断规则；它不把规格风险伪装成当前 Run 已发生，也不把 preflight `passed` 伪装成转换通过。

目前优先前移的风险包括：K-005/K-006 的资源与授权边界、K-007 的候选误配、K-008/K-010 的证据和重跑覆盖、K-009 的状态误报，以及 K-001/K-002/K-003/K-004/K-011/K-012 仍需在 A4–A7 和独立 validator 中确认的内容质量问题。
此外，K-013（扩展文档声明与生产 runner 不一致）已作为 `source-format-supported` 在 A1 前阻断；当前 active Workflow 输入已收敛为 PPT/PPTX、PDF 和图片，DOCX 仅保留为未接入的扩展 Skill/历史路线。

## 专家推进法

1. **先冻结边界**：确定一源一包、source SHA、页码、授权和“不调用在线视觉模型”的默认政策。
2. **风险前移**：把路径、格式、容器、工具、页码、输出碰撞、候选一致性做成 fail-closed preflight。
3. **最小纵向切片**：先用 1–2 页 smoke 证明 A1→A7 的真实链路，再按小批次推进；不让长跑掩盖首个失败。
4. **逐阶段证据**：每一阶段写入输入、状态、诊断、证据路径和下一步；fallback 只能走 Workflow 声明的路径。
5. **分层事实**：原生事实、OCR 候选、视觉关系、Unknown、读者 Markdown、manifest 分开保存；候选不能覆盖原生事实。
6. **独立判定**：runner 只负责生成，validator 重新读取 package；`passed`、`needs-review`、`failed` 的含义不能互换。
7. **修复闭环**：失败登记 K 条目，修复后保留旧 package，用新 attempt 重跑，并做局部 re-audit；只有修改前后证据齐全才可标记 `resolved`。

## 当前仍不应假装已解决的风险

- 历史 Skill/说明仍可能写有 DOCX，但当前 active runner 尚未实现；preflight 会在 A1 前阻断，直到补齐能力、契约、夹具和真实验收。
- 语言质量修复、人工复核消费端、幂等恢复和真实 mac96/Apple OCR E2E 仍是阶段内或横向的后续工作；preflight 通过不等于这些门禁通过。
- 真实来源仍必须执行独立 `reports/validation-result.json` 和必要的人工复核；fixture、静态契约和预检都不能替代真实业务证据。
- active source runner 的 deterministic execute 还必须保留 `source-inventory.json`、`capability-probe.json`、`smoke-report.json` 和 `progress.jsonl`；这些文件记录“未执行”同样是证据，不能缺失或伪造通过。
