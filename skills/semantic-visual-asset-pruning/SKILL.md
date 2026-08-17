---
name: semantic-visual-asset-pruning
description: 对文档转 Markdown 资产包执行视觉资产语义筛选：识别封面、目录和已被可靠文字/结构覆盖的纯文字页面图，保留复杂图示与关系证据，并在显式批准后只移除读者层图片链接与 section 绑定，保留来源资产登记。
---

# 视觉资产语义筛选

## 术语与边界

这一步的专业名称是“视觉资产语义筛选”（semantic visual-asset pruning），也可称为“视觉资产去冗余”或“读者层视觉资产裁剪”。它是内容级资产清洗的一部分，但不等同于文件去重：`asset-deduplication` 处理重复来源/哈希，本 Skill 判断图片是否仍为读者或下游视觉任务提供增量信息。

本 Skill 只清理读者资产包，不修改原始 PDF/PPTX、不覆盖原始文字层、不把复杂关系图降级成纯文字。原始页面若需要审计，应由 A3 的渲染证据或外部来源保存；被筛掉的资产必须在 pruning report 中留下页码、路径、SHA-256、理由和状态。

## 处理流程

1. 读取 `manifest.yaml`、`index.md`、`evidence/content-classification.json` 和 `evidence/reconciliation.json`（存在时）。确认来源指纹一致，并建立 `page -> asset -> Markdown link` 映射。
2. 生成页码级候选。可筛选的典型页面包括封面/标题页、目录页、以及文字层已完整且没有图形/空间关系增量的说明页。复杂图、表格关系、箭头、颜色编码、层级嵌套、流程和可复用布局默认保留。
3. 对每个候选记录 `decision`、`reason_code`、`evidence_paths` 和 `confidence`。不能由证据区分“纯文字”与“关系图”时必须是 `needs-review`，不能自动删除。
4. 先运行脚本的预览模式检查报告；只有用户明确要求清理或命令带 `--apply` 时才应用删除。
5. 应用时只移除被批准页面在 `index.md` 的图片行和对应 section 的 `visual_asset_ids`。必须保留 `assets/` 中的来源文件、manifest 的 `assets` 条目、`source_occurrences`、`media.sha256` 和 `evidence/`，因为它们是 A3/A6 审计与后续修复的来源证据。
6. 运行独立包验收器。任何断链、哈希/来源不一致或剩余 section 只有图片没有语义内容时，状态为 `needs-review` 或 `failed`。

## 可重复命令

```bash
python3 skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py \
  /absolute/path/to/package \
  --exclude-pages 1,2,23-27

python3 skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py \
  /absolute/path/to/package \
  --exclude-pages 1,2,23-27 --apply
```

默认是预览模式，会输出 JSON 并写入 `evidence/visual-asset-pruning.json`；`--apply` 只改写读者层链接和 section 绑定，不删除来源文件或 manifest 资产。页码只是当前复核结论，跨来源复用时必须重新根据证据决定，不能复制页码白名单。

## 失败与保守规则

- 找不到 manifest、来源指纹或页码映射时停止，不猜测路径。
- 图片包含图形关系、布局编码或下游重建价值时保留，即使 OCR 已能读出全部文字。
- 纯文字候选缺乏独立证据时记录 `needs-review`，不删除。
- `--apply` 失败时保留原文件，并在报告中记录未完成动作；不做部分静默删除。若来源资产、manifest 登记或 source occurrence 缺失，必须停止。

## 交付证据

报告至少包含 `status`、`mode`、`source_fingerprint`、`excluded_pages`、`kept_pages`、每个被筛资产的 `asset_id`/`file_path`/`sha256`/`reason_code`/`evidence_paths`，以及 `index.md` 是否更新、`manifest.assets`/`source_occurrences` 是否保留、section 绑定是否同步。该报告是 A5/A6 的补充证据，不替代 A7 独立验收。

## mac96 批量分类

对未获用户批准的候选页，使用 `scripts/classify_visual_assets_mac96.py` 通过 SSH 将页面图逐页发送到局域网 `mac96` 的 `qwen3-vl:30b-a3b-instruct`。脚本只生成候选报告，不调用 pruning 的 `--apply`；模型输出必须是 `retain`、`candidate-remove` 或 `needs-review`，并带有 `cover`、`table-of-contents`、`text-covered`、`diagram`、`table`、`flow`、`color-coded-structure` 等理由。传输失败、响应不可解析或模型无法区分文字与空间/关系编码时，记录 `needs-review` 并保留页面。

```bash
python3 skills/semantic-visual-asset-pruning/scripts/classify_visual_assets_mac96.py \
  /absolute/path/to/package --pages 23-27 \
  --output evidence/visual-asset-classification-mac96.json
```

候选报告不改变 `index.md`、`manifest.yaml` 或来源资产；只有用户明确批准的页码才可另行运行 pruning 脚本的 `--apply`。
