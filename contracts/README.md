# 工程 YAML 与人类说明

[CONTRACTS.md](CONTRACTS.md) 是活跃工程 YAML 的人工目录。它不定义业务需求；requirements Markdown 说明“用户要什么”，本目录说明“工程精确字段如何被人理解”。

治理范围包括 contracts/*.yaml、根部三个 registry 和 workflows/*/workflow.yaml。Skill UI metadata、fixture、历史 Run、prompt 参数和 archive snapshot 不作为独立活跃工程合同；它们由上位合同、测试或历史边界管理。

每个受治理 YAML 必须有 id、version、human_reference 和 lifecycle_status。卡片与 YAML 的 ID/版本或链接不一致时，validator 报 contract-doc-drift。

