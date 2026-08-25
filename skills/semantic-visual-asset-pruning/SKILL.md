---
name: semantic-visual-asset-pruning
description: 检查 Workflow A 或独立 Markdown 资产包里的页面图，删除无引用/纯文字派生图，并把混合文字复杂页路由为最小充分裁图；hash 锁定后才换链或删除可再生工程图。原始 PDF/PPTX 与不确定资产一律保留。
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
2. 生成页码级候选。新运行优先复用 `vision-page-analysis` 的 `page_content_type`、`layout_dependency`、`semantic_visuals`、`ignored_elements` 和 `reader_image_policy`；旧包再按内容证据补判。无引用派生图和文字层已完整且没有图形/空间关系增量的页面为删除候选；页面同时含大量正文与复杂图时必须标为 `crop-required`，不得把“复杂图需保留”解释成整页工程截图永久留在 reader `assets/`。只有全页关系不可分割时才 `retain` 整页。二维码直接作为 `ignored_elements`，不解码、不裁剪、不生成独立图片资产。
3. 对每个候选记录 `decision`、`reason_code`、`evidence_paths` 和 `confidence`。决策为 `candidate-remove`、`crop-required`、`retain` 或 `needs-review`。不能由证据区分“纯文字”与“关系图”时必须是 `needs-review`，不能自动删除。
4. 已在首次页面投影时由结构化视觉 JSON 明确决定 `omit` 的页面无需再做“删除”；本 Skill 的预览/`--apply` 只用于旧包或后置复核。后置修改仍只有用户明确要求清理或命令带 `--apply` 时才执行。
5. Workflow A manifest 包应用时只移除被批准页面在 `index.md` 的图片行和对应 section 的 `visual_asset_ids`。必须保留 `assets/` 中的来源文件、manifest 的 `assets` 条目、`source_occurrences`、`media.sha256` 和 `evidence/`，因为它们是 A3/A6 审计与后续修复的来源证据。
   唯一例外是用户明确要求物理删除、且对应 `page-units/pNNN.md` 已声明 `type: divider` 的无语义章节分隔页：可以删除该来源文件，但不得改写原始 manifest；pruning report 必须记录原 SHA-256、页码、资产 ID、`source_asset_action: deleted-by-explicit-user-request` 与同步结果，独立 validator 仅对这组精确记录放行。普通页面、纯文字页或缺少显式授权的页面仍不得删除来源文件。
   独立 LangGraph 简化包没有 manifest，`assets/page-NNN.png` 是可重建的派生页面图。用户明确授权物理清理时，必须先生成 hash 锁定计划，再用同一计划和 `--delete-source-files` 删除；同时精确删除 `page-units/`、`index.md` 和其他根级合并 Markdown 的对应整行图片引用。删除后 validator 通过 pruning report、plan 和 classification 三段哈希链验证有意缺失的 PNG，不把它误报为断链。
6. `crop-required` 必须交给 `region-cropping`：优先使用 OCR 坐标、图题锚点和确定性布局，局域网视觉模型只处理低置信例外。逐区域评分至少 95，生成语义短文件名并换链；只有可再生工程页图才可在全部裁图通过、全部新图被引用后删除。
   对已经生成的 reader crop，还必须追加一次 `region-cropping/scripts/audit_text_heavy_reader_assets.py`
   的确定性“纯文字图二次门”。这一步只读当前 Markdown 引用图，不重新读取整页正文，也不逐图调用大模型。
   二次门输出 `candidate-remove`、`crop-required`、`retain`、`needs-review`：
   `candidate-remove` 才能进入删图计划；`crop-required` 继续收裁；`retain` 保留；
   `needs-review` 失败关闭。不得把“已经从整页裁成局部图”当成保留授权。
   二次门默认 `--ocr-engine auto`，优先 Apple Vision 坐标 OCR、失败或低置信时回退 Tesseract。
   自动删除资格还要求 `decision_confidence_score >= 95`、字符覆盖、顺序覆盖、OCR 可用和
   视觉增量已覆盖五项硬门全部通过；稀疏箭头/坐标轴还必须看非文字包围面积，只有简单线性顺序已在
   Markdown 中明确表达时才算覆盖。审计证据质量 95 分不能替代裁图视觉质量 95 分。
   审计只生成 hash 锁定 removal plan；物理删除必须另行显式 `--apply-plan`，并在删图前精确移除引用，
   任一哈希、引用行或硬门漂移时不修改文件。
7. 运行独立包验收器。任何断链、孤儿图、残留 `page-NNN` reader 资产、哈希/来源不一致或剩余 section 只有图片没有语义内容时，状态为 `needs-review` 或 `failed`。

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
- 图片包含图形关系、布局编码或下游重建价值时保留其最小充分视觉区域，即使 OCR 已能读出全部文字；混合正文页不得因此默认保留整页工程图。
- 纯文字候选缺乏独立证据时记录 `needs-review`，不删除。
- 纯文字图 OCR 已覆盖但上下文重叠不足、OCR 置信度不足或非文字边界不清时，也记录 `needs-review`，不删除。
- `--apply` 失败时保留原文件，并在报告中记录未完成动作；不做部分静默删除。若来源资产、manifest 登记或 source occurrence 缺失，必须停止。

## 交付证据

报告至少包含 `status`、`mode`、`source_fingerprint`、`excluded_pages`、`kept_pages`、每个被筛资产的 `asset_id`/`file_path`/`sha256`/`reason_code`/`evidence_paths`，以及 `index.md` 是否更新、`manifest.assets`/`source_occurrences` 是否保留、section 绑定是否同步。该报告是 A5/A6 的补充证据，不替代 A7 独立验收。

## 独立图片语料模式

`standalone-image-corpus`（亦称 `image-only`）用于没有 manifest、page-units 或 Markdown
引用的显式图片集合。它仍复用 `reader-asset-text-auditor` 的确定性 OCR/版面门，但不得寻找、读取或
修改任何 Markdown。此模式只接受锁定的 `input-manifest.jsonl`；首次运行可以从显式语料根生成该清单，
恢复运行必须复用原清单，运行期间新增图片只记为 `new-after-snapshot`，不得静默并入当前批次。

处理必须是单图片小事务，默认并发 1、最大并发 2：读取一条输入，验证输入 SHA-256，OCR 与结构分析，
将完整结果 append + flush + fsync 到 `classification.jsonl`；符合双重删除门时，先将
`delete-intent` append + fsync 到 `deletion-ledger.jsonl`，重新计算 SHA-256，一致才立即 unlink，
再 append + fsync 最终删除结果。每张完成后释放图片像素、OCR 响应和临时数组，不得累计整批结果、
Future 或 OCR 对象。完成索引必须落盘；`--resume` 只复用路径和 SHA 均匹配的最终状态。

如果进程在 unlink 后、成功台账前中断，恢复时必须依据锁定输入 SHA、已有分类和 `delete-intent`，把
已不存在的文件修复为 `deleted-recovered`，不得重跑整批。SHA 漂移、OCR 失败和内存压力安全退出都要
保留 checkpoint 并失败关闭。进程树 RSS 必须包含 OCR 子进程；达到阈值时先停止提交、回收 OCR，仍不安全
才退出。不得使用 launchd、keepalive 或完成后自动重启。

独立语料的物理删除不依赖 Markdown 覆盖，而依赖 `semantic_text_coverage=complete`（OCR 已可靠取得页面
重要文字）、`visual_increment=none`、确定性删除门和技能删除门同时通过。1–3 个 OCR 文字组、居中标题、
大字号、普通分段、页脚线或扫描边缘本身都不能触发保留；表格/关系线必须在正文内部有网格交叉、节点连接、
方向结构或明确对应关系。照片、插图、截图、真表格、流程图、关系图、颜色/容器/二维位置编码和空间化文字图
必须保留；主要为正文但含局部视觉区域时为 `crop-required`，本阶段没有安全裁图换链时保留原图。

正式入口：

```bash
python3 skills/region-cropping/scripts/audit_text_heavy_reader_assets.py \
  /absolute/path/to/image-corpus \
  --mode standalone-image-corpus \
  --evidence-dir /absolute/path/to/second-round-evidence \
  --prior-classification /absolute/path/to/first-round/classification.jsonl

python3 skills/region-cropping/scripts/audit_text_heavy_reader_assets.py \
  /absolute/path/to/image-corpus \
  --mode standalone-image-corpus \
  --evidence-dir /absolute/path/to/second-round-evidence \
  --resume
```

证据目录至少包含：`input-manifest.jsonl`、`classification.jsonl`、`deletion-ledger.jsonl`、
四类保留/复核/裁图/删除路径清单、`skipped-paths.txt`、`progress.json`、`execution.log`、
`summary.json` 和 `最终验收报告.md`。只有输入全覆盖、分类守恒、删除哈希链完整、保留 SHA 不变、
Markdown 未读写、没有错误/残留子进程且相关测试通过时，第二轮才可声明完成。

## mac96 批量分类

对未获用户批准的候选页，使用 `scripts/classify_visual_assets_mac96.py` 通过 SSH 将页面图逐页发送到局域网 `mac96` 的 `qwen3-vl:30b-a3b-instruct`。脚本只生成候选报告，不调用 pruning 的 `--apply`；模型输出必须是 `retain`、`crop-required`、`candidate-remove` 或 `needs-review`，并带有 `cover`、`table-of-contents`、`text-covered`、`mixed-text-visual`、`diagram`、`table`、`flow`、`color-coded-structure` 等理由。传输失败、响应不可解析或模型无法区分文字与空间/关系编码时，记录 `needs-review` 并保留页面。

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
