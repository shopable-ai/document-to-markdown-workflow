# 资产命名与去重策略

## A6 的双名制

A6 不再把“中文显示名”和“物理文件名”混为一谈：

- `file_name` 是唯一落盘文件名，必须是 ASCII 小写英文 kebab-case 分段，供路径、脚本和 Markdown 链接使用；
- `display_name` 是读者看到的中文语义名，供目录、说明和检索使用；
- `asset_id` 是包内稳定程序 ID，不能从文件名反推，也不要求暴露在文件名中；
- `original_filename` 只作为来源证据保存，不能作为命名依据或去重依据。

### 稳定英文文件名格式

```text
<source-slug>__p<page-3-digits>__<asset-kind>__<semantic-slug>__<purpose-slug>__o<ordinal>.<ext>
```

示例：

```text
work-models__p024__page-render__leadership-3357__original-page__o01.png
work-models__p025__page-render__self-vs-others__original-page__o01.png
```

各段来自 manifest 的结构化字段：

| 文件名段 | 来源字段 | 约束 |
| --- | --- | --- |
| `source-slug` | `naming.source_slug` | 人工确认的英文来源别名；未知时使用 `source-unknown`，不臆译 |
| `p024` | `source_occurrences[].page` | 页码三位补零；没有页码时使用 `p000` 并标记 `needs-review` |
| `asset-kind` | `asset_kind` 的英文枚举映射 | 只使用 `page-render`、`embedded-media`、`complex-region`、`crop`、`input-image` |
| `semantic-slug` | `naming.semantic_slug` | 来自已确认的内容标签；不能把原始文件名直接清洗成 slug |
| `purpose-slug` | `naming.purpose_slug` | 例如 `original-page`、`reference-crop`、`embedded-image` |
| `o01` | `source_occurrences[].ordinal` | 同一页/区域的多个资产从 01 起递增，禁止依赖目录排序 |

`source_slug` 与 `semantic_slug` 的英文含义必须来自来源元数据、人工别名表或已核对的术语；无法可靠翻译时不要猜，使用 `unknown`/`needs-review`，并保留中文 `display_name`。

文件名只允许 `[a-z0-9-]`、双下划线分隔符和原始扩展名的小写形式。禁止随机哈希前缀、空格、中文、日期猜测、平台临时名（如 `image1`、`slide_24`）和从裸文件名推断语义。

## 稳定关联与跨文件重名

- `asset_id`：包内稳定 ID，用于 Markdown 链接和程序关联；不要求等于文件名。
- `source_fingerprint`：来源文件指纹，用于区分同名来源；只进入 manifest。
- `sha256`：文件内容指纹，用于去重和完整性校验；只进入 manifest。
- `source_occurrence_id`：来源中的一次出现，用于同一图片在不同页面重复出现时分别登记。

来源出现登记必须独立于文件名：每个 `source_occurrence` 至少记录 `source_occurrence_id`、来源文件、页码/区域、对象 ID、父资产和状态。跨文件合并时按 `source_fingerprint + source_occurrence_id + sha256` 判断同一来源出现，不按裸文件名判断。只有人工确认可泛化后，才另建模板候选记录；来源资产本身不自动变成模板。

## 迁移与兼容

- 已存在的中文文件不在 A6 中静默改名；迁移时先在 manifest 增加 `file_name`，复制或重命名后再更新 Markdown 链接，并保留 `legacy_file_name` 作为一次性迁移证据。
- 新运行禁止继续生成仅有 `display_name` 的资产记录；缺少英文语义时应为 `needs-review`，而不是回退到原始文件名。
- `file_name` 冲突时，先修正结构化 `semantic_slug`/`purpose_slug`/`ordinal`；不得把 SHA-256 或随机串塞进用户可见文件名。内容哈希只用于 manifest 和去重报告。
