# 测试运行记录与 Workflow 结果的区别

## 先区分两类文件

`tests/runs/<run-id>/` 是测试运行记录，不是 PPTX/PDF 转换后的 Markdown 资产包。

Workflow A 真正成功时，结果必须写入用户指定的 `output_root`，目录至少包含：

```text
<output_root>/<package-name>/
├── index.md
├── manifest.yaml
├── reports/
│   ├── processing-report.yaml
│   ├── acceptance-report.yaml
│   └── failure-report.yaml
└── assets/                 # 扁平保存所有实际生成的视觉资产
```

只有这类目录才是“PPT/PDF → Markdown + 视觉资产”的业务结果。契约见 `contracts/workflow-a-output-layout.yaml`。

## 测试运行记录

普通测试运行的生成文件不写回 `fixtures/`，统一保存到：

```text
tests/runs/<run-id>/
```

其中：

- `test-report.yaml`：测试参数、输入、断言、证据等级和产物索引；
- `summary.zh.md`：人类可读摘要；
- 若实际调用某个 Workflow，可按需要保存 `stages/A1/`–`stages/A7/` 或 `stages/B1/`–`stages/B7/`；这只是测试输出分目录，不代表新增测试 Workflow；
- `outputs/`：仅当测试真的调用被测 Workflow 并生成业务结果时，保存该次测试产生的临时结果；它仍不是正式交付目录；
- `logs/`：命令和错误日志；
- `prompts/`：本次实际使用的提示词副本。

当前已保存一份普通夹具契约测试记录：

`tests/runs/fixture-contract-20260814-0118/`

其中的 `outputs/document-asset-package/` 是手工维护的 `fixture-sample`，用于验证预期的 Markdown 链接、manifest 资产路径和报告形状；它不是解析器自动生成的业务结果。由于本次只运行夹具契约测试，没有执行完整解析器或 GPT-image-2，`generated_business_outputs` 必须为空。
