# 继续：视觉资产语义筛选

## 原始需求

使用当前“文档转 Markdown”工作流处理真实资产包：

`/Users/a0000/Documents/workspace_agent/workflow/workflow-document-markdown-visual-assets/runs/real/real-runs-20260814-independent-sources-v6/21-1130abf8-pdf`

Markdown 已提取可靠文字后，封面、目录和其他只有简单文字、没有额外视觉信息的页面图不应继续出现在读者层 Markdown。用户问这一环节的专业名称，并要求缺失时创建对应 Skill。

已明确确认必须移除读者层图片链接的只有：

- 第 1 页封面图（文档顶部图片）
- 第 2 页 `## 目录` 的图片

第 23–27 页曾被作为“纯文字页”候选处理，但用户尚未再次明确批准；本轮不得删除其读者图片链接或来源资产。

## 本轮目标与验收

目标：完成“视觉资产语义筛选（semantic visual-asset pruning）”能力，并将当前包的第 1、2 页图片从 `document.md` 的读者层移除；复杂图、关系图、流程图、颜色或空间编码页面必须保留。

验收：

- `document.md` 不再引用第 1、2 页页面图；第 3–33 页当前链接保持完整，尤其第 23–27 页必须仍存在。
- `manifest.yaml` 与 `assets/pages/` 保留所有来源页资产和 source occurrence，确保审计/修复引用不被破坏。
- `evidence/visual-asset-pruning.json` 记录第 1、2 页的页码、原文件 SHA-256、理由、来源指纹和同步结果。
- `python3 tests/validate_real_output_root.py <package>` 与 `python3 adapters/workflow_a_output_validator.py <package>` 都不得因 pruning 产生断链、页覆盖或来源追溯错误。
- `python3 tests/validate_workflow_package.py` 与项目规定 pytest 命令通过。

测试阶段：当前处于测试与完善阶段。每次测试都记录结果；发现问题后重新梳理原始需求、实现范围和验收标准，完成修正并复测，直到验收证据成立。

## 任务拆分

- 1. 收敛当前真实包
  - 1.1 先检查 package 的 33 个 assets、33 条 manifest 资产、33 个 Markdown 图片链接是否均已恢复；当前状态尚未在恢复后复验。
  - 1.2 运行 `skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py <package> --exclude-pages 1,2 --apply`。
  - 1.3 确认该脚本只移除读者层链接和 section 的 `visual_asset_ids`，保留 `assets/pages/` 与 manifest 的来源资产登记。
  - 1.4 更新/核对 `evidence/visual-asset-pruning.json`，只列第 1、2 页。
- 2. 完成可复用工作流能力
  - 2.1 审查并完成新 Skill、registry、契约、Workflow A 的 A5/A6 说明和验收规则。
  - 2.2 为 pruning 脚本补充 fixture/单元测试，至少覆盖 preview、apply、断链保护和来源资产保留。
  - 2.3 验证 `agents/openai.yaml` 与 Skill frontmatter。
- 3. 为未确认的纯文字页建立本地批量判定路线
  - 3.1 使用现有 mac96 局域网视觉模型，而不是把整批图片输入当前对话。当前包已有 `evidence/mac96-visual-review.json`，目标为 `mac96`、模型为 `qwen3-vl:30b-a3b-instruct`，但现有提示词只做关系复核，不能直接作为删图结论。
  - 3.2 新建专用的本地视觉分类提示词/脚本：每页输出 `retain`、`candidate-remove` 或 `needs-review`，并给出 `cover`、`table-of-contents`、`text-covered`、`diagram`、`table`、`flow`、`color-coded-structure` 等理由。只有 `retain/candidate-remove` 的依据可读，且用户批准后才调用 `--apply`。
  - 3.3 先对第 23–27 页生成候选报告，不能自动删除。

## 当前状态与证据

- 已确认：专业名称采用“视觉资产语义筛选（semantic visual-asset pruning）”；它属于内容级资产清洗/视觉资产去冗余，不等同于已有 `asset-deduplication` 的哈希/出现记录去重。
- 已创建：`skills/semantic-visual-asset-pruning/SKILL.md`、`skills/semantic-visual-asset-pruning/scripts/prune_visual_assets.py`、`skills/semantic-visual-asset-pruning/agents/openai.yaml`。
- 已修改：`skill-registry.yaml`、`contracts/visual-asset-pruning.yaml`、`contracts/markdown-asset-package.yaml`、`workflows/document-to-markdown-assets/workflow.yaml`、`README.zh.md`、`ACCEPTANCE.zh.md`。
- 已修改：`tests/validate_real_output_root.py` 对仅含 optional/legacy evidence 的 manifest 降为 warning；`adapters/workflow_a_output_validator.py` 已修正 Markdown 尖括号图片路径的解析。
- 已确认：真实包的第 1、2 页是封面和目录；第 3–22、28–33 页含图形、流程、层级、颜色或空间结构而保留。
- 已中断：曾错误地把第 23–27 页纳入 apply 候选，随后已重新渲染并恢复这 7 张 PNG，并通过结构化修复将 manifest 资产恢复到 33 条、Markdown 图片链接恢复到 33 条。恢复后尚未重新运行验证。
- 待核实：当前 package 的 `evidence/visual-asset-pruning.json` 和处理报告仍可能遗留旧的“第 1、2、23–27 页”记录，必须由第 1 步的只删第 1、2 页 apply 覆盖。

## 约束与未知项

- 不修改原始来源 `/Users/a0000/Downloads/21个成长思维模型.pdf`。
- 读者层删图不等于删除来源资产：保留 `assets/pages/`、manifest asset 和 `source_occurrence`，否则 OCR 修复记录和 A3 页面覆盖会失效。
- 不创建分支、工作树，不 reset/clean/stash，不回退用户已有的未提交修改。
- 真实运行目录在 Git ignore 范围内；仍必须直接验证该路径，不能用 fixture 代替。
- 未确认页面的自动分类只能形成候选；没有用户批准不得删除。

## 恢复起点

停止位置在“已恢复全量来源资产和 33 条 Markdown 图片链接、尚未仅对第 1/2 页 apply”的边界。先运行下面的只读核查，确认 33/33/33 状态后再 apply：

```bash
python3 - <<'PY'
from pathlib import Path
import re, yaml
p = Path('runs/real/real-runs-20260814-independent-sources-v6/21-1130abf8-pdf')
m = yaml.safe_load((p / 'manifest.yaml').read_text())
print(len(list((p / 'assets/pages').glob('*.png'))))
print(len(m['assets']))
print(len(re.findall(r'^!\\[', (p / 'document.md').read_text(), re.M)))
PY
```

## 交付要求

- 提供当前真实包的最终删图结果和两份独立验收输出摘要。
- 提供新增 Skill/契约/工作流文件列表和针对第 1、2 页的 pruning report。
- 提供新的本地 mac96 批量分类提示词或脚本入口，但不要把未确认候选写成已删除。

## 排除内容

- 不要继续沿用“直接删除 `assets/pages/` 或从 manifest 移除来源资产”的旧实现；它违反现有 `contracts/ocr-layout-repair.yaml` 的来源保留约束，并会破坏 A3/A6 追溯。
- 不要把当前对话的手工缩略图判断或长工具日志当作未来批量判定方法；后续应使用局域网视觉模型的结构化报告和确定性同步脚本。
