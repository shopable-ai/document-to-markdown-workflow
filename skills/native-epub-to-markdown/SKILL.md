---
name: native-epub-to-markdown
description: 优先用 AnyDoc 读取可读型 EPUB；当 AnyDoc 不可用、缺少所需资产，或需要可追溯图片包时，再用本地 OPF/XHTML 解包脚本生成 Markdown 与 assets。
---

# 原生 EPUB 转 Markdown

优先借用成熟工具处理正文，再用本地脚本补足证据与资产：

1. 首选 `pnpm dlx @firecrawl/anydoc <source.epub> -o <index.md>` 生成正文 Markdown。
2. 如果需要图片资产、spine 顺序证据，或 `AnyDoc` 输出不满足交付要求，再运行 `scripts/epub_to_markdown.py`。

本 Skill 的重点是不把 EPUB 当 PDF，也不默认引入 OCR 或视觉模型；先吃掉 EPUB 自带的 `container.xml`、`*.opf`、`spine`、导航文档和 XHTML 正文能力，再决定是否需要本地兜底。

## 适用边界

- 适用于 `opf + spine + xhtml + 图片` 可读的原生 EPUB。
- 不适用于固定版扫描图书、只有整页图片的 OCR 任务、损坏 zip、或无法读取正文的 DRM 包。
- 如果 EPUB 本身就有 XHTML 正文，优先保留源结构和图片引用；不要为了“统一流程”强行改走 PDF/视觉链路。

## 输出约定

- `index.md`：优先来自 `AnyDoc` 的正文 Markdown；需要 fallback 时由本地脚本按 spine 顺序生成。
- `assets/`：当需要保留图片时，由本地脚本复制正文实际引用到的图片副本。
- `evidence/source-summary.json`：来源摘要、指纹、spine 与目录统计。
- `evidence/spine-documents.json`：逐个 XHTML 文档的标题、字符数、图片数与输出顺序摘要。
- `reports/conversion-summary.json`：包路径、状态、生成时间和主要计数。

## 执行顺序

1. 先确认 `AnyDoc` 可用，并直接对当前 `.epub` 跑一次原始 Markdown 输出。
2. 检查 `AnyDoc` 输出是否满足当前任务：正文是否完整、是否需要图片、是否需要 run-level evidence。
3. 需要补强时，再读取 `META-INF/container.xml`、OPF、manifest、spine 和导航文档。
4. 使用本地脚本补 `assets/`、`evidence/`、`reports/`，或在 `AnyDoc` 不可用时完整兜底。
5. 保存最终采用的是 `AnyDoc` 还是本地 fallback，并做最小验证。

## 常用命令

```bash
pnpm dlx @firecrawl/anydoc "/absolute/source.epub" \
  -o "/absolute/output-root/index.md"
```

需要图片包或 fallback 时：

```bash
python3 skills/native-epub-to-markdown/scripts/epub_to_markdown.py \
  --source "/absolute/source.epub" \
  --output-root "/absolute/output-root"
```

重跑同一个输出目录时显式允许覆盖：

```bash
python3 skills/native-epub-to-markdown/scripts/epub_to_markdown.py \
  --source "/absolute/source.epub" \
  --output-root "/absolute/output-root" \
  --overwrite
```

## 失败与升级

- 如果脚本报 `missing OPF`、`invalid EPUB`、`unsupported encrypted` 或正文为空，停止并保留错误，不伪造成功。
- `AnyDoc` 已支持 `epub` 时，应先给它一次机会；不要在可直接借用工具时默认全手写。
- 只有在 `AnyDoc` 不可用、输出缺少本任务必须的内容，或用户明确要求完全本地可追溯包时，才切到本地 XHTML 兜底。
- 不修改现有 PDF 主 runner；EPUB 处理保持为独立 skill，避免把额外分支混进当前生产主链。
