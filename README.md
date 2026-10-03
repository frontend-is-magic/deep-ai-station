# Deep AI Station

AI Agent 与 AI 全栈工程的学习空间，连接路线、官方信息源和实验工作台。

- 两条路线，各 8 个阶段、24 节课；课时包含原理、实践、验收、测验、官方资料与笔记。
- 96 份逐课参考代码及可下载练习包，包含实践目标、验收条件和证据记录模板。
- Agent 结构化输出与工具契约两课支持[免费工具契约实验](docs/tool-contract.md)，手动提交原始JSON，观察真实只读工具成功/拒绝，并将实际结果追加到本课笔记。
- 本课导师绑定课程目标与验收项，完整实验可追加到本课笔记；历史记录保留课程归属，教学回答不自动完成验收。
- 全栈路线支持 TypeScript / Hono、Go / Gin、Python / FastAPI 参考实现。
- 九节课提供三语言可运行实验：API 契约、SQLite 数据/迁移、会话与授权、受限文本上传、SSE 流式与取消，含冻结依赖、启动入口、可注入依赖和共同成功/失败契约；独立下载无需真实账号或模型凭据。
- 全栈毕业课可下载三套完整项目骨架，共用 React 前端与接口契约，包含对应后端、依赖锁文件、固定资料、测试和验收模板；默认演示不调用模型。
- Agent 毕业课可下载独立研究助手：固定资料检索、批量读取、原生工具循环、实际已读引用校验与三类评测案例；并可跟读独立公开仓库的[云端毕业项目](docs/cloud-capstone.md)，查看源码、开发记录与实际验收证据。
- 官方 RSS / Atom 信息流、明确来源状态、筛选与收藏；8 条精选资料可直达配套课程与指定语言，并查看独立实践状态，收藏快照保留课程入口。
- 课程检索与有界 Agent 循环两种工作流，通过 DeepSeek 的 OpenAI 兼容协议实现流式工具调用、可观察轨迹、停止与历史恢复，失败/停止保留已收到的用量并明确缺失；演示模式明确标为预设流程。
- 免费检索评测工作台比较标题与加权词法检索、top-k 配置，显示正例 Precision / Recall / MRR、负例无结果准确率、逐题排名和漏检，导出带配置与语料版本的 JSON，也可把摘要与个人判断追加到本课笔记；不调用模型。
- 代码实验支持静态检查，以及配置后的 E2B 隔离运行。Python/TS 使用独立 Code Interpreter，Go 使用预装编译器的受信模板；未配置时明确禁用运行。
- 最近 20 组代码编辑按课程与语言保留在页面会话中，支持下载当前代码；刷新会清空会话编辑，学习记录导出不包含代码草稿。
- Jotai 保存每条路线的实际学习位置、进度、笔记和收藏，支持导出/导入；首页与路线页分别恢复上次课时，已完成课提供回顾，下一节未完成课单独推荐。当前不包含账号与跨设备同步。
- 课程完成与语言实践分别记录：全栈按 TS / Go / Python、Agent 按 Python 自行确认成功和失败样例，路线页独立计数；旧备份不自动推断语言实践。
- 毕业课提供按课程与语言分别保存的实践证据卡，记录代码版本、命令、成功/失败结果与未验证事项，可导出 Markdown，也随学习记录备份；内容由学习者填写，不自动证明验收通过。

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

本地通过受保护的 shell 环境传入配置；线上使用 Vercel 托管 Environment Variables。当前程序不会自动读取 `.env`。不要把密钥写进源码、VITE_ 变量、聊天或文档。敏感配置、私钥、认证缓存和日志已加入根目录与毕业包的 `.gitignore`；只有空值 `.env.example` 可提交。提交前脚本同时检查 Git 暂存区、工作区及 ZIP 的敏感路径和常见凭据特征，CI 再执行同一检查，该检查不替代对新凭据类型的审查。

真实调用要求 `PLAYGROUND_ACCESS_TOKEN`、`DEEPSEEK_API_KEY` 与共享配额配置同时就绪。默认使用 PostgreSQL，配置 `AI_QUOTA_DATABASE_URL` 并显式初始化配额表；生产禁止回退到进程内计数。模型请求按固定分钟 / UTC 日累计 10 / 100 次，沙箱创建独立计数 2 / 20 次，同一数据库和 `AI_QUOTA_SCOPE` 跨实例共享。准入后的失败或取消不退款；这限制请求尝试数，不能替代供应商费用上限或全局并发限制。访问码只在页面内存中使用。配置、迁移与故障行为见 [共享请求配额](docs/shared-quota.md)。

Playground 默认使用课程检索和一次回答；可选择“有界 Agent 循环”，让模型选择 `knowledge_search` / `lesson_read` 只读课程工具。每次实验最多 3 次模型请求、2 次工具请求，每轮最多输出 1200 tokens，总时间 45 秒；每轮调用前分别申请共享请求额度。教学演示使用固定工具顺序，不调用模型。完整协议、边界与验证方式见 [Agent 工作流](docs/agent-workflow.md)。

隔离代码运行配置见 [沙箱说明](docs/sandbox.md)，由 `E2B_API_KEY`、访问码与共享配额配置启用；Go 另外需要 `E2B_GO_TEMPLATE`。没有向应用主机、模板或用户沙箱传入模型密钥。

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

路由、输入校验、SQLite 数据/迁移、会话/授权及受限文本上传实验的运行说明见 [课程实验](labs/README.md)。`python3 scripts/build_course_labs.py --check` 比较源码与 ZIP；`python3 scripts/verify_course_labs.py` 在仓库之外解包，冻结安装、运行各语言测试并启动实际 HTTP 服务验证共同案例，结束后确认自有监听已释放。`python3 scripts/verify_sqlite_labs.py` 在独立临时目录验证三语言真实 SQLite CLI、迁移及故障回滚。`python3 scripts/verify_session_labs.py` 验证假会话、权限隔离、CSRF、原始重复头及进程重启；服务端Cookie属性测试不代表真实HTTPS浏览器验收。`python3 scripts/verify_upload_labs.py` 验证真实UTF-8字节上传、owner范围、原子配额和附件下载，重启确认内存数据清空。维护者脚本只运行仓库固定代码。

SSE 实验另附共享 React 客户端，比较三种后端的增量、失败、总 deadline、真实 HTTP 断连与并发隔离。`python3 scripts/verify_stream_labs.py --browser` 从仓库外解包、冻结安装并验证实际浏览器到三后端的流；原生测试观察 Producer 取消和一次清理，页面停止不替代服务端证据。详见[流式实验](labs/sse-stream/shared/README.md)。

完整毕业骨架的启动与校验见 [多语言项目](starters/README.md) 与 [Agent 研究助手](starters/agent/README.md)。三种语言使用共用固定契约；研究助手另有证据存在、证据不足和合成冲突案例。四个项目分别验证 React 到实际后端的演示请求；维护者脚本不接受学习者代码。真实 DeepSeek adapter 仅经过 mock 验证，账号、上传、数据库和生产发布仍是后续实践任务。

安装 `starters/frontend` 与 `starters/typescript` 的冻结依赖后，运行 `uv run python scripts/check_starter_archives.py`；该检查在仓库之外解包四份 ZIP，确认它们使用包内 Prettier 配置，并验证各自前端与 TypeScript 后端格式，防止误继承平台配置。

## 交付约定

`main` 保持验收版本，`develop` 持续开发；提交使用 `feat: 中文说明` 等前缀。Vercel 发布配置位于 [vercel.json](vercel.json)，同域路由静态前端与 Python API。

[GitHub](https://github.com/frontend-is-magic/deep-ai-station) · [架构与边界](docs/architecture.md) · [验证记录](docs/verification.md) · [迭代计划](docs/roadmap.md)
