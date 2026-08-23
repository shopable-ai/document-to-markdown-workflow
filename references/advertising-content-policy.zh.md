# 广告与推广内容后置策略

本策略只处理“广告分类及后续处置”，不参与基础文字识别，也不向视觉提取提示词注入删除、隐藏、
净化或改写指令。它的首要目标是让来源中的微信号、二维码说明、推广语和其他异常内容可追溯。

## 1. 阶段边界

- `text-recognition`：忠实识别所有可见文字。不得因为像广告而省略。
- `markdown-structure-reconstruction`：按页面证据恢复标题、段落、目录和列表。不得因为像广告而降级或删除。
- `advertising-classification-and-treatment`：读取前两阶段的不可变输出，另写分类和处理建议。

分类输出不是来源事实，也不得覆盖 raw OCR、模型原始响应、page unit 或页面图。

## 2. 候选分类

分类器可以输出下列候选，但必须附来源 span、页面、理由和置信度：

- `contact-information`：微信号、手机号、邮箱、群号等联系信息；
- `qr-code-adjacent-copy`：二维码附近可见说明文字；二维码图形本身不解码；
- `promotional-call-to-action`：加群、领取、购买、关注、联系等明确行动号召；
- `publisher-or-author-branding`：出版者、作者或账号标识；
- `document-content`：属于正文、案例或教学内容；
- `unknown`：当前证据不足，不能分类。

“出现微信”“出现号码”“靠近二维码”只能产生候选，不能单独证明它是应删除的广告。

## 3. 处理模式

本轮固定为 `preserve-only`：

- 不删除、不隐藏、不替换、不改写；
- 不从最终 Markdown 静默移除；
- 允许在独立 evidence 中记录 `candidate_classification` 和 `recommended_action`；
- 当前真实文档不执行广告清理，即使发现候选也只留证据。

未来模式必须显式开启，并保留处理前后 diff：

- `classify-and-mark`：正文仍保留，另加非破坏性标记；
- `hide-in-reader-view`：只改变派生读者视图，raw 与工程层保持不变；
- `delete-from-derived-view`：只允许对派生副本执行，必须有人或已批准规则授权；
- 任何模式都不得修改 source、页面图、raw OCR/视觉结果或原始 manifest。

## 4. 二维码边界

- 二维码不作为读者层语义图片，不裁剪、不解码、不生成链接。
- 二维码附近实际可见的文字仍必须进入文字识别和 Markdown 结构层。
- `ignored_elements` 只能描述二维码图形的读者图片策略，不能成为删除邻近文字的理由。

## 5. 最小证据

后置分类记录至少包含：`policy_version`、`mode`、`source_fingerprint`、`page`、
`source_span`、`candidate_classification`、`confidence`、`reason`、`recommended_action`、
`applied_action` 和处理前后 SHA。`preserve-only` 下 `applied_action` 必须是 `preserve`，
处理前后 SHA 必须相同。
