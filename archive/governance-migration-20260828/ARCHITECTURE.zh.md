# Workflow、阶段、Skill 与 Tool 的关系

```text
Workflow
└── Stage / 阶段（A1-A7 或 B1-B7）
    ├── Gate / 门禁：决定是否进入下一阶段
    ├── Skill：可复用的判断、编排或领域能力，独立 SKILL.md
    └── Tool / Adapter：确定性代码、系统能力或外部模型接口

兼容入口（不增加阶段）
└── `knowledge-map-image-to-md`
    └── 知识地图 profile → `document-to-markdown-assets` A1-A7

历史归档（不参与路由）
└── `archive/legacy-skills/knowledge-map-image-to-md/`
    └── 原始 SKILL.md + 快照哈希
```

## 当前编号的正确含义

- `A1`–`A7`：Workflow A 的七个阶段，不是七个总 Skill 文件。
- `B1`–`B7`：Workflow B 的七个阶段，不是七个总 Skill 文件。
- 每个阶段下的 `skills: [...]`：是可以独立加载和复用的 Skill；现在均有对应 `skills/<id>/SKILL.md`。
- `tools: [...]`：是实现 Skill 时调用的工具或适配器；现在由 `tool-registry.yaml` 登记，尚未全部实现。
- `document-asset-extraction` 和 `document-visual-generation`：是面向用户/路由层的复合 Skill；它们不是替代阶段 Skill，而是入口级 Skill。已有转换结果审计不另立入口级 Workflow。
- `knowledge-map-image-to-md`：是早期名称的兼容入口，不是第二个复合流程；它只加载 `references/knowledge-map-profile.zh.md` 的领域差异，并路由到 Workflow A。
- profile 的职责是补充语义资格、重复预检、版本仲裁、来源保真 trace 和批处理运行记录要求；通用 OCR、视觉、Markdown、资产和验收规则只保留在现有 A1-A7/Skill/contract 中。
- `archive/legacy-skills/` 只保存不可执行的历史快照；它不能被 `skill-registry.yaml` 或 Workflow 阶段引用。

## 为什么不把 A1/A2…各自都命名成 Workflow

阶段之间有明确顺序、门禁和失败态，属于同一 Workflow 内的执行链路。只有当某个阶段需要被跨多个 Workflow 独立触发、拥有独立输入输出和生命周期时，才应升级为独立 Workflow。

## 为什么 XML/OCR/GPT-image-2 不全部写成 Skill

Skill 描述“如何判断和组织工作”；解析器、OCR 引擎、渲染器和供应商 API 适配器属于可替换执行组件。把二者分开后，可以替换本地 OCR 或 GPT-image-2 供应商，而不改变 Workflow 的阶段契约。

## 当前完成度

- 已完成：阶段、Skill 文件、Skill 注册、Tool 注册、契约、失败态、验收边界。
- 待实现：`tool-registry.yaml` 中标记 `implementation: planned` 的具体代码或外部适配器。
- 可选：`mac96-vision-endpoint` 不可用时，Workflow A 仍可在本地完成并标记 `needs-review`。
