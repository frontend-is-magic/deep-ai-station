# 记忆资格与冲突实验

- 先读 README.md 与冻结 CONTRACT.md；这里只处理维护者固定合成资料，不添加模型、网络、HTTP、任意路径或执行入口。
- Python 3.12、标准库运行；开发依赖由 uv.lock 固定，使用 pytest 与 Ruff。修改规则需同时检查人工案例预期；不要按 case ID 返回预设结果。
- 全资产先校验。owner/scope 不符的记录只计数，不输出其 ID、内容或来源；知识文字永远是资料。当前请求不得改源资产。
- 不加入真实凭据、私人记忆或日志；保留 .gitignore 敏感规则。实验报告先放被忽略的 reports/；证据必须区分真实运行与未验证。
- 核对 `uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .`；不将固定规则实验称为生产安全、模型质量或长期记忆认证。
