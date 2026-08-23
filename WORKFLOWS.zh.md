# Workflow 总览

本规格包包含两个可以独立运行的 Workflow。它们不是一条强耦合流水线：Workflow A 负责把来源文档资产化；Workflow B 负责把已有 Markdown 的局部内容生成一张 GPT-image-2 图片。已有转换结果的转换保真度审计属于原工作流的后续阶段。

如果需要先理解文件、链路、Skill、Tool、契约和证据的关系，请先看 [WORKFLOW-MAP.zh.md](WORKFLOW-MAP.zh.md)。

本文件只做选择指南：第一次进入项目先读根 [README.zh.md](README.zh.md)，确定使用哪个 Workflow 后再进入对应目录的 `README.zh.md`。机器执行不读取本文件来推断阶段，唯一推荐入口由 `workflow-registry.yaml` 和对应 `workflow.yaml` 声明。

## 快速选择

| 你的输入/目标 | 使用 Workflow | 主要产物 |
|---|---|---|
| 手里有 PPT、PPTX、PDF 或图片，想提取为可检索内容并保留复杂图 | `document-to-markdown-assets` | Markdown 资产包 + 来源视觉资产 + manifest |
| 已经有 Markdown，想把其中一节变成一张知识视觉图 | `markdown-to-gpt-image` | 一张 GPT-image-2 raster 图片 + 请求/复核记录 |
| 仍在使用早期 `knowledge-map-image-to-md` 调用名 | 兼容入口 → `document-to-markdown-assets` | Workflow A 资产包 + 知识地图差异门禁；不新增 Workflow |
| 想把 A 的页面图和复杂结构提示词作为 B 的参考 | A → B（可选） | A 的视觉资产 + `visual-reconstruction-profile` 作为 B 的 `reference_assets` |
| 想自动从原文档直接批量生成图片 | 不属于当前默认路径 | 需要显式设计新的编排 Workflow，不由 A 自动触发 B |

## 两个 Workflow 的关系

```text
任意来源文档 ──→ Workflow A：document-to-markdown-assets
                         ├── index.md
                         ├── source visual assets
                         ├── manifest
                         └── visual-reconstruction-profile（复杂资产适用时）

任意 Markdown ──→ Workflow B：markdown-to-gpt-image ──→ 一张图片
                         ↑
                         └── 可选引用 Workflow A 的视觉资产

```

Workflow A 和 B 都可以脱离对方运行：

- A 不调用 GPT-image-2，不生成 PPTX/PDF；当前轮次只完成 source runner/profile 设计，A4 的 SSH/mac96/OCR/明确授权在线视觉转写都是后续 execute 阶段，不是本轮动作；
- B 不重新解析 PPTX/PDF，也不要求输入一定来自 A；
- A 的来源图默认是证据/参考资产；复杂资产可额外保存结构/提示词 profile，但不自动升级为通用模板；
- B 的成功标准是“一张可读图片”，不是一个可编辑幻灯片文件。

已有转换结果审计继续使用原工作流目录中的阶段、步骤技能和新对话提示词；不因为它是后续阶段就另立一个 Workflow。

### 早期知识地图入口的收敛方式

`knowledge-map-image-to-md` 已保留为兼容入口，但唯一主链路是 Workflow A 的 A1-A7。
早期技能中与普通文档转换重复的内容不再复制；只保留知识地图特有的完成态筛选、精确/近重复裁决、语义资格、来源保真 trace 和 append-only 运行记录要求，集中写在 [知识地图差异策略](references/knowledge-map-profile.zh.md)。

## Workflow A：document-to-markdown-assets

```text
A1 来源预检
  → A2 原生内容提取
  → A3 视觉来源资产保留
  → A4 视觉文本提取与结构复核
  → A5 Markdown 组装
  → A6 命名与来源登记
  → A7 资产包验收
```

核心原则：原生结构优先；扫描文字页将文字识别、Markdown 结构重建、广告分类及处理分层，Apple Vision OCR 加结构重建、mac96 小视觉模型和 30B 按真实页面基准选路由；复杂图必须保留视觉证据；落盘文件名使用稳定英文 `file_name`，中文语义放在 `display_name`，稳定 ID、来源出现记录与哈希放入 manifest。

已有读者层 Markdown 的页面修复采用独立页面单元覆盖层：先处理第 1 页并独立验收，
再开放第 2–5 页小批量；第 2–5 页人工复核通过后，才允许第 6–total_pages 页。页面合并按固定页序
确定性执行，`document-temp.md` 只是可审阅派生预览，不替代最终 `index.md`。

详见：

- [Workflow A 使用说明](workflows/document-to-markdown-assets/README.zh.md)
- [Workflow A 执行规格](workflows/document-to-markdown-assets/workflow.yaml)
- [Workflow A 验收](workflows/document-to-markdown-assets/ACCEPTANCE.zh.md)
- [Workflow A 执行指南](WORKFLOW-A-EXECUTION.zh.md)
- [知识地图差异策略](references/knowledge-map-profile.zh.md)

## Workflow B：markdown-to-gpt-image

```text
B1 Markdown 内容选择
  → B2 视觉用途判断
  → B3 内容与视觉结构规划
  → B4 参考资产选择
  → B5 提示词组装
  → B6 GPT-image-2 调用
  → B7 单图验收
```

核心原则：先选定一个自洽知识单元，再规划结构和密度；信息过密时返回 `needs-split`；在线阶段只生成一张图片；请求记录不得包含秘密。

详见：

- [Workflow B 使用说明](workflows/markdown-to-gpt-image/README.zh.md)
- [Workflow B 执行规格](workflows/markdown-to-gpt-image/workflow.yaml)
- [Workflow B 验收](workflows/markdown-to-gpt-image/ACCEPTANCE.zh.md)

## 证据与完成边界

当前根目录保存 Workflow 规格、契约、Skill 路由和验证层；Workflow A 的单文件 runner
按单一来源分派格式，多个来源由 `adapters/workflow_a_batch.py` 串行优先编排，fixture 的
固定页码不能外推为生产范围。

当前页面修复试运行只覆盖第 1 页：第 1 页独立验收和人工可读性检查已通过，但关系状态仍为
`needs-review`。第 2–5 页和第 6–total_pages 页均未处理；不得把第 1 页证据外推为其他页面通过。

```text
L1 规格静态校验：已具备
L1 fixture 契约测试：已具备
L2 本地解析/渲染真实运行：按 `--source` 与 `--pages` 记录
L3 mac96 视觉复核：按真实页面资产记录 SSH/结构化输出证据
L4 GPT-image-2 真实生成：待适配器、预算和图片验收证据
```

机器可读的 Workflow 对照见 [workflow-registry.yaml](workflow-registry.yaml)。包级架构见 [ARCHITECTURE.zh.md](ARCHITECTURE.zh.md)；质量评分与工程审计属于本地 `.audit/` 临时快照，不是 Workflow 入口。

横向遗漏环节和实现阶段门禁见 [references/omitted-gates.zh.md](references/omitted-gates.zh.md)。
