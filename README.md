# 文档资产化与 Markdown 视觉生成工作流

本仓库既是文档资产化样板项目，也是“人工治理、AI 推导、Markdown 权威、工程投影、证据闭环”架构的实例。业务需求从人类 Markdown 开始；已有 Workflow、Contract、代码、测试和历史 Run 只作为 Brownfield 工程资产，不能反向批准需求。

## 稳定阅读入口

1. [C01–C40 项目需求到工程交付主链](PROJECT-DELIVERY-WORKFLOW.md)：唯一推进主链和样板逐节点状态。
2. [需求语义权威](requirements/README.md)：目标、需求、场景、功能、行为、时序、验收和追溯。
3. [治理层](governance/README.md)：U01–U24、AI 推理、生命周期和失效传播。
4. [设计层](design/README.md)：V2/V3 后的方案、架构、工程和验证设计；当前均为 Brownfield candidate。
5. [执行层](execution/README.md)：工程任务和人工执行指南；单次 Run 状态不写入长期文件。
6. [工程 YAML 人类目录](contracts/README.md)：活跃 YAML 的 ID、版本、human_reference 和失败语义。

## 目录与职责

    /
    ├── README.zh.md
    ├── PROJECT-DELIVERY-WORKFLOW.md
    ├── requirements/
    │   ├── PROJECT-GOAL-AND-SCOPE.md
    │   ├── REQUIREMENTS.md
    │   ├── REQUIREMENT-TREE.md
    │   ├── BASELINES.md
    │   ├── BUSINESS-SCENARIOS-AND-CHAINS.md
    │   ├── FUNCTIONS.md
    │   ├── BEHAVIOR-STATE-AND-TEMPORAL-SPECS.md
    │   ├── ACCEPTANCE-AND-TEST-SPACE.md
    │   ├── TRACEABILITY.md
    │   └── discovery/
    ├── governance/
    ├── design/
    ├── execution/
    ├── contracts/
    ├── workflows/
    ├── skills/
    ├── references/
    ├── tests/
    ├── runs/
    ├── archive/
    ├── .prompt/
    └── .audit/

生命周期和默认加载范围见 [governance/README.md](governance/README.md)。迁移前根文档已按原文保存到 [archive/governance-migration-20260828](archive/governance-migration-20260828/README.md)，不参与当前路由。

## 当前基线边界

- V1 业务范围基线：pending-human-reconfirmation。
- V2 完整需求基线：blocked-by-V1。
- V3 架构基线：blocked-by-V2。

因此，requirements 中的文档资产化需求、功能、行为和验收是可审查 draft；design/execution 中的现有映射是 Brownfield candidate。当前静态文件、测试或既有实现都不能把它们升级为 confirmed。

## 现有 Workflow

- document-to-markdown-assets：来源文档到 reader Markdown 和必要视觉资产。
- markdown-to-gpt-image：一个 Markdown 单元到一张派生图片。
- knowledge-map-image-to-md：Workflow A 的兼容入口，不增加第三套阶段。
- lan-pdf-markdown-simple：显式 visual-only 的独立未注册 worker。

层级和边界见 [design/WORKFLOW-ARCHITECTURE.md](design/WORKFLOW-ARCHITECTURE.md)。机器入口在 [workflow-registry.yaml](workflow-registry.yaml)，精确阶段在各 workflow.yaml。

## 日常生产触发

日常用户只需提供：

> 文档转 Markdown：来源 SOURCE；输出到 OUTPUT_ROOT；页码全部。

执行器按 AGENTS.md 和 contracts/workflow-a-production-runtime-context.yaml 加载最小生产上下文。多个来源分别执行；只有用户明确声明同一逻辑文档候选关系时才比较。

本轮治理迁移不启动真实文档转换。实际执行说明见 [execution/WORKFLOW-A-EXECUTION.md](execution/WORKFLOW-A-EXECUTION.md) 和 [Workflow A 人工入口](workflows/document-to-markdown-assets/README.zh.md)。

## 证据与声明

Run、Evidence、Independent Verdict、Delivery 和 Bounded Claim 分离。fixture、HTTP 200、文件存在、runner succeeded 或静态测试通过都不能单独证明业务正确或生产能力。

## 规格包验证

维护完成后，从仓库根目录执行：

    python3 tests/validate_workflow_package.py
    python3 -m pytest -q tests/test_repository_layout.py tests/test_real_output_root_validator.py
    python3 -m pytest -q tests/test_document_driven_workflow_contract.py tests/test_document_governance.py

这些命令只验证规格、契约和既有测试范围；真实包仍需独立 real output validator，且本轮不运行真实转换。
