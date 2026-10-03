# API 契约实验

- 保持 CONTRACT.md 的三语言共同响应；修改业务时同步成功与失败案例。
- Python 使用 uv / FastAPI / Pydantic / Ruff；TypeScript 使用 pnpm / Hono / Prettier；Go 使用 Gin / gofmt。冻结依赖并提交锁文件。
- 只运行当前实验的维护者固定代码；学习者输入不由平台 API 主机执行。此实验没有模型、远程抓取或密钥需求。
- main 稳定、develop 开发；提交前缀加中文说明，少量多次；不强推 main。
- 敏感配置、私钥、认证缓存、日志与数据库不得提交；遵守 .gitignore，仅允许空值配置模板。
- 用共享契约、依赖替换、失败与真实 HTTP 验证行为；实际结果写 EVIDENCE.md，不把未执行项记为通过。
- 路由、运行命令与练习步骤见 README.md；协议见 CONTRACT.md。
