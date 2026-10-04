# 受限文本上传实验

- CONTRACT.md、STORAGE.md 和共同案例是三语言行为约定；每个 ZIP 独立可运行，内含同一 React / TypeScript / Tailwind / Jotai 客户端。
- 客户端只处理公开教学身份与有界原字节；controller统一异步状态，身份切换先失效请求。未知POST不自动重传、不由列表匹配推断成功；文件/内容只留内存，不渲染HTML或Markdown。
- uv / FastAPI / Ruff、pnpm / Hono / Prettier、Go / Gin / gofmt；保留冻结锁文件。
- 公开假 Bearer 会话仅限本地教学，入口固定回环地址。真实密钥、Cookie、数据库、日志和缓存不得提交。
- 文件名只作校验后的元数据；对象 ID 由服务端生成。默认内存，SQLite只用固定 .data/uploads.sqlite3 和显式init；不解析 Markdown、不执行内容、不访问用户 URL、不调用模型。
- 仓储在短锁/事务内重检原身份与写权、校验 owner 配额并提交元数据与原字节；读取请求体期间不得持仓储/会话全局锁。
- 用户代码在独立受控环境或隔离沙箱运行。记录自己的成功、失败和未验收项；不冒充生产上传/登录/持久化已完成。
- SQLite忙锁不等待、不重试；COMMIT开始后异常明确result_unconfirmed，清理不覆盖原错误。坏库固定拒绝、不自动修复或重建。
