# Workflow A 最小测试夹具矩阵

这组夹具先解决“换一个文件是否仍然独立工作”的问题，不是生产结果，也不替代真实来源运行。

## 生成测试输入

使用现有 24–25 页小夹具作为内容来源，按元数据生成单页/两页 PPTX、单页/两页 PDF、同名候选和 PPTX 排版异常候选：

```bash
RUNTIME_PY=/Users/a0000/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$RUNTIME_PY" tests/scripts/materialize_smoke_fixtures.py \
  --output /tmp/workflow-a-smoke-matrix
```

生成结果只用于测试输入。脚本不会调用 Workflow A，也不会写入 `runs/` 或正式
`output_root`。

## 顺序

先单页，再两页，再候选比较，最后才接已有 24–25 页 fixture 和全量批次。每个 case
必须分别记录输入 SHA-256、页数、输出包、独立验收状态和失败原因。

## 关键验收

- PPTX XML 中的文字和对象关系来自当前输入；不能从 `18个工作思维模型` 或其他旧 fixture 注入。
- 同名 PPTX/PDF：PPTX XML 保留为语义/数字资产；截图差异大或 PPTX 渲染异常时，PDF 页面图作为主视觉证据。
- 排版异常候选：错误的 PPTX 页面仍需保留 XML 与 Office 渲染诊断，不能因切换到 PDF 而丢数据。
- 单页/两页错误必须在本页/本批次暴露，不能等到全量结束后只给一个总状态。
