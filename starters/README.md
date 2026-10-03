# 多语言毕业项目骨架

三个项目共用 React + TypeScript、Tailwind、Jotai 和 Radix 源码组件前端，分别配 FastAPI / Hono / Gin 服务端。固定资料检索、回答、来源、请求 ID、演示/真实模式、访问码、用量与错误采用同一契约；默认无需模型密钥。

这是一条可运行的纵向切片。真实模式通过 OpenAI 兼容 Chat Completions 协议调用 DeepSeek，但本项目只用 mock 验证，没有实际调用。资料上传、数据库、账号、跨实例配额和生产部署仍是毕业实践任务，不能将骨架描述为已完成这些集成。

## 下载后的目录与运行

ZIP 包含 `frontend/`、`backend/`、`README.md`、`AGENTS.md`、`EVIDENCE.md` 与 `.gitignore`。前后端各自包含依赖锁文件和校验命令，不包含 node_modules、虚拟环境、构建产物或密钥。

前端在 `frontend/` 运行：

```sh
pnpm install --frozen-lockfile
pnpm check
node --test src/response.test.mjs
pnpm dev
```

前端地址 `http://127.0.0.1:5174`，通过同域 `/api` 代理到 `http://127.0.0.1:8010`。启动前核对端口，不停止其他项目服务。

Python / FastAPI 在 `backend/`：

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run uvicorn app:app --host 127.0.0.1 --port 8010
```

TypeScript / Hono 在 `backend/`：

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm start
```

Go / Gin 在 `backend/`：

```sh
go test -mod=readonly ./...
go run .
```

需要 Node 24 / pnpm 10.32.1；Python 3.12 / uv；Go 1.27.1。Go module 沿用平台已锁定的 Gin v1.12.0 依赖。模型 Key 不进入前端或固定资料集，访问码只保留页面会话；刷新清空本次最多 20 条运行记录。

## 共同接口

| 接口              | 行为                                                                               |
| ----------------- | ---------------------------------------------------------------------------------- |
| `GET /api/health` | 返回框架与固定资料条数                                                             |
| `POST /api/ask`   | `prompt` 为 1–1000 字符、非空；`mode` 为 `demo`（默认）或 `deepseek`；拒绝额外字段 |
| 成功响应          | `run_id`、`mode`、`answer`、实际 `sources`、已知 `usage` 或 null                   |
| 无证据            | `mode: no-evidence`、空来源和未知用量，不调用模型                                  |
| 错误响应          | 固定 `error` 字段，不返回供应商错误正文或校验时的原始输入                          |

`contract-cases.json` 在三种语言中共用，15 组案例覆盖演示、无证据、Unicode、空值、未知字段/模式、拒绝旧 `openai` 模式与授权。请求体限制 16 KiB；真实模式每实例每分钟最多 10 次模型请求，20 秒请求上限、`max_tokens: 800`、1 MB 响应与 20,000 字符回答。只接收正常停止的完整回答，截断或异常不写入前端历史；usage 只读取已知非负整数，缺失不当作零。

## 真实模型与后续毕业任务

只在服务端受保护环境设置 `DEEPSEEK_API_KEY`、`PLAYGROUND_ACCESS_TOKEN`，可选 `DEEPSEEK_MODEL`（默认 `deepseek-flash`）。程序不自动读取 .env 文件。不要在聊天、源码或 VITE_ 变量中保存密钥；先在供应商控制台配置费用上限。真实请求由用户显式选择 `deepseek` 模式，并通过访问码验证后才调用 `https://api.deepseek.com/chat/completions`。请求采用 OpenAI 兼容 `messages` 输入输出格式，设置 `thinking: {"type": "disabled"}`；实际密钥、endpoint 与模型均属于 DeepSeek。

取消会传播到上游请求并关闭连接，供应商可能已经计费。Node 与 Go 使用请求 context / signal；Python 监测 disconnect 后取消上游任务。生产发布前仍需在真实平台验证断连与费用边界，不能仅凭 mock 测试作生产保证。

后续按路线实现资料上传的大小/类型限制与 SSRF 防护、用户身份与资料归属、数据库迁移、共享预算、流式回答、评测和回滚。保留当前契约测试，增加实际成功与失败证据。仅在独立受控开发环境运行学习者修改，不把它提交给平台 API 主机执行。

官方依据：[FastAPI 错误处理](https://fastapi.tiangolo.com/tutorial/handling-errors/)、[Hono Node](https://hono.dev/docs/getting-started/nodejs)、[Gin](https://gin-gonic.com/en/docs/quickstart/)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。固定资料中的 OpenAI 官方延伸阅读仍是课程参考，运行时不请求这些网页。

## 维护者生成与验证

平台源码位于 [Deep AI Station](https://github.com/frontend-is-magic/deep-ai-station) 的 `starters/`，共享固定资料与契约来自 `starters/shared/`。在平台仓库中，`uv run python scripts/build_starters.py` 同步共享 fixture 并生成三份全栈骨架及一份 Agent 研究助手的可复现 ZIP；`--check` 只比较已提交产物，不修改文件。CI 校验下载包与源码一致，分别运行三个后端测试和共用前端构建，再用 `uv run python scripts/run_starter_e2e.py` 验证 React → FastAPI / Hono / Gin 以及独立 Agent FastAPI 的实际演示链路。该脚本只运行维护者固定代码，以 headless 模式使用自有 8010 / 5174 端口；已有服务占用时拒绝启动，结束后关闭自己的进程组。
