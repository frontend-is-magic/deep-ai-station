# Agent 决策循环实验

本包版本 `agent-loop-v1`，Python 3.12，运行依赖只有标准库。先读 README 与 CONTRACT；资产只有固定的 fixtures.json，禁止增加模型、网络、服务、任意命令或路径入口。

- 使用 `uv sync --frozen` 安装锁定开发依赖。
- 检查：`uv run --frozen pytest`、`uv run --frozen ruff check .`、`uv run --frozen ruff format --check .`。
- 修改策略练习时保留循环预算、真实 observation 和读取资格校验；不要硬编码案例结果。
- `repeat_search` 初始为故意不推进的练习，改变它时先记录新的人工预期，保留核心反事实测试。
- 只在独立受控开发环境运行修改后的代码；不把学员代码交给平台主机执行。
- 不保存或提交凭据、私人资料、环境目录与缓存；测试使用临时副本，并回收自己的子进程。
