---
name: markdown-asset-copy
description: "复制指定 Markdown 和其中实际引用的本地图片，导出为扁平、可读、可独立携带的 Markdown 资产目录。"
---

# Markdown 资产复制

## 模式选择

本 Skill 支持两个必须显式指定的模式：

- `single_markdown`：默认模式，复制一个 Markdown 及其实际引用的图片；
- `collection_merge`：集合模式，把多个 `page-units/pNNN.md` 按页序合并为
  `total.md`，生成一个导航 `index.md`，并复制实际引用的页面图。

不能根据来源目录中是否存在多个 Markdown 自动猜测模式。集合模式的确定性适配器为：

```bash
python3 adapters/markdown_collection_merge.py \
  --source-page-units "<source>/page-units" \
  --source-assets "<source>/assets" \
  --destination-root "<destination-root>" \
  --destination-dir-name "<collection-name>" \
  --category-map "<category-map.json>" \
  --pages all
```

## 目的

把已有 Markdown 作为读者内容的唯一入口，复制：

- 指定的 Markdown 文件；
- Markdown 中实际出现并能在来源目录找到的本地图片。

这是读者层导出，不是 Workflow A 完整资产包的搬运。来源包里的 `manifest`、`evidence`、
`reports`、缓存和其他工程文件不得复制；新 Workflow A 包的 `assets/` 已经是扁平目录，
旧包中的 `assets/pages/`、`assets/regions/`、`assets/embedded/` 仅作为 legacy 输入兼容，
也不得原样暴露到导出目录。

## 输入

调用时必须明确提供：

- `source_markdown`：要复制的 Markdown 绝对路径；
- `destination_root`：目标仓库根目录；
- `destination_dir_name`：在目标根目录下新建的目录名；
- `destination_markdown_name`：目标 Markdown 文件名，可默认使用来源文件名；
- `semantic_aliases`：可选的已核对中文标题到英文语义 slug 的映射；没有可靠映射时不得臆译。

目标目录必须是扁平的读者层结构：

```text
<destination_root>/<destination_dir_name>/
├── <destination_markdown_name>
└── assets/
    └── <语义图片文件名>
```

不得生成无实际用途的 `assets/pages/`、`assets/regions/` 或 `assets/embedded/` 子目录。

### `collection_merge` 输入与输出

集合模式的独立命名契约是 [`contracts/markdown-collection-output-layout.yaml`](../../contracts/markdown-collection-output-layout.yaml)。
这里的 `index.md` 与 Workflow A 工程包中的 `index.md` 处于不同输出边界：
Workflow A 的 `index.md` 仍是转换后的完整正文；集合阅读包的 `index.md` 才是新增导航索引，
完整正文放在 `total.md`。

集合模式必须明确提供：

- `source_page_units`：包含 `pNNN.md` 的来源目录；
- `source_assets`：来源页面图目录；
- `destination_root`：目标仓库根目录；
- `destination_dir_name`：目标集合目录名；
- `category_map`：每个选定页码唯一主分类的 JSON 映射；
- `pages`：`all`、单页、逗号列表或范围。

集合模式输出：

```text
<destination-root>/<destination-dir-name>/
├── index.md
├── total.md
├── 单页内容/
│   └── p<page-3-digits>.md
└── assets/
    └── page-<page-3-digits>.png
```

`单页内容/` 是原始单页 Markdown 的读者层保留副本，文件内容和其中的相对图片链接
保持不变；`total.md` 是从这些单页内容确定性合并出的根目录阅读文件，因此它的图片
链接改写为 `assets/page-<page-3-digits>.png`。两者不能互相替代。

集合输出的文件名固定为 `index.md` 与 `total.md`，但它们的职责不同：前者是索引，
后者是完整合并正文；文档标题分别生成为“`<title>｜索引`”和“`<title>｜合并全文`”。
来源工程目录参数仍可使用 `source_page_units`，但集合导出的读者层目录统一使用中文的
`单页内容/`。旧集合包中只有 `index.md` 的布局不能直接复用，必须从原始页面单元重新生成。

集合模式的页面图使用 `preserve_page_render_names` 策略：页面渲染图保留
`page-001.png` 这种来源页码名，不把它们误命名为语义裁切图。语义资产重命名仍由
`single_markdown`/现有语义命名流程单独处理。

集合模式只负责读者层整理，不向 `index.md`、`total.md` 或集合结果自动写入
`needs-review`。`needs-review` 属于前期转换阶段门和独立验收报告，用于两页试跑、
真实失败、冲突或尚未裁决的关系；它不是集合合并的默认状态。来源页面原文中已经存在的
`Unknown`、`无法确认` 或其他待核对标记仍必须原样保留。分类表中的“待复核”只是导航分类，
不等同于 Workflow 状态。

## 复制与命名规则

1. 只读取 `source_markdown`，解析普通 Markdown 图片引用，包括：
   `![说明](relative/path.png)` 和 `![说明](<relative/path.png>)`。
2. 图片路径按 `source_markdown` 所在目录解析，只接受本地相对路径和实际存在的普通文件。
3. 每张图片先确定读者语义：
   - 优先使用图片所在 section 最近的语义标题；
   - 没有标题时使用图片 alt 文本或紧邻图片的说明文字；
   - 页码只作为位置字段，不作为语义；
   - 英文 slug 必须来自已核对的别名、来源元数据或人工确认，不得从哈希、随机前缀或
     内部技术字段生成。
4. 导出文件名只保留最短可辨识语义：

   ```text
   <short-semantic-slug>.<ext>
   ```

   页码不进入读者文件名；它已经在 Markdown 顺序、section、复制报告或上游 manifest
   中可追溯。只有同一导出目录中两个不同文件得到同一语义名时，才追加 `-02`、`-03`；
   不能默认追加页码或 ordinal。
5. 文件名只允许 ASCII 小写英文、数字和单个连字符。禁止：
   - 来源哈希或随机串；
   - `page-render`、`original-page`、`source-media`、`o01` 等内部枚举；
   - 页码或重复定位字段，如 `-p003`、`p003__page-003`；
   - 直接清洗没有语义的原始文件名。
6. 将图片复制到目标 `assets/`，并把 Markdown 中原图片链接改为
   `assets/<新文件名>`。图片链接重写后，Markdown 必须仍能在目标目录独立打开。
7. 来源原路径、原文件名、页码、SHA-256、asset kind 和 occurrence 是工程追溯信息，
   只在复制报告或上游 manifest 中保留，不拼进读者文件名。
8. 目标已有同名文件时，只有内容完全相同才允许复用；内容不同则按真实冲突追加序号，
   仍冲突时停止并报告，不覆盖。
9. HTTP(S)、`file:`、`data:`、绝对路径、越界路径、缺失图片、无法解析的图片引用或
   无法确认语义的图片直接报告明确错误；不下载、不猜测、不把它们当成已复制。
10. 目标目录之外不得写入文件。来源 Markdown 和来源图片不得修改。目标集合根目录中若已由
    桌面工作区创建 `.shopme` 或 `.docsignore`，合并器只容忍并保留它们，不读取、不复制、不生成；
    其他未声明的根目录文件仍视为目标污染并停止。

## 命名示例

对于标题为“21个成长思维模型”的来源，已核对的英文来源别名可以记录为
`growth-thinking-models`；这里使用 `growth`，不用语义不对的 `grown`。但来源别名不必
暴露在每个文件名中，因为导出目录本身已经限定了文档边界。

```text
四象限时间管理法则  →  assets/time-quadrants.png
一看就会的 GRAI 复盘法 → assets/grai-review.png
人生价值韦恩图（第 9 页） → assets/life-value.png
同名但内容不同的第二张图 → assets/life-value-02.png
```

## 完成核对

复制后只做轻量核对：

- 目标 Markdown 存在且除图片链接外内容与来源相同；
- Markdown 中每个图片引用在目标 `assets/` 中都能找到；
- 每个目标图片内容与来源图片相同；
- 所有目标图片文件名满足语义命名规则；
- 目标目录没有 `assets/pages/` 等多余图片层级或工程文件；
- 没有缺失引用、越界路径、未解释冲突或未确认语义。

## 可选模型协作

命名通常不需要把图片全部加载进当前上下文。若标题或图示语义确实不足，可以把同一批
图片、标题和页码交给局域网视觉模型，要求只返回紧凑 JSON：

```json
{"items":[{"source":"...","semantic_slug":"life-value","confidence":"medium"}]}
```

模型只提供候选；最终命名由别名表、冲突检查、文件哈希和链接校验确定。模型不可用、
输出不可解析或置信度不足时必须停止当前语义命名分支，不能把模型猜测直接写入 Markdown；
是否需要 `needs-review` 由上游转换阶段或独立验收器记录，不由本 Skill 的读者层导出结果代填。
批量一次处理多张图比逐张调用更省上下文，但不要把原图、OCR 全文和工程日志重复塞进主
对话。推荐分工是：本机负责解析、复制、哈希和链接验收；局域网视觉模型只返回短 slug
候选；主流程负责别名核对、真实冲突分配和最终落盘。模型不直接写 Markdown，也不决定
页码、哈希或 `-02` 序号。

本 Skill 不把 `passed`、`needs-review` 或 runner 的 `complete` 写入读者层文件。完成的含义
仅是：指定 Markdown 和它实际引用的图片已经按读者层结构复制，链接、内容、命名和目录核对
通过。集合模式的命令结果状态使用 `merged`，表示整理动作完成，不代表 Workflow 验收结论。

## 返回信息

至少报告：

- 目标目录绝对路径；
- 目标 Markdown 路径；
- 实际复制的图片数量、旧路径到新路径的映射；
- 使用的语义别名或无法确认语义的图片；
- 是否发现缺失引用、路径越界、文件冲突、额外目录或写入错误。

集合模式还必须报告：

- 合并页数与页码顺序；
- `index.md`、`total.md` 的绝对路径；
- `单页内容/` 保留副本的绝对路径；
- 复制的页面图数量；
- 使用的分类映射；
- 目标目录中是否存在未声明的额外文件。
