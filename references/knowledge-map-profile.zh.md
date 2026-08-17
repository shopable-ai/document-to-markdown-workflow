# 知识地图差异策略

这份 profile 是早期 `knowledge-map-image-to-md` 技能在当前规格包中的唯一领域增量。
它不复制 Workflow A 的 A1-A7 步骤，也不重新定义 Markdown、视觉资产或 manifest
字段；通用能力和字段分别由 Workflow A、阶段 Skill 与 `contracts/` 负责。

## 1. 入口与正式主链路

```text
$knowledge-map-image-to-md
  → profile 门禁
  → document-to-markdown-assets：A1 → A2 → A3 → A4 → A5 → A6 → A7
  → Workflow A 正式资产包
```

该入口是兼容别名，不是第三个 Workflow，也不是 A1-A7 之外的新阶段。不能因为旧
技能名称仍然可用，就再维护一套独立的 OCR、VLM、Markdown 或报告实现。

## 2. 只增加知识地图特有的门禁

| profile 门禁 | 插入位置 | 目的 | 失败时 |
|---|---|---|---|
| `completed-input` | A1 之前 | 只接收已经完成且不再变化的 archive/member；排除 `.part`、`.downloading`、`.crdownload` | 记录 `skipped_partial`，不读取、不 hash、不移动 |
| `duplicate-preflight` | A1 后、A4 前 | 先以 SHA-256 处理精确重复，再把感知相似只作为复核候选 | 精确重复复用已有证据；相似候选不得直接复用 |
| `semantic-eligibility` | A3 后、A4 前 | 确认图片确实包含可保留的分类、层级或实体语义 | `blocked_unsupported` 或 `needs-review`，不消费高精度 OCR/VLM |
| `source-fidelity-trace` | A4/A5 后、A7 前 | 对高密度地图要求 line inventory、可见实体到最终节点的 trace 和冲突登记 | 不能进入 `success`，保留来源图并进入 `needs-review` |
| `append-only-run-record` | A6/A7 | 记录每个 archive/map occurrence、状态、重试和证据链接 | 运行记录不完整时不能声称批次完成 |

这些门禁是 profile 的差异要求，不替代现有阶段的 gate。当前仓库已经有通用
`asset-deduplication`、来源 occurrence、A4 reconciliation 和 A7 验收能力；若它们
没有产生 profile 所需的证据，必须如实报告缺口，而不是用“模型说完整”或 HTTP 200
代替证据。

## 3. 输出与过程层

单个来源的正式用户结果仍使用 Workflow A 的扁平资产布局：

```text
<output_root>/<package-id>/
├── index.md
├── manifest.yaml
├── assets/                 # 所有来源视觉资产；类型由 manifest.asset_kind 区分
└── reports/
```

批处理可以在 profile 自己的结果根下增加：

```text
_过程文件/       # OCR、VLM、crop、trace、duplicate review 等中间证据
运行记录/         # append-only ledger、summary、去重索引
```

这些目录不能被写入最终 Markdown，不能取代 Workflow A 的 manifest/report，也不能
把测试 fixture 当作生产结果。早期技能中 `AI知识库_转换结果/最终知识库` 的命名只
是历史布局，不应复制成通用 Workflow A 输出契约；需要该布局时由批处理适配器做
显式映射。

## 4. 精确重复、近重复与版本

- 精确 SHA-256 重复：复用 canonical 资产和证据，只新增 source occurrence，不重跑 OCR/VLM。
- 感知哈希、SSIM、embedding、文件名或布局相似：只能产生 `review_candidate`。
- 新版是等价或严格超集：选择有证据的新版为 canonical；旧版保留可恢复记录并标记 `superseded_near_duplicate`。
- 旧版有独有可见内容：只做 gap audit 和历史补充，不能重新抄写共享内容。
- 版本无法裁决：两份来源都保留，状态为 `needs_review` 或 `failed_retryable`。

任何裁决都要保留比较过的 SHA-256、方法/分数、类别重叠、独有项、canonical 选择
和理由。阈值本身不能成为删除或合并依据。

## 5. 旧技能到当前仓库的迁移表

| 早期技能内容 | 当前唯一权威位置 | 是否在 profile 重复 |
|---|---|---|
| 来源识别、原生提取、渲染、裁剪 | A1-A3 及其 Skill | 否，只引用 |
| 页面视觉理解（默认 `full + single`；高风险可 `dual`）→ 文字层 fallback | A4、`vision-page-analysis`、`local-ocr` | 否，只引用 |
| Markdown/图片链接/不确定性 | A5 及其 Skill、相关 contracts | 否，只引用 |
| manifest、命名、occurrence、资产 hash | A6 及其 Skill | 否，只引用 |
| 语义资格、精确/近重复、版本仲裁 | 本 profile 的差异门禁 | 是，唯一增量 |
| line inventory、`source_to_final_trace` | A4/A5/A7 的 profile 证据要求 | 是，唯一增量 |
| append-only ledger 与恢复索引 | profile 证据要求；实现需单独适配 | 是，唯一增量 |

## 6. 当前实现诚实边界

当前仓库的规格层已经登记入口和 profile，但不因此宣称以下能力已经存在：安全
解压 ZIP/RAR/7z、跨批次 ledger 脚本、完整 duplicate preflight、line inventory
coverage checker 或 `source_to_final_trace` writer。它们实现前，真实运行必须在对应
证据中写明 `needs-review`/`failed_retryable`，不能把规格校验或 fixture 结果升级为
知识地图批量转换证明。
