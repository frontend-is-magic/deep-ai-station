# SQLite 数据实验

- 保持 CONTRACT.md 的三语言 CLI 协议；修改行为时同步共同成功/失败案例。
- Python 使用 uv / sqlite3 / Ruff；TypeScript 使用 pnpm / node:sqlite / Prettier；Go 使用 database/sql / modernc SQLite / gofmt。冻结依赖并提交锁文件。
- 只在独立练习环境运行。平台 API 不执行学习者修改；不调用模型或远端服务，不需要密钥。
- 迁移、进度与审计使用明确事务。查询绑定参数并在 SQL 中限制 owner；CLI owner 是教学输入，真实服务必须验证会话。
- 数据库及 -wal / -shm / -journal、敏感配置、私钥、缓存和日志不得提交；仅空值配置模板可提交。
- main 稳定、develop 开发；提交前缀加中文说明，少量多次；不强推 main。
- 用真实临时数据库检查迁移、约束、重启、分页隔离、锁与失败回滚；实际结果写 EVIDENCE.md，不把未执行项记为通过。
- 运行和练习步骤见 README.md，迁移 SQL 在 migrations/。
