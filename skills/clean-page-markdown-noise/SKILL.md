---
name: clean-page-markdown-noise
description: 对逐页 Markdown 或 page-units 执行可审计的确定性页眉页脚清洗；用于清除来源各异的重复页眉、OCR 拼写变体、原始页面图链接前的页码和首尾边界分隔线，同时逐字保护正文、标题、表格、列表、Unknown/needs-review 与页面图链接。适用于“清洗 Markdown”“删除重复页眉页脚”“页眉字符每页不同”“清理页码噪声”以及进入 markdown-content-repair 前的页面文本预处理。
---

# 页面 Markdown 噪声清洗

## 执行位置

在 `page-units/pNNN.md` 生成后、`markdown-content-repair` 调用语言模型前执行。只处理 Markdown 文本，不打开页面图，不读取 OCR、bbox、Vision JSON、manifest 或其他证据文件。

遵守 [`contracts/page-markdown-noise-cleanup.yaml`](../../contracts/page-markdown-noise-cleanup.yaml)，使用 [`adapters/page_markdown_noise_cleanup.py`](../../adapters/page_markdown_noise_cleanup.py)。

## 固定顺序

1. 先用 scan 模式盘点所有选定页面的边界候选、输入哈希和疑似例外，不写文件。
2. 查看自动发现规则和 `suspects`。不同来源的页眉不能靠扩大通用正则解决；为当前来源补充 exact、regex 或 fuzzy profile 规则。
3. 使用同一 profile 加 `--apply`。适配器在任何写入前重新检查全部输入哈希，只原子重写确有删除的文件。
4. 读取证据中的删除清单、分类计数和验证项。存在 `suspects` 时保留原行并返回 `needs-review`，不得扩大规则后盲删正文。
5. 清洗通过或安全候选已应用后，再进入 `markdown-content-repair`；语言模型不得重新删除页眉页脚。

```bash
python3 adapters/page_markdown_noise_cleanup.py \
  --page-units "/absolute/path/page-units" \
  --pages all \
  --profile references/reader-noise-profile.yaml \
  --evidence "/absolute/path/evidence/page-markdown-noise-cleanup.json"

python3 adapters/page_markdown_noise_cleanup.py \
  --page-units "/absolute/path/page-units" \
  --pages all \
  --profile references/reader-noise-profile.yaml \
  --apply \
  --evidence "/absolute/path/evidence/page-markdown-noise-cleanup.json"
```

## 删除边界

- 只删除完整物理行，不删除行内子串，不改写保留行。
- 页眉只在页首安全区、页尾安全区或至少两个独立模板规则相邻形成的模板块中删除。
- 自动发现只提升跨页重复的边界短行；低频 OCR 变体必须附着到已经确认的重复模板，不能单独成为删除依据。
- 页脚页码只在原始页面图链接之前的页尾区域删除；正文编号保持不变。
- `---` 只在其他拟删除行移除后成为第一个或最后一个非空行时删除；正文内部横线保持不变。
- 精确保留页面元数据、语义标题、正文、列表、表格、代码块、`Unknown/needs-review` 和所有原始页面图路径行。

## 来源专用 profile

使用 `header_rules` 描述当前来源的模板，不把某一本书或某个品牌的文字写入通用 Workflow：

- `exact`：稳定的独立页眉行；
- `regex`：有固定锚点、章节尾词变化或多段模板被 OCR 合并成一行；
- `fuzzy`：英文 OCR 拼写变体，必须设置保守相似度。

默认 profile 只提供安全区、自动发现阈值和受保护结构。先 scan，再根据实际候选创建运行级 profile；无法确认的独立同名文字保留为 `suspects`。

运行级 profile 示例：

```yaml
version: 1.0.0
header_rules:
  - id: source-title-prefix
    kind: regex
    pattern: '^资料标题\s*[—–-]\s*.+$'
  - id: english-template-ocr
    kind: fuzzy
    value: Management Thinking Frameworks
    minimum_similarity: 0.84
  - id: source-library-label
    kind: exact
    value: 管理知识库
```

mac96 页面运行器与恢复运行器接受 `--noise-profile <profile.yaml>`，会先保存 scan 证据，再用完全相同的输入哈希与删除计划 apply，之后才调用语言修复。

## 完成条件

只有以下条件全部满足时才报告 `passed`：选定文件全部非空、输出可由输入仅删除清单中的行精确重建、原始页面图路径行逐字不变、写前哈希未漂移、写后内容等于预期输出、没有未处理的高置信残留。`passed` 只证明文本噪声清洗，不证明页面语义或视觉关系已验收。
