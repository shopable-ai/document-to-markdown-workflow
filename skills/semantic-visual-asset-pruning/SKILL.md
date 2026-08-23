---
name: semantic-visual-asset-pruning
description: 检查 Workflow A 或独立 LangGraph Markdown 资产包里的页面图，找出已经没必要保留的图片。先按 hash 列出锁定删除计划；只有明确说“清理”“删除”或“应用计划”时，才删除合格图片的 Markdown 引用或可删派生图；最后检查没有断链。复杂图、不确定页和原始 PDF/PPTX 一律保留。
---

# 清理冗余页面图

## 术语与边界

日常可以直接把这一步叫做“清理冗余页面图”或“清理没必要保留的页面图”。它的专业名称仍然是“视觉资产语义筛选”（semantic visual-asset pruning），也可称为“视觉资产去冗余”或“读者层视觉资产裁剪”。它是内容级资产清洗的一部分，但不等同于文件去重：`asset-deduplication` 处理重复来源/哈希，本 Skill 判断图片是否仍为读者或下游视觉任务提供增量信息。

本 Skill 只清理读者资产包，不修改原始 PDF/PPTX、不覆盖原始文字层、不把复杂关系图降级成纯文字。原始页面若需要审计，应由 A3 的渲染证据或外部来源保存；被筛掉的资产必须在 pruning report 中留下页码、路径、SHA-256、理由和状态。

## 直接触发

日常触发词：

> `清理冗余页面图`
> `清理多余的文字图片`

这两个说法都可以。需要补范围或动作时，再在后面接一句日常说明即可，例如“清理当前运行目录里的冗余页面图”“清理当前运行目录里多余的文字图片”“先预览可删页面图，不要实际删除”。如果你习惯显式 Skill 名，也可以继续说 `$semantic-visual-asset-pruning`，但日常使用不必记这个英文名。

以下自然语言也应路由到本 Skill：

- “清理冗余页面图。”
- “清理多余的文字图片。”
- “把这个运行目录里没必要保留的页面图清一下。”
- “先帮我看看哪些页面图可以删，只出计划，先别动文件。”
- “清理纯文字页截图，但复杂图和拿不准的页面先保留。”

“检查”“预览”“列出候选”只生成分类报告和 hash 锁定计划；只有“删除”“清理”“应用计划”等明确动作词才授权修改派生输出。未给出运行目录时，从当前任务已确认的单一 `run_root` 获取；存在多个候选目录时停止，不猜测。

## 处理流程

1. 读取 `manifest.yaml`、`index.md`、`evidence/content-classification.json` 和 `evidence/reconciliation.json`（存在时）。确认来源指纹一致，并建立 `page -> asset -> Markdown link` 映射。
2. 生成页码级候选。新运行优先复用 `vision-page-analysis` 的 `page_content_type`、`layout_dependency`、`semantic_visuals`、`ignored_elements` 和 `reader_image_policy`；旧包再按内容证据补判。可筛选的典型页面包括封面/标题页、目录页、以及文字层已完整且没有图形/空间关系增量的说明页。复杂图、表格关系、箭头、颜色编码、层级嵌套、流程和可复用布局默认保留。二维码直接作为 `ignored_elements`，不解码、不裁剪、不生成独立图片资产。
3. 对每个候选记录 `decision`、`reason_code`、`evidence_paths` 和 `confidence`。不能由证据区分“纯文字”与“关系图”时必须是 `needs-review`，不能自动删除。
4. 已在首次页面投影时由结构化视觉 JSON 明确决定 `omit` 的页面无需再做“删除”；本 Skill 的预览/`--apply` 只用于旧包或后置复核。后置修改仍只有用户明确要求清理或命令带 `--apply` 时才执行。
5. Workflow A manifest 包应用时只移除被批准页面在 `index.md` 的图片行和对应 section 的 `visual_asset_ids`。必须保留 `assets/` 中的来源文件、manifest 的 `assets` 条目、`source_occurrences`、`media.sha256` 和 `evidence/`，因为它们是 A3/A6 审计与后续修复的来源证据。
   唯一例外是用户明确要求物理删除、且对应 `page-units/pNNN.md` 已声明 `type: divider` 的无语义章节分隔页：可以删除该来源文件，但不得改写原始 manifest；pruning report 必须记录原 SHA-256、页码、资产 ID、`source_asset_action: deleted-by-explicit-user-request` 与同步结果，独立 validator 仅对这组精确记录放行。普通页面、纯文字页或缺少显式授权的页面仍不得删除来源文件。
   独立 LangGraph 简化包没有 manifest，`assets/page-NNN.png` 是可重建的派生页面图。用户明确授权物理清理时，必须先生成 hash 锁定计划，再用同一计划和 `--delete-source-files` 删除；同时精确删除 `page-units/`、`index.md` 和其他根级合并 Markdown 的对应整行图片引用。删除后 validator 通过 pruning report、plan 和 classification 三段哈希链验证有意缺失的 PNG，不把它误报为断链。
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

对 LangGraph 简化包应覆盖当前全部页面；脚本先读 compact page evidence 中已有的 `visual_analysis.reader_image_policy`、`page_content_type`、`layout_dependency`、`semantic_visuals`、`relationships`、`tables_or_charts` 和 `unknown_forms`。只有字段不足时才沿用同一 mac96 模型、接口、请求参数和提示词做 fallback。`--resume` 会逐页持久化并只复用图片 SHA-256 一致的完成记录。

```bash
python3 skills/semantic-visual-asset-pruning/scripts/classify_visual_assets_mac96.py \
  /absolute/path/to/langgraph-run --pages 1-29 --resume \
  --output evidence/visual-asset-classification-mac96.json

python3 skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py \
  /absolute/path/to/langgraph-run \
  --classification-report evidence/visual-asset-classification-mac96.json \
  --plan evidence/visual-asset-pruning-plan.json \
  --delete-source-files

python3 skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py \
  /absolute/path/to/langgraph-run \
  --apply-plan evidence/visual-asset-pruning-plan.json \
  --delete-source-files
```

LangGraph 删除门固定为：`decision=candidate-remove`、`semantic_text_coverage=complete`、`visual_increment=none`，且全部理由只能属于 `cover`、`table-of-contents`、`text-covered`。`retain`、`needs-review`、缺字段、字段冲突或任何复杂关系一律不删除。结果写入 `evidence/visual-asset-pruning.json`；只有图片缺失、引用清零、保留图片哈希一致、Markdown 非图片内容不变、复杂图保留且图片数差值吻合时，状态才是 `passed`。
