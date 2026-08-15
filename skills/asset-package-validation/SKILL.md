---
name: asset-package-validation
title: "资产包验收"
description: 验证 Markdown 资产包的链接、完整性、可追溯性、媒体可读性和失败态。
order: 10
---

# 资产包验收

## 职责

验证 Markdown 资产包的链接、完整性、可追溯性、媒体可读性和失败态。

## 输入与输出

输入：`output_root/<package-name>/` 中的 `document.md`、`manifest.yaml`、`assets/`、`reports/processing-report.yaml` 和 `reports/failure-report.yaml`。输出：`reports/acceptance-report.yaml`。规则：任何断链、无来源资产、缺少失败报告或缺少最终文件均不通过。

参考实现：`tests/validate_real_output_root.py`。对真实包必须单独运行该 validator；`tests/validate_workflow_package.py` 只验证规格注册表，不能替代 output-root 验收。validator 会重新读取文件并校验目录、Markdown 链接、manifest asset ID、来源 occurrence、媒体 MIME/尺寸/SHA-256、A1–A7 stage evidence、报告状态一致性和禁止秘密字段。

验收还必须检查每个 section 的阅读顺序：标题、文字和关系说明在视觉资产之前；检查 A4 是否存在开放集合未知形式扫描，且所有未知候选都有来源位置、资产链接、风险和 `Unknown`/`needs-review` 状态。没有未知形式扫描证据时，最多为 `needs-review`，不得 `accepted`。

## 边界

这是一个可复用能力 Skill，不负责决定整个 Workflow 的顺序、全局失败策略或最终业务验收；这些由所属 Workflow 和阶段门禁负责。

## 交付要求

- 输出必须包含来源、状态和可诊断错误信息。
- 不覆盖来源文件，不写入密钥或认证信息。
- 无法可靠完成时显式返回 `Unknown`、`needs-review` 或对应失败态。

## 最小字段契约

`acceptance-report.yaml` 必须包含 `status`、`evidence_level`、`checks[]`、`failures[]`、`warnings[]`、`stage_evidence[]` 和 `diagnostics[]`。至少检查四类结果文件、Markdown 链接、manifest asset ID、来源 occurrence、媒体可读性/MIME/尺寸/SHA-256、报告状态一致性和无秘密字段。

## 硬性失败

任何断链、`available` 资产缺媒体完整性、来源不可追溯、`status=succeeded` 无 outputs、stage evidence 缺失或未执行工具被标记完成，都必须返回 `contract-invalid`/`provenance-missing`/`integrity-check-failed`，不得 `passed`。

真实包验收命令：

```bash
python3 tests/validate_real_output_root.py <output_root>/<package-name>
```

该命令返回 `status=passed` 且退出码为 0 后，才允许把真实包的 A7 记录为 acceptance passed；fixture 和静态校验不能代替它。
