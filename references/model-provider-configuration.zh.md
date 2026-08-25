# 合并后 Markdown 模型调用配置

本文说明 `document-temp.md` 合并及标题修复之后、最终 `index.md` 交付之前的模型调用方式。正式契约见
[`../contracts/markdown-post-merge-model-routing.yaml`](../contracts/markdown-post-merge-model-routing.yaml)，可提交的路线目录见
[`../config/markdown-post-merge-models.toml`](../config/markdown-post-merge-models.toml)。

## 配置分层

配置分为三层，优先级为命令行参数、已导出的进程环境、仓库根目录 `.env`。`.env` 只补充进程中尚未存在的变量，
不会覆盖调用方已经导出的值，也不会执行 shell 表达式。

- `.env`：机器地址、模型覆盖、profile 名称和密钥；被 Git 忽略。
- `config/markdown-post-merge-models.toml`：route ID、transport、默认路线顺序；可提交且不得包含密钥。
- `~/.codex/config.toml` 与 `~/.codex/<profile>.config.toml`：Codex 官方或非官方 provider、认证和 base URL；属于用户级配置，不进入仓库。

从 [`.env.example`](../.env.example) 开始配置。未创建 `.env` 时，默认模式是 `deterministic`，不会产生模型调用或费用。

## 三种运行模式

```dotenv
MARKDOWN_FORMATTER_MODE=deterministic
MARKDOWN_FORMATTER_MODE=model-optional
MARKDOWN_FORMATTER_MODE=model-required
```

- `deterministic`：仅运行保守规则格式化，适合离线、测试和明确的 `--no-model` 路线。
- `model-optional`：按 route 顺序调用；全部失败时记录原因并使用确定性格式化，最终文件仍可交付。
- `model-required`：必须有一个模型 route 成功且通过结构守卫；失败时不替换已有目标文件。

`MARKDOWN_FORMATTER_ROUTE=auto` 使用 TOML 中的 `default_route_order`。指定 route ID 时只调用该 route，失败即停止，
不会静默切换到其他 provider。

## 局域网模型

默认自动路线先尝试 `lan-ollama`：

```dotenv
MARKDOWN_FORMATTER_ROUTE=auto
MARKDOWN_LAN_BASE_URL=http://shopme.local:11434
MARKDOWN_LAN_MODEL=qwen3.6:latest
```

兼容 OpenAI Chat Completions 或 Responses 的局域网服务可分别指定：

```dotenv
MARKDOWN_FORMATTER_ROUTE=lan-openai-chat
MARKDOWN_OPENAI_COMPATIBLE_BASE_URL=http://host:port/v1
MARKDOWN_OPENAI_COMPATIBLE_MODEL=model-id
MARKDOWN_OPENAI_COMPATIBLE_API_KEY=
```

Responses 路线把 route 改为 `lan-openai-responses`。API key 为空时不发送 `Authorization` 请求头。
仓库内三条 `lan-*` route 固定使用 `proxy_policy=direct`，避免系统 `HTTP_PROXY` 或不兼容的
`NO_PROXY=*.local` 规则把局域网请求错误转发到外部代理。非 LAN provider 默认继承环境代理策略。
LAN Ollama 默认关闭 thinking，并把单块最大输出限制为 16384 token；单块输入按页面边界控制在约 12000 字符。
完整通用提示词继续作为治理来源，实际分块调用使用
[`prompts/markdown-post-merge-formatting-runtime.zh.txt`](prompts/markdown-post-merge-formatting-runtime.zh.txt) 的精简等价投影。

如果直连不可用，`auto` 会在 Codex 之前尝试 `lan-ollama-ssh`：通过 `ssh mac96` 在远端访问
`http://127.0.0.1:11434/api/generate`。请求 JSON 从 stdin 传给远端 curl，不进入命令参数；SSH host 和远端端点可由
`MARKDOWN_LAN_SSH_HOST`、`MARKDOWN_LAN_SSH_ENDPOINT` 覆盖，但端点必须是 loopback HTTP `/api/generate`。

## Codex 官方或非官方 provider

Codex CLI 没有通用 `--provider` 参数。正式调用使用 `codex exec --profile <profile> --model <model>`；provider 定义位于
用户级 Codex 配置。仓库脚本只接收 profile 名称，不读取、不复制也不记录认证内容。
这一边界与 [Codex 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) 中的用户级 provider 配置规则一致。

```dotenv
MARKDOWN_FORMATTER_ROUTE=codex-default
MARKDOWN_CODEX_PROFILE=official
MARKDOWN_CODEX_MODEL=gpt-5.4
```

如果非官方 provider 已在另一个用户级 profile 中配置，只需把 `MARKDOWN_CODEX_PROFILE` 改为该 profile 名称。
两个变量都留空时，`codex-default` 会新启动一次临时 Codex CLI 运行并继承当前机器的默认用户配置。

这里的“默认 Codex”不是当前对话本身。普通子脚本无法回调正在运行的这一次对话；`current-conversation` route 会明确失败，
要求由当前任务处理并通过独立导入步骤交回结果，防止递归调用被误报为成功。

## 执行与验证

正常 Workflow 会自动调用格式化器，也可以单独验证：

```bash
python3 adapters/markdown_post_merge_formatter.py \
  --run-root "/absolute/path/to/run" \
  --source "/absolute/path/to/run/document-temp.md" \
  --destination "/absolute/path/to/run/index.md" \
  --mode model-optional
```

长文档按页面标记分块，单个页面不会被拆开。每个分块都记录 route、transport、model、profile、耗时和结果；报告不记录密钥。
任何模型输出只要改变页面标记顺序、图片路径、文档长度异常或破坏代码围栏，就会被拒绝并进入下一条 route。
合规模型输出还会经过一次确定性保守归一化，确保基础断行、重复纯文本和空行整理不会因模型原样返回而失效。

验收命令：

```bash
python3 -m pytest -q tests/test_markdown_model_router.py tests/test_markdown_post_merge_formatter.py
python3 tests/validate_workflow_package.py
```
