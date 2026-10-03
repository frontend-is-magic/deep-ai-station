# Deep AI Station

AI Agent 与 AI 全栈工程的学习空间，连接路线、官方信息源和实验工作台。

- 两条路线，各 8 个阶段、24 节课；课时包含原理、实践、验收、测验、官方资料与笔记。
- 96 份逐课参考代码及可下载练习包，包含实践目标、验收条件和证据记录模板。
- 本课导师绑定课程目标与验收项，完整实验可追加到本课笔记；历史记录保留课程归属，教学回答不自动完成验收。
- 全栈路线支持 TypeScript / Hono、Go / Gin、Python / FastAPI 参考实现。
- 全栈毕业课可下载三套完整项目骨架，共用 React 前端与接口契约，包含对应后端、依赖锁文件、固定资料、测试和验收模板；默认演示不调用模型。
- Agent 毕业课可下载独立研究助手：固定资料检索、批量读取、原生工具循环、实际已读引用校验与三类评测案例。
- 官方 RSS / Atom 信息流、明确来源状态、筛选与收藏。
- 课程检索与有界 Agent 循环两种工作流，通过 DeepSeek 的 OpenAI 兼容协议实现流式工具调用、可观察轨迹、停止与历史恢复；演示模式明确标为预设流程。
- 代码实验支持静态检查，以及配置后的 E2B 隔离运行。Python/TS 使用独立 Code Interpreter，Go 使用预装编译器的受信模板；未配置时明确禁用运行。
- 最近 20 组代码编辑按课程与语言保留在页面会话中，支持下载当前代码；刷新会清空会话编辑，学习记录导出不包含代码草稿。
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

演示模式无需密钥。平台与四套毕业骨架的真实模式统一使用 DeepSeek：服务端读取 `DEEPSEEK_API_KEY`，`DEEPSEEK_MODEL` 默认 `deepseek-flash`。字段见 [.env.example](.env.example)。

请求使用 OpenAI 兼容的 `messages`、`tool_calls` 与 Chat Completions SSE 输入输出格式，实际 base URL 固定为 `https://api.deepseek.com`，请求发送到 `/chat/completions`。DeepSeek 明确关闭 thinking，使用 `max_tokens` 限制教学回答输出。模型可用性与费用仍以用户控制台为准；当前未做真实服务调用。参考：[DeepSeek 官方配置](https://api-docs.deepseek.com/quick_start/pricing/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。课程和信息流中的 OpenAI 官方资料继续作为学习参考。

本地通过受保护的 shell 环境传入配置；线上使用 Vercel 托管 Environment Variables。当前程序不会自动读取 `.env`。不要把密钥写进源码、VITE_ 变量、聊天或文档。敏感配置、私钥、认证缓存和日志已加入根目录与毕业包的 `.gitignore`；只有空值 `.env.example` 可提交。CI 检查已跟踪文件及 ZIP 的敏感路径和常见凭据特征，该检查不替代对新凭据类型的审查。

`PLAYGROUND_ACCESS_TOKEN` 与 `DEEPSEEK_API_KEY` 同时配置后才启用真实调用。访问码只在页面内存中使用。服务内部限流是单实例保护；公开大规模使用前应配置共享限流与供应商费用上限。

Playground 默认使用课程检索和一次回答；可选择“有界 Agent 循环”，让模型选择 `knowledge_search` / `lesson_read` 只读课程工具。每次实验最多 3 次模型请求、2 次工具请求，每轮最多输出 1200 tokens，总时间 45 秒；费用与限流按实际模型请求累计。教学演示使用固定工具顺序，不调用模型。完整协议、边界与验证方式见 [Agent 工作流](docs/agent-workflow.md)。

隔离代码运行配置见 [沙箱说明](docs/sandbox.md)，由 `E2B_API_KEY` 与访问码启用；Go 另外需要 `E2B_GO_TEMPLATE`。没有向应用主机、模板或用户沙箱传入模型密钥。

## 验证

```sh
pnpm check
uv run ruff check backend api tests scripts
uv run ruff format --check backend api tests scripts
uv run pytest -m 'not e2e'
uv run python scripts/check_secrets.py
uv run playwright install chromium
uv run python scripts/run_e2e.py
uv run python -m scripts.export_examples /tmp/examples.json
node scripts/check_examples.mjs /tmp/examples.json
go run scripts/check_examples.go /tmp/examples.json
uv run python -m scripts.compile_go_examples
```

浏览器验收使用 Codex 内置 Browser。自动化测试只使用 headless Chromium。

课程验证覆盖 48 份 Python AST、24 份 TypeScript 严格类型检查（含锁定的 Hono 依赖）、24 份 Go 编译（含锁定的 Gin module）。Go 检查使用 `go test -c -mod=readonly`，只编译维护者提供的参考，不运行程序或测试；这仍不代替业务行为验收。

完整毕业骨架的启动与校验见 [多语言项目](starters/README.md) 与 [Agent 研究助手](starters/agent/README.md)。三种语言使用共用固定契约；研究助手另有证据存在、证据不足和合成冲突案例。四个项目分别验证 React 到实际后端的演示请求；维护者脚本不接受学习者代码。真实 DeepSeek adapter 仅经过 mock 验证，账号、上传、数据库和生产发布仍是后续实践任务。

## 交付约定

`main` 保持验收版本，`develop` 持续开发；提交使用 `feat: 中文说明` 等前缀。Vercel 发布配置位于 [vercel.json](vercel.json)，同域路由静态前端与 Python API。

[GitHub](https://github.com/frontend-is-magic/deep-ai-station) · [架构与边界](docs/architecture.md) · [验证记录](docs/verification.md) · [迭代计划](docs/roadmap.md)
