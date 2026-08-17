# 资产命名与去重策略

## A6 的双名制

A6 不把“中文显示名”和“物理文件名”混为一谈：

- `file_name` 是唯一落盘文件名，必须是短的 ASCII 小写英文 kebab-case 名称，供路径、
  脚本和 Markdown 链接使用；
- `display_name` 是读者看到的中文语义名，供说明和检索使用；
- `asset_id` 是包内稳定程序 ID，不能从文件名反推，也不要求暴露在文件名中；
- `original_filename` 只作为来源证据保存，不能作为命名依据或去重依据。

## 最短语义文件名格式

读者层和复制导出的图片使用：

```text
<short-semantic-slug>.<ext>
```

页码只写入 manifest/evidence；同一目录两个不同文件发生真实同名冲突时，才追加
`-02`、`-03`：

```text
life-value.png
life-value-02.png
```

页码仍然是定位信息，不是语义，只写入 manifest/evidence。

文件名只允许 `[a-z0-9-]` 和原始扩展名的小写形式。禁止：

- 来源文件名中的随机哈希或短指纹，例如 `21-1130abf8`；
- 内部资产枚举，例如 `page-render`、`original-page`、`source-media`；
- 重复定位字段，例如 `p003__page-003`；
- 默认序号，例如 `o01`；
- 中文、空格、日期猜测、平台临时名和没有语义的 `image1`、`slide-24`。

`asset_kind`、`purpose_slug`、`ordinal`、`source_fingerprint`、`sha256`、
`original_filename` 和 `source_occurrence_id` 必须进入 manifest 或复制报告，不得拼接到
读者可见文件名中。主上下文只保留短标题、候选 slug、置信度和冲突项；原图、完整 OCR
和运行日志保留在本地证据。

对于“21个成长思维模型”，人工确认的来源英文别名可写为
`growth-thinking-models`。这里使用 `growth` 表示“成长”，不使用语义不对的
`grown-thinking`。来源别名主要用于 manifest、报告或包上下文，不要求重复写入每个文件名。

## 语义来源

`semantic_slug` 只能来自：

1. 当前来源中可确认的页面标题；
2. 图片 alt 文本或紧邻说明文字；
3. 已核对的人工英文别名表；
4. 已核对的领域术语。

不能从哈希、页码、内部字段或裸原始文件名推断语义。无法可靠确认时返回
`needs-review`/`unknown`，不得静默生成随机名或技术占位名。

## 正式包与读者导出的目录边界

新版本正式 Workflow A 资产包统一使用扁平资产目录：

```text
assets/
```

页面图、嵌入媒体、复杂区域图和输入原图的类别只由 manifest 的 `asset_kind`、
`source_location` 和 `source_occurrences` 表达，不再通过物理目录表达。
`markdown-asset-copy` 导出已有 Markdown 时，必须把实际引用的图片扁平化到：

```text
assets/
```

并同步重写 Markdown 链接；新包不得生成 `assets/pages/`、`assets/regions/` 或
`assets/embedded/` 子目录。旧包仍按 legacy layout 读取，不自动迁移历史运行证据。

集合阅读包另有独立的文件命名边界：Workflow A 工程包的 `index.md` 仍是完整正文；
只有 `collection_merge` 输出中，`index.md` 才表示导航索引，`total.md` 才表示合并全文。
集合输出的逐页副本使用中文目录 `单页内容/`。旧的“只有一个 index.md 的集合目录”必须从
页面单元重新生成，不能通过重命名文件来猜测其新职责。

## 稳定关联与跨文件重名

- `asset_id`：包内稳定 ID，用于 Markdown 链接和程序关联；不要求等于文件名。
- `source_fingerprint`：来源文件指纹，用于区分同名来源；只进入 manifest。
- `sha256`：文件内容指纹，用于去重和完整性校验；只进入 manifest。
- `source_occurrence_id`：来源中的一次出现，用于同一图片在不同页面重复出现时分别登记。

来源出现登记必须独立于文件名：每个 `source_occurrence` 至少记录
`source_occurrence_id`、来源文件、页码/区域、对象 ID、父资产和状态。跨文件合并时按
`source_fingerprint + source_occurrence_id + sha256` 判断同一来源出现，不按裸文件名判断。

## 迁移与兼容

- 已存在的长文件名不能在 manifest 中被静默丢弃。迁移时先记录 `legacy_file_name`，
  再更新 Markdown 链接并复制/重命名图片，最后更新 manifest。
- 迁移只能改变容器路径和读者文件名，不改变图片内容、来源 occurrence、SHA-256 或运行 ID。
- 新运行禁止继续生成随机前缀和技术字段堆叠的读者文件名。
- `file_name` 冲突时先修正 `semantic_slug`、页码或真实 ordinal；不得把 SHA-256 或随机串
  塞进用户可见文件名。内容哈希只用于 manifest 和去重报告。
