# 来源无关 Runner 设计

本文件定义新的 `adapters/workflow_a_source_runner.py` 边界。它是 Workflow A 的通用执行器接口，负责把当前 source、profile、页码范围和输出目录编成可追溯的执行计划，再按显式阶段门启动后续能力。它不包含任何具体文档的标题、术语、页码或内容分类规则。

## 当前状态

| 项目 | 状态 | 本轮含义 |
|---|---|---|
| `workflow_a_source_runner.py` | `active-reader-only-execute` | 单一 source 默认生成 `index.md` 与可选的被引用语义图片；纯文本结果无 assets/，完整 manifest/evidence/reports 仅由显式 `--engineering-output` 生成 |
| 旧 document-profile runner | 已删除 | 不参与当前 Workflow；历史证据按已有 package 直接审计 |
| `workflow_a_dispatcher.py` / `workflow_a_batch.py` | `optional-or-blocked` | dispatcher 只处理显式 bounded envelope；batch 仍 fail-closed，不是普通单源入口 |
| 图片资产视觉校准路线 | `planned` | 高质量在线基准 → 本地模型复现 → 质量比较 → 达标后降本；本轮不调用 |
| 真实 source 转换 | `not-run` | 不创建新的真实运行证据，不把历史旧运行目录升级为当前结论 |
| 测试 | `passed` | reader-only 默认输出、engineering opt-in、真实输出结构守卫和规格包校验均已回归 |

## 参数边界

```text
workflow_a_source_runner.py
  --source <one-file>
  --output-root <dir>
  --pages <all|page|comma|range>
  --profile <profile.yaml>       # 可选；默认 Workflow A generic profile
  --config <config.yaml>         # 可选；只覆盖允许的 profile 字段
  --candidate-comparison <json>  # 可选；必须显式声明同一逻辑文档候选
  --plan-only                    # 只输出 route plan，不读取 source 内容
  --execute                      # 默认 reader-only：index.md + 可选被引用语义图片
  --engineering-output           # 显式生成完整工程登记与诊断包
```

`--execute` 与 `--plan-only` 互斥。单次运行只接受一个 source；批处理由上层编排器分别创建独立 package。profile 只能描述能力和策略，不能注入读者层标题或当前文档事实。

## 来源路由

| source type | 原生结构 | 页面资产 | 嵌入/原图 | 区域裁剪 |
|---|---|---|---|---|
| PPT/PPTX | OOXML slide、文本、shape、关系 | PPT/PPTX 自身 Office 渲染 | PPTX media relationship 无损复制 | 复杂 shape/group/chart 按需登记 |
| PDF | PDF text/layout/XObject 元数据 | 原始 PDF 页面渲染 | XObject/embedded image 无损复制 | 矢量、扫描、表格、混排按需裁剪 |
| 独立图片 | 不适用；以 image metadata 为原生层 | 原图本身 | 原图保留，不先转码 | 以父资产 + 坐标登记 crop |

reader-only 模式只生成 Markdown 实际引用的语义图片；纯文本结果不创建 `assets/`。完整资产的 `asset_id`、`asset_kind`、`source_occurrence_id`、`source_location`、`extraction_method`、`sha256` 登记仅在 engineering 模式生成。默认不提取未被引用的嵌入图或区域图，二维码也不作为图片提取。

## 内容提取阶梯

```text
原生结构/文字
  → 已验证视觉模型（仅在 profile 明确允许且有当前证据）
  → Apple Vision OCR（候选）
  → 其他 OCR（候选，必须先登记为 available）
```

视觉模型和 OCR 都不能覆盖原生层。语言修复是页面单元生成后的独立步骤：局域网语言模型只输入当前
`page-units/pNNN.md`，输出 guarded page-unit 和 `evidence/markdown-content-repair-pNNN.json`；
在线模型只在局域网失败且当前任务明确授权时回退。没有输入/输出哈希、逐项来源对照和复验时，不能声称文字优化完成。

## 组装与验收边界

1. source preflight 和 fingerprint 锁定当前 source、媒体类型、页码和输出碰撞策略；
2. profile 选择原生解析器和页面渲染策略；
3. 临时工作区生成当前 source 的关键点 Markdown 与页面截图；
4. 只把 `index.md` 和被其引用的截图复制到公开 package；默认 package 目录名使用 `来源名称`，
   仅在目录冲突时追加 `-02`、`-03`；来源哈希保留在工程元数据中，不再默认暴露到目录名；
5. 需要 raw/candidate/repair/reconciliation、manifest、reports 或质量审计时，显式切换 engineering 模式。

高质量在线模型基准、本地模型复现、质量比较和预算 fallback 只属于后期 calibration profile。它们不是通用 runner 的默认阶段，也不是本轮的执行动作。

## 迁移原则

- 将旧 runner 的 `classify_content`、固定 OCR 标题区、固定 PDF DPI、mac96/SSH/Ollama、旧 Markdown 标题组织等归入 `legacy/document-profile`；不得复制到新 runner。
- 新 runner 只提供接口和阶段编排；具体解析/渲染能力通过 `profile.source_types`、`tool-registry.yaml` 和独立 Skill 注入。
- 兼容入口可以继续生成历史格式，但必须在运行记录中标注 `runner_mode: legacy`，不能把旧包当成新 runner 证据。
