---
name: region-cropping
title: "区域裁剪与可读性增强"
description: 按显式图片范围，用 OCR 坐标和版面证据裁剪局部来源图或电子书上下文字边带，并按需生成不改变结构的可读性增强派生图。
order: 10
---

# 区域裁剪

## 职责

按已确认或候选区域裁剪局部来源图，并在有明确可读性收益时生成可回退的增强图。

## 输入与输出

输入：页面图，以及 `complex-region-detection` 或等价人工定位产生的候选区域决策；不得只给一个来历不明的裸坐标。输出：经复核的 crop/complex-region，以及可选 enhanced-raster `source-visual-asset`。规则：所有派生资产进入同一个 manifest，保留父页面关系、候选/接受坐标、参数、复核证据和 fallback。

## 范围门与上下文成本

在读取图片像素或调用任何视觉模型前先固定本轮图片集合：

1. 用户点名文件时，文件名或路径就是严格白名单。只验证这些文件存在并只处理这些文件；不得遍历、打开、
   拼接 contact sheet 或把同目录其他图片载入模型上下文，也不得为了“确认是否还有遗漏”扩大范围。
2. 用户未点名、但说明“只剩偏高图片/整页型图片”时，先只读取文件名、尺寸和宽高比，用同批尺寸分布筛出
   候选；元数据筛选不等于授权查看候选以外的图片。
3. 没有明确筛选条件时，优先复用 Markdown 图片引用、既有 crop result 和审计证据形成候选，不默认查看整个
   `assets/`。只有仍无法形成安全候选集时才返回 `needs-review`，不以批量看图代替范围判断。
4. 用户说明任务已经完成、当前只优化 Skill/规则时，不重新 OCR、不打开图片、不生成新裁图。

视觉上下文是最后一级证据，不是发现候选的默认方式。确定性路线能够给出 `no-op`、安全候选或明确
`needs-review` 时，不再调用视觉模型。

## 整页图判断

- 图片尺寸与同批页面图一致，或仍包含页眉、页码、章节标题、正文段落时，优先视为整页导出候选。
- 判断不清时保留整页图并标记 `needs-review`，不得只留下裁切候选。

## 裁切顺序

1. 验证候选 `region_id`、父页面资产、定位依据、必须保留/允许排除元素和 candidate geometry。
2. 先用保守大框保证节点、箭头、标签、括号、流程线和必要上下文完整。
3. 锁定横向边界，优先调整 `y`/`height` 去除页眉、正文残片和已独立保存的图注。
4. 只有确认两侧是纯空白时才收窄；两侧可能有关系元素时宁可留白。
5. 生成 crop preview，并与来源页或带框预览逐边对照 top/right/bottom/left。
6. 前景触边时必须说明是有意接触还是裁残；`clipped`/`unknown` 返回调整或 `needs-review`。
7. 记录 candidate/accepted `WxH+X+Y`、坐标空间、四边检查、安全边距、调整历史、父资产、实际尺寸、SHA-256 和 review evidence。

只有 `crop_decision.status=accepted`、四边均为 `clear` 或有证据的 `intentional-contact`，且
accepted geometry 与实际派生参数一致时，crop 才能 `available`。坐标合法、文件可打开或视觉上“大致像图”
都不能单独代替这个完成门。

## 坐标候选路线与成本

默认先走不消耗模型 token 的确定性路线：Apple Vision OCR 坐标用于定位“图/表”题、图内标签和正文行，
再用空白带、墨迹密度、连通区域和页面边界形成保守候选框。OCR 只提供文字及坐标，不单独证明图形边界。
无图题、横置整图、跨栏关系或确定性候选低置信时，才把当前单页缩略图交给已验证的局域网视觉模型提出
候选坐标；模型不得直接授权删除。不得为了批量方便逐页调用视觉大模型读取已经进入 Markdown 的正文。

### 电子书上下边带

电子书内嵌图或整页图的“有 OCR 文字”本身不构成裁切理由。只有同时满足以下条件，才可把文字判为可去除
的顶部/底部边带：

- OCR 返回文字框和置信度，文字框位于顶部或底部候选带，而不是图片中央；
- 边带与中央视觉内容之间存在连续空白分隔带；
- 拟删除边带内没有明显非文字结构、连线、括号、坐标轴或跨越裁切线的连通前景；
- 裁切只调整 `y`/`height`，横向保持不变，并保留安全边距；
- 中央 OCR 文字默认视为图内标签并保留，除非另有来源证据证明它是正文污染。

OCR 成功但没有检出文字时，本轮“去文字裁切”为 `retain/no-op`，不再把图片送给视觉模型寻找文字；OCR
执行失败、引擎不可用或置信度不足与“没有文字”必须分开，前者进入 `needs-review`。上下边带位置、空白带、
非文字墨迹与连通结构仍不确定时，只允许复核白名单中的当前单张图或其低分辨率 crop overlay，不得回看
同目录其他图片。

## 纯文字图二次门

一次裁图通过，不等于读者层图片就应该保留。对已经生成的 reader crop，必须再经过一次低 token 的
“纯文字图审计”：

1. 先用本机 OCR 或等价确定性 OCR 读取当前 crop 的文字、坐标和置信度。
2. 用 OCR 文字框、非文字墨迹和 Markdown 邻近上下文，判断它是 `candidate-remove`、
   `crop-required`、`retain` 还是 `needs-review`。
3. 只有 `candidate-remove` 才允许进入删图计划；`crop-required` 先继续收裁；
   `retain` 保留；`needs-review` 明确保守停下。

本目录中的 `scripts/audit_text_heavy_reader_assets.py` 是这道二次门的正式确定性入口。`--ocr-engine auto`
会先编译并复用 Apple Vision 坐标 OCR，失败或低置信时回退 Tesseract；默认模型 token 为 0。
用户点名文件时重复传入 `--asset <filename-or-package-relative-path>`，形成 fail-closed 白名单；电子书上下边带
使用 `--layout-profile ebook-edge-text`。例如：

```bash
python3 skills/region-cropping/scripts/audit_text_heavy_reader_assets.py <package-root> \
  --document index.md \
  --asset <first-image.png> \
  --asset <second-image.png> \
  --layout-profile ebook-edge-text
```

`--asset` 只匹配 Markdown 已引用的图片，不扫描资产目录；同名文件不唯一或任一白名单项未匹配时保守失败。
审计同时要求 Markdown 字符覆盖和顺序覆盖，防止“字符相同但顺序错误”被误判为正文已覆盖。
稀疏箭头、坐标轴和分隔线还要经过非文字包围面积检查，不能只凭墨迹比例低而忽略；只有简单线性顺序
已在 Markdown 中用明确箭头完整表达时，才可视为视觉增量已覆盖。只有 `candidate-remove`、
`decision_confidence_score >= 95`、全部 `deletion_gates=true` 且
`deletion_eligible=true` 才能进入 hash 锁定删图计划。局域网视觉模型只在 OCR 低置信、文字/非文字边界
不清或复杂关系疑似被误判时作为单图例外补充，并且不能直接设置删除资格。

脚本的 `audit_evidence_quality.score >= 95` 只证明来源哈希、OCR 路线、Markdown 覆盖、视觉增量指标和
fail-closed 删除门证据齐全；它不能替代下述 reader crop 视觉质量 95 分。两个 95 分门必须分别通过。
默认审计同时生成 `evidence/text-heavy-reader-asset-removal-plan.json`。物理清理只能另行使用
`--apply-plan evidence/text-heavy-reader-asset-removal-plan.json`：应用前重读 Markdown/图片哈希、原引用行、
删除分和全部硬门，先移除引用再删除文件，失败时恢复两者，并写出 removal result。

读者裁图的应用质量分不得低于 95/100。至少独立检查：视觉内容完整、图内必要标签完整、图题或理解上下文完整、
四边无裁残、正文污染可接受；任一硬检查失败时，无论总分多少都不得应用。推荐目标为 97/100，并保存分项、
复核方法和轮次。横置图允许记录 `rotate_degrees` 后再进入读者层，旋转不得改变内容结构。

## 可读性增强

黑白扫描图可以按当前工具能力使用 Gray、auto-level、`brightness-contrast 5x18/5x20`、
`sharpen 0x1.2–0x1.4` 的可配置组合。增强不是重绘：不得改变节点、边、层级、标签和文字。
增强后必须把 crop 与 enhanced 并排比较并保存证据。只有可读性确有改善且 `structure_preserved=true` 才能完成；效果变差或存在结构风险时状态为 `needs-review`，
并以原 crop 作为 `fallback_asset_id`。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

本目录中的 `scripts/apply_reader_asset_crops.py` 是 reader-only 简化包的正式裁切、重链接和受控删除入口。
它要求 hash 锁定 spec/plan、至少 95 分的逐区域复核、精确 Markdown 投影和完整回滚；只有
`source_kind=regenerable-engineering-page-render`，并且全部 crop 已生成、可打开、被 Markdown 引用后，
才删除整页工程图。原始 PDF/PPTX/独立图片永远不属于这项删除。

该脚本仍不是带 `manifest.yaml` 的 Workflow A 图级工程生命周期完整执行器：它不生成
`complex-region-detection` 定位记录、完整 `crop_decision` 或同一 Manifest 派生更新。因此它能证明
reader-only 简化包的“裁图 → 换链 → 删除可再生页图”闭环，但不能单独证明 Workflow A 的 F2–F8 闭合。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

裁剪资产必须保留 `parent_asset_id`、来源页/区域、candidate/accepted 坐标、坐标空间、四边复核、安全边距、调整历史、review evidence、MIME、尺寸、SHA-256、`status` 和诊断；坐标越界、空裁剪、父资产缺失、四边裁残或复核证据缺失进入 `integrity-check-failed`/`needs-review`。增强资产还必须记录父 crop、工具/版本、全部参数、前后比较证据、`structure_preserved` 和 fallback。

reader-only 简化包还必须保留整页工程图删除前 SHA-256、accepted geometry、可选旋转、输出 SHA-256、
Markdown before/expected-after SHA-256、全部输出被引用检查和“换链完成后才删除”检查。
