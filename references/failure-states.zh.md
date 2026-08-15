# 失败态与处理边界

| 状态 | 含义 | 是否可重试 | 处理 |
|---|---|---:|---|
| `source-invalid` | 来源文件不存在、损坏或无法识别 | 否，需修复输入 | 保留诊断，不创建空资产包 |
| `unsupported-format` | 类型不在支持范围 | 否 | 请求转换或扩展适配器 |
| `extract-timeout` | 原生解析超时 | 是 | 降级到分页/分块处理 |
| `render-failed` | 页面渲染失败 | 是 | 更换本地渲染器或分辨率 |
| `ocr-unavailable` | 本地 OCR 不可用 | 是 | 保留原生结果，标记视觉文字待复核 |
| `visual-review-unavailable` | mac96 或视觉复核不可用 | 是 | 不阻塞本地产出，标记 `needs-review` |
| `markdown-section-missing` | B 找不到指定 Markdown 片段 | 否，需修复选择器 | 不调用图像模型 |
| `visual-intent-ambiguous` | 无法选择主视觉结构 | 否，需补充意图 | 返回澄清所需字段 |
| `needs-split` | 单图信息密度过高 | 否，需缩减/拆分 | 不强行调用模型 |
| `provider-timeout` | 图像供应商超时 | 是 | 指数退避并保留 request_id |
| `provider-rate-limit` | 图像供应商限流 | 是 | 遵循供应商窗口，不重复轰炸 |
| `review-failed` | 图片生成但未通过验收 | 是 | 只修改规划/提示词后重试 |
| `policy-blocked` | 内容或请求触发策略限制 | 否 | 记录安全原因，不绕过策略 |
| `contract-invalid` | 阶段产物缺少必填字段、状态不一致或契约无法解析 | 否，需修复产物 | 不得进入下一阶段或写入 succeeded |
| `provenance-missing` | 事实、资产或链接无法回溯到来源指纹/页/节点/区域 | 否，需补来源证据 | 标记 failed 或 needs-review，不得静默补猜 |
| `integrity-check-failed` | 媒体不可读、尺寸/MIME/SHA-256 缺失或不匹配 | 是 | 保留失败诊断，阻止 acceptance passed |

任何失败态都必须包含：阶段、输入摘要、可重试性、错误类别和下一步；不得以空文件或“完成”状态代替失败。
