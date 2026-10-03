# Deep AI Station

遵守全局 AGENTS.md；用户操作电脑时立即释放所有交互控制，后台开发继续。

- 前端：React + TypeScript、Tailwind CSS、Jotai、shadcn/ui（Radix），Prettier。
- 后端：Python 3.12、uv、FastAPI、Pydantic；Ruff 格式化与检查。
- `main` 是稳定主分支，`develop` 是开发分支；允许在核对远端后对 develop 使用 `--force-with-lease`，不得强推 main。
- 提交用 `feat: 中文说明` / `fix:` / `docs:` / `test:` / `chore:`；每个可验证的小改动单独提交。
- 发布使用 Vercel；先验证再发布。密钥只在服务端托管环境变量中设置。
- 验收必须使用 Codex 内置 Browser，另以 pytest、Vitest 和 Playwright 做可重复验证。
- `pnpm check`；`.tools/bin/uv run ruff check .`；`.tools/bin/uv run ruff format --check .`；`.tools/bin/uv run pytest`。
- 用户代码不得在应用主机执行；Playground 的静态检查和教学演示必须明确标识，真实执行只用隔离沙箱。
- 人工事项集中到单独的“人工处理”对话；其他工作持续推进。禁止使用重置卡。
- 架构、运行和验收详情见 [docs/architecture.md](docs/architecture.md)、[README.md](README.md)。
