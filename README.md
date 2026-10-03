# Deep AI Station

AI Agent 与 AI 全栈工程的学习空间，连接路线、官方信息源和实验工作台。

- 两条路线，各 8 个阶段、24 节课；课时包含原理、实践、验收、测验、官方资料与笔记。
- 96 份逐课参考代码及可下载练习包，包含实践目标、验收条件和证据记录模板。
- 全栈路线支持 TypeScript / Hono、Go / Gin、Python / FastAPI 参考实现。
- 官方 RSS / Atom 信息流、明确来源状态、筛选与收藏。
- Agent 工作流教学演示、OpenAI / Anthropic / DeepSeek 原生流式适配器、SSE 事件、停止与运行历史。
- 代码实验支持静态检查，以及配置后的 E2B 隔离运行。Python/TS 使用独立 Code Interpreter，Go 使用预装编译器的受信模板；未配置时明确禁用运行。
- Jotai 浏览器进度、笔记和收藏，支持导出/导入；当前不包含账号与跨设备同步。

## 本地运行

需要 Node.js 24、pnpm 10.32.1、Python 3.12 和 uv。完整课程编译检查另外使用 Go 1.27.1。

```sh
pnpm install --frozen-lockfile
uv sync --locked
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
# 另一个终端
pnpm dev
```

本次开发环境的 uv 安装在 `.tools/bin/uv`，也可用这个路径代替全局 uv。若受限环境不能写全局缓存，设置 `UV_CACHE_DIR=/private/tmp/deep-ai-station-uv`。

前端：<http://127.0.0.1:5173>；API 文档：<http://127.0.0.1:8000/api/docs>。Vite 将 `/api` 代理到 FastAPI。

## 模型配置

演示模式无需密钥。真实模式只读取服务端环境变量，字段见 [.env.example](.env.example)。模型名称可通过 `OPENAI_MODEL`、`ANTHROPIC_MODEL`、`DEEPSEEK_MODEL` 调整。

本地通过受保护的 shell 环境传入配置；线上使用 Vercel 托管 Environment Variables。当前程序不会自动读取 `.env`。不要把密钥写进源码、VITE_ 变量、聊天或文档。

`PLAYGROUND_ACCESS_TOKEN` 与供应商密钥同时配置后才启用真实调用。访问码只在页面内存中使用。服务内部限流是单实例保护；公开大规模使用前应配置共享限流与供应商费用上限。

隔离代码运行配置见 [沙箱说明](docs/sandbox.md)，由 `E2B_API_KEY` 与访问码启用；Go 另外需要 `E2B_GO_TEMPLATE`。没有向应用主机、模板或用户沙箱传入模型密钥。

## 验证

```sh
pnpm check
uv run ruff check backend api tests scripts
uv run ruff format --check backend api tests scripts
uv run pytest -m 'not e2e'
uv run playwright install chromium
uv run python scripts/run_e2e.py
uv run python -m scripts.export_examples /tmp/examples.json
node scripts/check_examples.mjs /tmp/examples.json
go run scripts/check_examples.go /tmp/examples.json
uv run python -m scripts.compile_go_examples
```

浏览器验收使用 Codex 内置 Browser。自动化测试只使用 headless Chromium。

课程验证覆盖 48 份 Python AST、24 份 TypeScript 严格类型检查（含锁定的 Hono 依赖）、24 份 Go 编译（含锁定的 Gin module）。Go 检查使用 `go test -c -mod=readonly`，只编译维护者提供的参考，不运行程序或测试；这仍不代替业务行为验收。

## 交付约定

`main` 保持验收版本，`develop` 持续开发；提交使用 `feat: 中文说明` 等前缀。Vercel 发布配置位于 [vercel.json](vercel.json)，同域路由静态前端与 Python API。

[GitHub](https://github.com/frontend-is-magic/deep-ai-station) · [架构与边界](docs/architecture.md) · [验证记录](docs/verification.md) · [迭代计划](docs/roadmap.md)
