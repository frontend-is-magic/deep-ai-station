# 工具审批与幂等实验

- 遵守 CONTRACT.md；Python 3.12 / uv / FastAPI / Ruff / pytest，客户端 React + TypeScript / Tailwind / Jotai / Radix 源码组件 / Prettier，冻结依赖。
- 审批绑定不可变参数、owner、原 requester 和预期版本；读取后、数据库锁后重检会话。不要用客户端 confirmed 或 hash 代替授权。
- 文档、发布回执与 applied 状态必须同事务提交；响应结果不明时保留原 ID 查询，不伪报回滚或自动生成新操作。真实 SQLite / HTTP / 并发失败测试须保留。
- 只用公开教学夹具，绑定回环；没有真实模型或外部发布。用户修改的代码只在独立隔离练习环境运行。
- 数据库、事务旁文件、日志、认证信息和配置留在 .gitignore；EVIDENCE.md 只记录无敏感信息的实际结果。
