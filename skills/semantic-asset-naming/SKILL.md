---
name: semantic-asset-naming
description: 根据来源语义、页面位置和用途生成短、稳定、可读的英文文件名与中文显示名。
---

# 语义资产命名

## 职责

根据已核对的来源语义、页面位置和资产用途生成稳定的英文文件名与中文显示名。
文件名服务于人和路径检索；`manifest` 负责完整的来源追溯。两者不能混成一串内部字段。

## 文件名规则

读者可见的资产文件名只保留最短可辨识语义：

```text
<short-semantic-slug>.<ext>
```

页码不进入文件名；同一目录中两个不同文件得到同一语义基名时，才追加 `-02`、`-03`。
不要把来源哈希、随机串或内部用途字段拼入文件名。

文件名只允许 ASCII 小写英文、数字和单个连字符。禁止来源哈希、随机串、中文、空格、
重复页码和内部实现枚举，例如 `page-render`、`original-page`、`source-media`、`o01`。

示例：

```text
time-quadrants.png
leadership-3357.png
self-control-breath.png
life-value-02.png
```

`asset_kind`、`purpose_slug`、`ordinal`、`source_fingerprint`、`sha256`、
`original_filename` 和 `source_occurrence_id` 仍然必须写入 manifest；它们不再拼接到
读者文件名中。正式 Workflow 资产包和 Markdown 复制导出都统一放入扁平 `assets/`，
资产类型由 manifest 表达，不创建 `assets/pages/` 等分类子目录。

## 输入与输出

输入：结构化资产元数据、已核对的来源英文别名、页面/区域位置、语义标签和用途。

输出：`file_name`、`display_name`、`naming` 和命名状态。`file_name` 采用上述短格式，
`display_name` 保留中文阅读语义，`naming` 保留机器追溯字段。

对于“21个成长思维模型”，自然的来源英文别名是 `growth-thinking-models`；`growth`
表示“成长”，不能写成 `grown-thinking`。来源别名主要用于 manifest、复制报告或目录
上下文，不要求暴露在每个文件名中。

## 语义来源与不确定性

- `semantic_slug` 必须来自来源标题、图片说明、人工别名表或已核对的术语。
- 不能把哈希、页码、`page-render` 或原始文件名当作语义。
- `source_slug`、`semantic_slug` 无法可靠确认时，返回 `needs-review`/`unknown`，不得
  臆译；读者层复制必须停止该图片或等待人工提供别名。
- 页码不代替内容语义，只写入 manifest/evidence。
- 同一目录中真实重名时，按导出目录中实际冲突追加 `-02`、`-03`；不能因为跨页就默认
  加页码。
- 生成命名候选时，主上下文只保留标题、短 alt、候选 slug、置信度和冲突项；原图、
  完整 OCR 和运行日志保留在本地证据，不重复塞入主对话。

## 交付要求

- 必须同时输出机器字段 `file_name` 与读者字段 `display_name`，不能用一个字段兼任两种用途。
- 不覆盖来源文件，不写入密钥或认证信息。
- 文件名迁移时先更新 Markdown 链接，再移动或复制图片，最后更新 manifest，并保留
  `legacy_file_name` 作为一次性迁移证据。

## 验证与失败

命名生成后必须按 `references/naming-policy.zh.md` 的正则、ASCII、语义来源和冲突规则校验；
同一输入重跑必须得到相同 `file_name`。发现随机前缀、技术字段堆叠、无语义回退、链接断裂
或内容覆盖时，返回明确失败或 `needs-review`。
